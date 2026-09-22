"""The M9 draft boundary is exercised with fakes, never paid model calls."""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from benchmark_tools.validation import load_dataset
from travel_product.intent import (BASE_URL, CONTRACT_VERSION, ESSENTIAL_FIELDS,
                                   MAX_OUTPUT_TOKENS, TIMEOUT_SECONDS,
                                   IntentInputError, extract_intent, intent_prompt,
                                   intent_schema, validate_intent_draft)
from travel_ui.scenario_library import load_destination_descriptions


ROOT = Path(__file__).resolve().parents[1]


class M9IntentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_dataset(ROOT / "data/m5")
        cls.descriptions = load_destination_descriptions(ROOT / "data/m6/destination_descriptions.json")

    def payload(self):
        return {
            "fields": {
                "origin_airport_ids": ["PVG"], "return_airport_ids": ["HGH"],
                "window_start_date": "2027-10-02", "window_end_date": "2027-10-18",
                "min_trip_days": 10, "max_trip_days": 14, "flight_budget_cny": 5000,
                "required_destination_ids": ["SEMPORNA"], "preferred_destination_ids": [],
                "preference_weights": {tag: 0 for tag in self.data.manifest["preference_tag_ids"]},
                "max_optional_destinations": 2, "max_connections_per_offer": 1,
                "allow_self_transfer": False,
            },
            "summary": "计划探索海岛，必须到仙本那。", "assumptions": [], "unresolved_mentions": [],
        }

    def fake(self, payload=None, *, output=None, status="completed"):
        if output is None:
            output = json.dumps(payload or self.payload(), ensure_ascii=False)
        create = Mock(return_value=SimpleNamespace(output_text=output, status=status))
        return SimpleNamespace(responses=SimpleNamespace(create=create))

    def validate(self, payload):
        return validate_intent_draft(payload, self.data, model="deepseek-flash")

    def test_complete_draft_keeps_original_fields_and_version(self):
        payload = self.payload()
        before = deepcopy(payload)
        result = self.validate(payload)
        self.assertEqual(result["contract_version"], CONTRACT_VERSION)
        self.assertEqual(result["fields"], payload["fields"])
        self.assertEqual(result["missing_fields"], [])
        self.assertEqual(payload, before)

    def test_unknown_essentials_stay_null_and_are_locally_reported(self):
        payload = self.payload()
        for field in ESSENTIAL_FIELDS:
            payload["fields"][field] = None
        payload["unresolved_mentions"] = ["10月未说明年份。", "预算未明确为机票预算。"]
        result = self.validate(payload)
        self.assertEqual(result["missing_fields"], list(ESSENTIAL_FIELDS))
        self.assertTrue(all(result["fields"][key] is None for key in ESSENTIAL_FIELDS))
        self.assertEqual(result["unresolved_mentions"], payload["unresolved_mentions"])

    def test_optional_defaults_are_explicit_assumptions(self):
        payload = self.payload()
        for name in ("max_optional_destinations", "max_connections_per_offer",
                     "allow_self_transfer", "preference_weights"):
            payload["fields"][name] = None
        result = self.validate(payload)
        self.assertEqual(result["fields"]["max_optional_destinations"], 2)
        self.assertEqual(result["fields"]["max_connections_per_offer"], 1)
        self.assertIs(result["fields"]["allow_self_transfer"], False)
        self.assertEqual(len(result["assumptions"]), 5)
        self.assertIn("模拟报价", result["assumptions"][-1])

    def test_unknown_ids_are_rejected_not_silently_dropped(self):
        for field, value in (("origin_airport_ids", ["SHA"]),
                             ("required_destination_ids", ["ATLANTIS"]),
                             ("preferred_destination_ids", ["ATLANTIS"])):
            with self.subTest(field=field):
                payload = self.payload()
                payload["fields"][field] = value
                with self.assertRaises(IntentInputError) as caught:
                    self.validate(payload)
                self.assertEqual(caught.exception.code, "unsupported_location")

    def test_duplicate_ids_overlap_and_unknown_fields_rejected(self):
        modifications = [
            {"origin_airport_ids": ["PVG", "PVG"]},
            {"preferred_destination_ids": ["SEMPORNA"]},
            {"made_up_field": 1},
            {"return_airport_ids": []},
        ]
        for changes in modifications:
            with self.subTest(changes=changes):
                payload = self.payload()
                payload["fields"].update(changes)
                with self.assertRaises(IntentInputError):
                    self.validate(payload)

    def test_numeric_types_ranges_and_conflicts_rejected(self):
        invalid = [
            ("flight_budget_cny", True), ("flight_budget_cny", 0),
            ("min_trip_days", 6), ("max_trip_days", 22),
            ("max_trip_days", 9), ("flight_budget_cny", "5000"),
            ("max_connections_per_offer", 3), ("max_optional_destinations", 5),
            ("allow_self_transfer", 0),
            ("window_start_date", "2027-02-30"),
            ("window_start_date", "2027-11-01"),
            ("window_start_date", "20271002"),
        ]
        for name, value in invalid:
            with self.subTest(name=name, value=value):
                payload = self.payload()
                payload["fields"][name] = value
                with self.assertRaises(IntentInputError):
                    self.validate(payload)

    def test_external_dates_and_self_transfer_are_preserved_with_warnings(self):
        payload = self.payload()
        payload["fields"]["window_start_date"] = "2028-10-01"
        payload["fields"]["window_end_date"] = "2028-10-18"
        payload["fields"]["allow_self_transfer"] = True
        result = self.validate(payload)
        self.assertEqual(result["fields"]["window_start_date"], "2028-10-01")
        self.assertIs(result["fields"]["allow_self_transfer"], True)
        self.assertEqual(len(result["unresolved_mentions"]), 2)

    def test_weights_cannot_add_unknown_tags_or_null_values(self):
        for weights in ({"made-up": 50}, {**self.payload()["fields"]["preference_weights"], "food": None},
                        {**self.payload()["fields"]["preference_weights"], "food": 101}):
            with self.subTest(weights=weights):
                payload = self.payload()
                payload["fields"]["preference_weights"] = weights
                with self.assertRaises(IntentInputError):
                    self.validate(payload)

    def test_request_uses_strict_nullable_schema_and_no_flight_information(self):
        client = self.fake()
        result = extract_intent("从浦东出发的海岛旅行", self.data, self.descriptions, client=client)
        call = client.responses.create.call_args.kwargs
        self.assertEqual(call["text"]["format"]["schema"], intent_schema(self.data))
        self.assertTrue(call["text"]["format"]["strict"])
        self.assertEqual(call["max_output_tokens"], MAX_OUTPUT_TOKENS)
        self.assertEqual(call["timeout"], TIMEOUT_SECONDS)
        self.assertEqual(call["reasoning"], {"effort": "none"})
        self.assertFalse(call["store"])
        self.assertNotIn("tools", call)
        prompt = call["instructions"]
        for forbidden in ("price_minor", "offer_id", "itinerary_id", "departure_at"):
            self.assertNotIn(forbidden, prompt)
        self.assertIn("缺少任何年份", prompt)
        self.assertEqual(result["fields"]["required_destination_ids"], ["SEMPORNA"])

    def test_server_environment_client_is_bounded_closed_and_key_not_returned(self):
        client = self.fake()
        client.close = Mock()
        sentinel = "unit-test-secret-never-render"
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": sentinel, "DEEPSEEK_MODEL": "deepseek-flash"}), \
                patch("openai.OpenAI", return_value=client) as factory:
            result = extract_intent("想去潜水", self.data, self.descriptions)
        self.assertEqual(factory.call_args.kwargs["api_key"], sentinel)
        self.assertEqual(factory.call_args.kwargs["base_url"], BASE_URL)
        self.assertEqual(factory.call_args.kwargs["max_retries"], 0)
        client.close.assert_called_once()
        self.assertNotIn(sentinel, json.dumps(result))
        self.assertNotIn(sentinel, json.dumps(client.responses.create.call_args.kwargs))

    def test_provider_failure_has_only_safe_public_message(self):
        client = self.fake()
        client.responses.create.side_effect = RuntimeError("provider body includes unit-test-secret")
        with self.assertRaises(IntentInputError) as caught:
            extract_intent("想去潜水", self.data, self.descriptions, client=client)
        self.assertEqual(caught.exception.code, "ai_service_error")
        self.assertNotIn("unit-test-secret", caught.exception.message)
        self.assertTrue(caught.exception.__suppress_context__)

    def test_missing_configuration_and_bad_input_do_not_call_model(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(IntentInputError) as caught:
                extract_intent("想去潜水", self.data, self.descriptions)
        self.assertEqual(caught.exception.code, "ai_not_configured")
        for text in (None, " ", "x" * 2001, "a\x00b"):
            client = self.fake()
            with self.assertRaises(IntentInputError):
                extract_intent(text, self.data, self.descriptions, client=client)
            client.responses.create.assert_not_called()

    def test_malformed_duplicate_truncated_and_secret_outputs_fail_closed(self):
        malformed = ['{"fields":{},"fields":{}}', "not json", "{\"fields\":NaN}",
                     "unit-test-secret", "x" * 18001]
        for output in malformed:
            with self.subTest(output=output[:40]):
                with self.assertRaises(IntentInputError):
                    extract_intent("想去潜水", self.data, self.descriptions,
                                   api_key="unit-test-secret", client=self.fake(output=output))
        with self.assertRaises(IntentInputError):
            extract_intent("想去潜水", self.data, self.descriptions,
                           client=self.fake(status="incomplete"))

    def test_schema_essentials_are_nullable_and_provider_cannot_claim_completeness(self):
        schema = intent_schema(self.data)
        for name in ESSENTIAL_FIELDS:
            field = schema["properties"]["fields"]["properties"][name]
            types = [option["type"] for option in field["anyOf"]] if "anyOf" in field else field["type"]
            self.assertIn("null", types)
        self.assertNotIn("missing_fields", schema["properties"])
        self.assertFalse(schema["additionalProperties"])
        self.assertFalse(schema["properties"]["fields"]["additionalProperties"])


if __name__ == "__main__":
    unittest.main()
