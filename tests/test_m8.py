import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from benchmark_tools.validation import load_dataset
from evals.m8.compare import compare
from evals.m8.generate_corpus import build_corpus
from evals.m8.frozen import verify_frozen_boundary
from evals.m8.run_real import create_manifest, execute_call, selected_cases
from evals.m8.reporting import write_reports
from evals.m8.evaluation import (CORPUS_FILE, DATA_DIR, classify_downstream_impact,
                                 load_corpus, score_payload, verify_manifest)
from evals.m8.run import run


class M8CorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = load_corpus()
        cls.data = load_dataset(DATA_DIR)

    def test_frozen_manifest_and_size(self):
        self.assertEqual(len(self.corpus["cases"]), 144)
        self.assertEqual(self.corpus["splits"], {"evaluation": 120, "holdout": 24})
        self.assertEqual(len(verify_manifest()), 64)

    def test_corpus_generation_is_byte_reproducible(self):
        rendered = json.dumps(build_corpus(), ensure_ascii=False, indent=2) + "\n"
        self.assertEqual(CORPUS_FILE.read_text(encoding="utf-8"), rendered)

    def test_required_coverage(self):
        languages = Counter(row["language"] for row in self.corpus["cases"])
        categories = {category for row in self.corpus["cases"] for category in row["categories"]}
        self.assertTrue({"zh", "en", "mixed"} <= set(languages))
        required = {"explicit_dates", "vague_date_window", "duration", "approximate_budget",
                    "required_destination", "preferred_destination", "natural_preference_weights",
                    "transfer_tolerance", "missing_information", "conflicting_constraints",
                    "catalog_external_destination", "ambiguous_airport_city",
                    "adversarial_or_malformed"}
        self.assertTrue(required <= categories)
        for row in self.corpus["cases"]:
            for spec in row["expectation"]["fields"].values():
                self.assertIn(spec["severity"], {"minor", "major", "critical"})

    def test_frozen_application_boundary_matches_snapshot(self):
        snapshot = verify_frozen_boundary()
        self.assertEqual(snapshot["boundary_version"], "m8-frozen-boundary-v1")
        self.assertEqual(snapshot["hashes"]["corpus_sha256"], verify_manifest())

    def _valid_payload(self, case):
        return {
            "origin_airport_ids": ["PVG"], "return_airport_ids": ["PVG"],
            "window_start_date": "2027-10-02", "window_end_date": "2027-10-18",
            "min_trip_days": 10, "max_trip_days": 14, "flight_budget_cny": 5000,
            "required_destination_ids": ["SEMPORNA"], "preferred_destination_ids": [],
            "preference_weights": {tag: 0 for tag in self.data.manifest["preference_tag_ids"]},
            "max_optional_destinations": 2, "max_connections_per_offer": 1,
            "summary": "Test request", "assumptions": [], "unresolved_mentions": [],
        }

    def test_field_scoring_and_schema_validation_are_separate(self):
        case = self.corpus["cases"][0]
        result = score_payload(case, self._valid_payload(case), self.data, model="fixture")
        self.assertTrue(result["schema_valid"])
        self.assertEqual(result["error_class"], "none")
        changed = self._valid_payload(case)
        changed["flight_budget_cny"] = 9000
        result = score_payload(case, changed, self.data, model="fixture")
        self.assertTrue(result["schema_valid"])
        self.assertEqual(result["error_class"], "dangerous_semantic_error")

    def test_invalid_value_rejection(self):
        case = next(row for row in self.corpus["cases"] if row["expectation"]["expected_outcome"] == "reject")
        payload = self._valid_payload(case)
        payload["flight_budget_cny"] = -500
        payload["min_trip_days"] = 30
        result = score_payload(case, payload, self.data, model="fixture")
        self.assertFalse(result["schema_valid"])
        self.assertTrue(result["invalid_value_rejected"])


class M8InfrastructureTests(unittest.TestCase):
    def test_downstream_classification(self):
        base = {"status": "ok", "feasible_itineraries": [{"id": "a"}],
                "pareto_itineraries": [{"id": "a"}], "itineraries": [{"id": "a"}]}
        reranked = {**base, "itineraries": [{"id": "b"}]}
        changed = {**base, "feasible_itineraries": [{"id": "b"}]}
        empty = {"status": "no_feasible_in_dataset", "feasible_itineraries": [],
                 "pareto_itineraries": [], "itineraries": []}
        self.assertEqual(classify_downstream_impact(base, base), "no_material_itinerary_effect")
        self.assertEqual(classify_downstream_impact(base, reranked), "changes_ranking_only")
        self.assertEqual(classify_downstream_impact(base, changed), "changes_feasible_itinerary_set")
        self.assertEqual(classify_downstream_impact(base, empty), "causes_no_feasible_solution")
        self.assertEqual(classify_downstream_impact(base, base, material_intent_violation=True),
                         "violates_user_intent_materially")

    def test_no_key_writes_explicit_blocked_result(self):
        with (tempfile.TemporaryDirectory() as temp,
              patch.dict(os.environ, {}, clear=True),
              patch("evals.m8.run.RESULTS_DIR", Path(temp))):
            output = Path(temp) / "blocked.json"
            args = argparse.Namespace(model="candidate-model", repeats=2, case=["M8-001"],
                category=None, limit=None, include_holdout=False, input_cost_per_million=None,
                output_cost_per_million=None, output=str(output))
            result, actual = run(args)
            self.assertEqual(actual, output)
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["blocking_reason"]["code"], "missing_api_key")
            self.assertIsNone(result["metrics"])

    def test_comparison_requires_equivalent_runs_and_never_selects_winner(self):
        template = {"status": "completed", "corpus_version": "v1", "corpus_sha256": "abc",
                    "selection": {"case_ids": ["1"]}, "repeats": 2,
                    "model_reported_versions": ["version"],
                    "metrics": {"schema_valid_response_rate": 1.0}}
        with tempfile.TemporaryDirectory() as temp:
            paths = []
            for index in range(2):
                value = {**template, "model_requested": f"model-{index}"}
                path = Path(temp) / f"{index}.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                paths.append(path)
            report = compare(paths)
            self.assertIsNone(report["decision"])
            self.assertEqual(len(report["models"]), 2)

    def test_real_run_manifest_excludes_holdout_and_key_material(self):
        _, cases = selected_cases()
        manifest = create_manifest(provider="openai", model="candidate", repeats=3, input_rate=.25,
                                   output_rate=2.0, key_configured=True, cases=cases)
        self.assertEqual(manifest["expected_call_count"], 360)
        self.assertFalse(manifest["evaluation_subset"]["holdout_consumed"])
        self.assertEqual(set(manifest["api_configuration"]), {
            "api_key_configured", "api_key_persisted", "store",
            "sdk_automatic_retries", "explicit_max_retries", "tools_supplied",
            "base_url", "api_key_environment_variable"
        })
        self.assertFalse(manifest["api_configuration"]["api_key_persisted"])

    def test_deepseek_manifest_is_explicit_provider_configuration(self):
        _, cases = selected_cases()
        manifest = create_manifest(provider="deepseek", model="deepseek-flash", repeats=3,
                                   input_rate=.30, output_rate=1.20,
                                   key_configured=True, cases=cases)
        self.assertEqual(manifest["provider"], "deepseek")
        self.assertEqual(manifest["api_configuration"]["base_url"],
                         "https://api.deepseek.com")
        self.assertEqual(manifest["api_configuration"]["api_key_environment_variable"],
                         "DEEPSEEK_API_KEY")

    def test_semantic_failure_is_not_retried(self):
        data = load_dataset(DATA_DIR)
        case = load_corpus()["cases"][0]
        payload = {
            "origin_airport_ids": ["PVG"], "return_airport_ids": ["PVG"],
            "window_start_date": "2027-10-02", "window_end_date": "2027-10-18",
            "min_trip_days": 30, "max_trip_days": 14, "flight_budget_cny": 5000,
            "required_destination_ids": ["SEMPORNA"], "preferred_destination_ids": [],
            "preference_weights": {tag: 0 for tag in data.manifest["preference_tag_ids"]},
            "max_optional_destinations": 2, "max_connections_per_offer": 1,
            "summary": "fixture", "assumptions": [], "unresolved_mentions": [],
        }
        class Usage:
            input_tokens = 100
            output_tokens = 50
            total_tokens = 150
        class Response:
            output_text = json.dumps(payload)
            model = "candidate-version"
            id = "response-fixture"
            status = "completed"
            usage = Usage()
        class Responses:
            def __init__(self): self.count = 0
            def create(self, **kwargs): self.count += 1; return Response()
        class Client:
            responses = Responses()
        client = Client()
        record = execute_call(client, case=case, repeat=1, model="candidate",
                              instructions="frozen", schema={}, data=data,
                              input_rate=.25, output_rate=2.0)
        self.assertEqual(client.responses.count, 1)
        self.assertEqual(record["validation_status"], "rejected")
        self.assertEqual(record["retry_count"], 0)

    def test_saved_structured_outputs_regenerate_metrics(self):
        data = load_dataset(DATA_DIR)
        payload = {
            "origin_airport_ids": ["PVG"], "return_airport_ids": ["PVG"],
            "window_start_date": "2027-10-02", "window_end_date": "2027-10-18",
            "min_trip_days": 10, "max_trip_days": 14, "flight_budget_cny": 5000,
            "required_destination_ids": ["SEMPORNA"], "preferred_destination_ids": [],
            "preference_weights": {tag: 0 for tag in data.manifest["preference_tag_ids"]},
            "max_optional_destinations": 2, "max_connections_per_offer": 1,
            "summary": "fixture", "assumptions": [], "unresolved_mentions": [],
        }
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            manifest = {"run_id": "fixture-run", "requested_model": "fixture",
                        "expected_call_count": 3, "repetition_count": 3,
                        "evaluation_subset": {"case_ids": ["M8-001"]}}
            manifest_path = run_dir / "manifest.json"
            manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
            (run_dir / "manifest.sha256").write_text(
                hashlib.sha256(manifest_path.read_bytes()).hexdigest() + "\n", encoding="ascii"
            )
            calls = []
            for repeat in range(1, 4):
                calls.append({"case_id": "M8-001", "repeat": repeat, "status": "completed",
                              "raw_structured_result": payload, "latency_ms": 100 + repeat,
                              "estimated_cost_usd": .001,
                              "usage": {"input_tokens": 100, "output_tokens": 50,
                                        "total_tokens": 150}})
            (run_dir / "calls.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in calls), encoding="utf-8"
            )
            result_path, failure_path = write_reports(run_dir)
            result = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(result["metrics"]["api_calls"]["successful"], 3)
            self.assertEqual(result["metrics"]["schema_valid_response"]["numerator"], 3)
            self.assertTrue(failure_path.exists())


if __name__ == "__main__":
    unittest.main()
