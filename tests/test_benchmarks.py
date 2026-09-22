from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmark_tools.run import (ROOT, canonical_digest, check_certificate,
                                 run_scenario, run_suite, verify_lock)
from benchmark_tools.validation import (ValidationError, evaluate_witness,
                                        flight_dates, instant, load_dataset,
                                        minutes, read_json, validate_dataset,
                                        validate_request, zone)


class BenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dataset = load_dataset(ROOT / "data/m1")
        cls.scenario = read_json(ROOT / "benchmarks/scenarios/B01.json")

    def data_copy(self):
        return copy.deepcopy(self.dataset)

    def assert_code(self, code, function, *args):
        with self.assertRaises(ValidationError) as caught:
            function(*args)
        self.assertEqual(caught.exception.code, code)

    def test_all_product_benchmarks(self):
        report = run_suite()
        self.assertEqual(len(report["scenarios"]), 20)
        self.assertFalse(report["optimizer_executed"])
        self.assertTrue(all(x["passed"] for x in report["scenarios"]))

    def test_cli_output_is_byte_reproducible(self):
        command = [sys.executable, "-m", "benchmark_tools.run", "--json"]
        a = subprocess.run(command, cwd=ROOT, capture_output=True, check=True).stdout
        b = subprocess.run(command, cwd=ROOT, capture_output=True, check=True).stdout
        self.assertEqual(a, b)
        self.assertEqual(json.loads(a)["scope"], "prepared_witness_validation_only")

    def test_cli_unknown_scenario_fails(self):
        result = subprocess.run([sys.executable, "-m", "benchmark_tools.run", "--scenario", "NOPE"],
                                cwd=ROOT, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"unknown scenario", result.stderr)

    def test_negative_and_boolean_prices_rejected(self):
        for value in (-1, True, 1.5):
            with self.subTest(value=value):
                data = self.data_copy()
                data.offers["pvg_twu"]["price_minor"] = value
                self.assert_code("invalid_dataset", validate_dataset, data)

    def test_mixed_fare_rejected(self):
        for key, value in (("currency", "USD"), ("fare_profile_id", "checked-bag")):
            data = self.data_copy()
            data.offers["pvg_twu"][key] = value
            self.assert_code("invalid_dataset", validate_dataset, data)

    def test_airport_timezone_mismatch_rejected(self):
        data = self.data_copy()
        offer = data.offers["pvg_sgn"]
        offer["arrival_at"] = offer["segments"][0]["arrival_at"] = "2026-10-03T11:00:00+08:00"
        self.assert_code("invalid_dataset", validate_dataset, data)

    def test_naive_timestamp_rejected(self):
        self.assert_code("invalid_dataset", instant, "2026-10-03T12:00:00")

    def test_nonpositive_flight_rejected(self):
        data = self.data_copy()
        offer = data.offers["pvg_twu"]
        offer["arrival_at"] = offer["segments"][0]["arrival_at"] = offer["departure_at"]
        self.assert_code("invalid_dataset", validate_dataset, data)

    def test_unknown_airport_reference_rejected(self):
        data = self.data_copy()
        data.offers["pvg_twu"]["segments"][0]["destination_airport_id"] = "XXX"
        self.assert_code("invalid_dataset", validate_dataset, data)

    def test_duplicate_segment_id_rejected(self):
        data = self.data_copy()
        data.offers["twu_pvg"]["segments"][0]["id"] = data.offers["pvg_twu"]["segments"][0]["id"]
        self.assert_code("invalid_dataset", validate_dataset, data)

    def test_connection_integrity(self):
        for mutation in ("count", "protection", "chronology", "airport"):
            data = self.data_copy()
            offer = data.offers["long_return"]
            if mutation == "count":
                offer["connection_count"] = 0
            elif mutation == "protection":
                offer["protected_connection"] = False
            elif mutation == "chronology":
                offer["segments"][1]["departure_at"] = "2026-10-14T00:00:00+08:00"
            else:
                offer["segments"][1]["origin_airport_id"] = "KUL"
            with self.subTest(mutation=mutation):
                self.assert_code("invalid_dataset", validate_dataset, data)

    def test_missing_metadata_field_rejected(self):
        data = self.data_copy()
        del data.destinations["SEMPORNA"]["min_usable_minutes"]
        self.assert_code("invalid_dataset", validate_dataset, data)

    def test_invalid_stay_order_rejected(self):
        data = self.data_copy()
        data.destinations["SEMPORNA"]["min_stay_nights"] = 20
        self.assert_code("invalid_dataset", validate_dataset, data)

    def test_missing_tags_and_season_rejected(self):
        for field, key in (("tag_scores", "island"), ("season_scores_by_month", "10")):
            data = self.data_copy()
            del data.destinations["SEMPORNA"][field][key]
            self.assert_code("invalid_dataset", validate_dataset, data)

    def test_request_error_categories(self):
        changes = [("min_trip_days", 6, "invalid_request"),
                   ("flight_budget_minor", True, "invalid_request"),
                   ("allow_self_transfer", True, "unsupported_request"),
                   ("required_destination_ids", ["UNKNOWN"], "insufficient_coverage"),
                   ("window_end_date", "2026-11-01", "insufficient_coverage"),
                   ("preference_weights", {"unknown": 1}, "invalid_request")]
        for key, val, code in changes:
            with self.subTest(key=key):
                request = copy.deepcopy(self.scenario["request"])
                request[key] = val
                self.assert_code(code, validate_request, request, self.dataset)

    def test_duplicate_and_overlapping_request_destinations(self):
        for field, value in (("required_destination_ids", ["SEMPORNA", "SEMPORNA"]),
                             ("preferred_destination_ids", ["SEMPORNA"])):
            request = copy.deepcopy(self.scenario["request"])
            request[field] = value
            self.assert_code("invalid_request", validate_request, request, self.dataset)

    def test_budget_one_minor_unit_over_rejected(self):
        request = copy.deepcopy(self.scenario["request"])
        request["flight_budget_minor"] = 359999
        result = evaluate_witness(request, self.scenario["witnesses"][0], self.dataset, set(self.scenario["offer_ids"]))
        self.assertEqual(result["errors"], ["flight_budget"])

    def test_witness_cannot_use_out_of_scenario_offer(self):
        self.assert_code("invalid_witness", evaluate_witness, self.scenario["request"],
                         self.scenario["witnesses"][1], self.dataset, {"pvg_twu", "twu_pvg"})

    def test_witness_airport_mismatch_rejected(self):
        witness = copy.deepcopy(self.scenario["witnesses"][0])
        witness["access_ids"] = ["BALI_DPS"]
        result = evaluate_witness(self.scenario["request"], witness, self.dataset, set(self.scenario["offer_ids"]))
        self.assertIn("visit_airport", result["errors"])
        self.assertIn("required_missing", result["errors"])

    def test_duplicate_visit_and_bad_chronology_rejected(self):
        witness = copy.deepcopy(self.scenario["witnesses"][1])
        witness["access_ids"] = ["SEMPORNA_TWU", "SEMPORNA_TWU"]
        result = evaluate_witness(self.scenario["request"], witness, self.dataset, set(self.scenario["offer_ids"]))
        self.assertIn("duplicate_destination", result["errors"])
        witness = copy.deepcopy(self.scenario["witnesses"][1])
        witness["offer_ids"] = list(reversed(witness["offer_ids"]))
        result = evaluate_witness(self.scenario["request"], witness, self.dataset, set(self.scenario["offer_ids"]))
        self.assertIn("visit_chronology", result["errors"])

    def test_midnight_half_open_interval(self):
        self.assertEqual(flight_dates(instant("2026-10-14T20:00:00+08:00"),
                                     instant("2026-10-15T00:00:00+08:00"), zone("Asia/Shanghai")), {"2026-10-14"})

    def test_elapsed_time_accounts_for_offset(self):
        self.assertEqual(minutes(instant("2026-10-03T08:00:00+08:00"),
                                 instant("2026-10-03T11:00:00+07:00")), 240)

    def test_preferred_bonus_is_explicit(self):
        request = copy.deepcopy(self.scenario["request"])
        request["preferred_destination_ids"] = ["KL"]
        result = evaluate_witness(request, self.scenario["witnesses"][1], self.dataset, set(self.scenario["offer_ids"]))
        self.assertEqual(result["metrics"]["experience_points"], "110.000000")

    def test_zero_preferences_use_neutral_match(self):
        request = copy.deepcopy(self.scenario["request"])
        request["preference_weights"] = {}
        result = evaluate_witness(request, self.scenario["witnesses"][0], self.dataset, set(self.scenario["offer_ids"]))
        self.assertEqual(result["metrics"]["experience_points"], "50.000000")

    def test_certificate_rejects_false_budget_proof(self):
        scenario = read_json(ROOT / "benchmarks/scenarios/B02.json")
        scenario["request"]["flight_budget_minor"] = 500000
        self.assert_code("benchmark_mismatch", check_certificate, scenario["infeasibility_certificate"],
                         scenario["request"], set(scenario["offer_ids"]), self.dataset)

    def test_certificate_rejects_existing_return(self):
        scenario = read_json(ROOT / "benchmarks/scenarios/B14.json")
        self.assert_code("benchmark_mismatch", check_certificate, scenario["infeasibility_certificate"],
                         scenario["request"], set(scenario["offer_ids"]) | {"twu_pvg"}, self.dataset)

    def test_tampered_expected_value_fails(self):
        scenario = copy.deepcopy(self.scenario)
        scenario["witnesses"][0]["expected"]["metrics"]["flight_total_minor"] = 1
        self.assert_code("benchmark_mismatch", run_scenario, scenario, self.dataset)

    def test_fixture_drift_fails_without_rewriting_lock(self):
        def changed_read(path):
            obj = read_json(path)
            if path.name == "offers.json":
                obj[0]["price_minor"] += 1
            return obj
        with patch("benchmark_tools.run.read_json", side_effect=changed_read):
            self.assert_code("fixture_drift", verify_lock, ROOT)

    def test_json_duplicate_keys_rejected(self):
        with tempfile.TemporaryDirectory(prefix="travel-m1-test-") as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text('{"id": 1, "id": 2}', encoding="utf-8")
            self.assert_code("invalid_dataset", read_json, path)

    def test_canonical_hash_ignores_format_and_key_order(self):
        self.assertEqual(canonical_digest({"a": 1, "b": 2}), canonical_digest(json.loads('{"b":2,"a":1}')))


if __name__ == "__main__":
    unittest.main()
