"""HTTP contract tests for the M9 public demo, using no external services."""

import json
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import server
from travel_core import search
from travel_product import service
from travel_product.intent import IntentInputError


class M9WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data, cls.descriptions, cls.scenarios = service.resources()

    def setUp(self):
        self.environment = patch.dict(os.environ, {"AI_ENABLED": "false"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.client = TestClient(server.app)
        self.addCleanup(self.client.close)
        server._ai_calls.clear()

    def fields(self):
        return service.request_fields(self.scenarios[0]["request"])

    def submit(self, fields=None, confirmed=True):
        return self.client.post("/api/optimize", json={
            "fields": self.fields() if fields is None else fields, "confirmed": confirmed,
        })

    def test_health_and_bootstrap_are_public_safe_metadata(self):
        secret = "http-test-secret-never-public"
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": secret, "AI_ENABLED": "true"}):
            health = self.client.get("/api/health")
            bootstrap = self.client.get("/api/bootstrap")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["data_status"], "simulated")
        self.assertEqual(bootstrap.status_code, 200)
        self.assertTrue(bootstrap.json()["ai_available"])
        self.assertEqual(len(bootstrap.json()["scenarios"]), 4)
        self.assertNotIn(secret, bootstrap.text)
        self.assertNotIn("DEEPSEEK_API_KEY", bootstrap.text)
        self.assertEqual(bootstrap.headers["cache-control"], "no-store")
        self.assertEqual(bootstrap.headers["x-content-type-options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", bootstrap.headers["content-security-policy"])

    def test_confirmation_is_exact_boolean_true_before_search(self):
        for value in (False, None, 1, "true", [], {}):
            with self.subTest(confirmed=value), patch("travel_product.service.search") as engine:
                response = self.submit(confirmed=value)
                self.assertEqual(response.status_code, 422)
                engine.assert_not_called()

    def test_missing_confirmation_and_unknown_envelope_fields_are_rejected(self):
        bodies = [
            {"fields": self.fields()},
            {"fields": self.fields(), "confirmed": True, "route": ["PVG", "DPS"]},
            {"confirmed": True},
        ]
        for body in bodies:
            with self.subTest(keys=list(body)), patch("travel_product.service.search") as engine:
                response = self.client.post("/api/optimize", json=body)
                self.assertEqual(response.status_code, 422)
                engine.assert_not_called()

    def test_invalid_json_non_objects_and_duplicate_keys_rejected(self):
        invalid = ["{", "null", "[]", "7", '"text"', '{"text":"first","text":"second"}']
        for route in ("/api/intent", "/api/optimize"):
            for body in invalid:
                with self.subTest(route=route, body=body):
                    response = self.client.post(route, content=body,
                                                headers={"Content-Type": "application/json"})
                    self.assertEqual(response.status_code, 422)
        duplicate_confirmation = json.dumps({"fields": self.fields(), "confirmed": False})[:-1] + ',"confirmed":true}'
        with patch("travel_product.service.search") as engine:
            response = self.client.post("/api/optimize", content=duplicate_confirmation,
                                        headers={"Content-Type": "application/json"})
            self.assertEqual(response.status_code, 422)
            engine.assert_not_called()

    def test_duplicate_nested_fields_cannot_override_budget(self):
        body = json.dumps({"fields": self.fields(), "confirmed": True})
        body = body.replace('"flight_budget_cny":', '"flight_budget_cny": 1, "flight_budget_cny":', 1)
        with patch("travel_product.service.search") as engine:
            response = self.client.post("/api/optimize", content=body,
                                        headers={"Content-Type": "application/json"})
            self.assertEqual(response.status_code, 422)
            engine.assert_not_called()

    def test_missing_unknown_null_or_invalid_request_fields_do_not_search(self):
        variants = []
        missing = self.fields()
        missing.pop("window_start_date")
        variants.append(missing)
        variants.append({**self.fields(), "ignore_constraints": True})
        for key, value in (
            ("origin_airport_ids", None), ("origin_airport_ids", ["SHA"]),
            ("origin_airport_ids", ["PVG", "PVG"]), ("return_airport_ids", []),
            ("window_start_date", "2027-02-30"), ("min_trip_days", 6),
            ("max_trip_days", 22), ("flight_budget_cny", None),
            ("flight_budget_cny", True), ("flight_budget_cny", 50001),
            ("required_destination_ids", ["ATLANTIS"]), ("allow_self_transfer", True),
            ("max_optional_destinations", 5), ("max_connections_per_offer", -1),
            ("max_connections_per_offer", 3),
            ("preference_weights", {"unlisted-tag": 99}),
        ):
            variants.append({**self.fields(), key: value})
        for fields in variants:
            with self.subTest(fields=fields), patch("travel_product.service.search") as engine:
                response = self.submit(fields)
                self.assertEqual(response.status_code, 422)
                engine.assert_not_called()

    def test_overlap_and_reversed_ranges_rejected_without_relaxation(self):
        variants = [
            {"required_destination_ids": ["SEMPORNA"], "preferred_destination_ids": ["SEMPORNA"]},
            {"window_start_date": "2027-10-20", "window_end_date": "2027-10-02"},
            {"min_trip_days": 18, "max_trip_days": 10},
        ]
        for changed in variants:
            with self.subTest(changed=changed), patch("travel_product.service.search") as engine:
                response = self.submit({**self.fields(), **changed})
                self.assertEqual(response.status_code, 422)
                engine.assert_not_called()

    def test_all_four_web_scenarios_preserve_m6_optimizer_results(self):
        for scenario in self.scenarios:
            with self.subTest(scenario=scenario["id"]):
                expected = search(scenario["request"], self.data, scenario["offer_ids"])
                response = self.submit(service.request_fields(scenario["request"]))
                self.assertEqual(response.status_code, 200, response.text)
                actual = response.json()
                self.assertEqual(actual["status"], expected["status"])
                self.assertTrue(actual["search_complete"])
                self.assertEqual(actual["summary"], expected["run_summary"])
                self.assertEqual(actual["data_fingerprint"], expected["data_fingerprint"])
                self.assertEqual([card["itinerary_id"] for card in actual["cards"]],
                                 [item["id"] for item in expected["itineraries"]])
                self.assertEqual([card["flight_cost_minor"] for card in actual["cards"]],
                                 [item["flight_total_minor"] for item in expected["itineraries"]])
                self.assertTrue(actual["comparison"])
                for key, value in scenario["request"].items():
                    if key != "request_id":
                        self.assertEqual(actual["request"][key], value)
                self.assertTrue(all(card["why"] and card["tradeoffs"] and card["legs"]
                                    for card in actual["cards"]))

    def test_identical_confirmed_request_produces_identical_http_result(self):
        first = self.submit()
        second = self.submit()
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json(), second.json())

    def test_infeasible_budget_retained_in_empty_response(self):
        fields = self.fields()
        fields["flight_budget_cny"] = 1
        response = self.submit(fields)
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["status"], "no_feasible_in_dataset")
        self.assertEqual(result["request"]["flight_budget_minor"], 100)
        self.assertEqual(result["cards"], [])
        self.assertEqual(result["comparison"], [])

    def test_oversized_regular_and_chunked_bodies_rejected_before_work(self):
        for body in (b"x" * 16385, (b"x" * 9000 for _ in range(2))):
            with self.subTest(body_type=type(body).__name__), patch("travel_product.service.search") as engine:
                response = self.client.post("/api/optimize", content=body)
                self.assertEqual(response.status_code, 413)
                engine.assert_not_called()

    def test_intent_input_limits_and_unknown_fields_precede_provider(self):
        for body in ({"text": "short"}, {"text": "x" * 2001}, {"text": None},
                     {"text": "请帮我安排一段海岛潜水旅行", "api_key": "must-not-be-accepted"}):
            with self.subTest(body=body), patch("server.extract_intent") as extractor:
                response = self.client.post("/api/intent", json=body)
                self.assertEqual(response.status_code, 422)
                extractor.assert_not_called()

    def test_ai_disabled_is_safe_and_manual_optimization_still_works(self):
        with patch("server.extract_intent") as extractor:
            response = self.client.post("/api/intent", json={"text": "请帮我安排一段海岛潜水旅行"})
            self.assertEqual(response.status_code, 503)
            extractor.assert_not_called()
        self.assertEqual(self.submit({**self.fields(), "flight_budget_cny": 1}).status_code, 200)

    def test_ai_draft_is_returned_without_automatic_optimization(self):
        secret = "http-test-key"
        draft = {"contract_version": "m9-intent-v2", "fields": {"flight_budget_cny": None},
                 "summary": "海岛旅行", "assumptions": [], "unresolved_mentions": [],
                 "missing_fields": ["flight_budget_cny"], "model": "deepseek-flash"}
        with patch.dict(os.environ, {"AI_ENABLED": "true", "DEEPSEEK_API_KEY": secret}), \
                patch("server.extract_intent", return_value=draft) as extractor, \
                patch("travel_product.service.search") as engine:
            response = self.client.post("/api/intent", json={"text": "请帮我安排一段海岛潜水旅行"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), draft)
        self.assertNotIn(secret, response.text)
        extractor.assert_called_once()
        engine.assert_not_called()

    def test_ai_safe_error_and_unexpected_exception_never_expose_details(self):
        private = "server-private-sentinel"
        for error, expected_status in (
            (IntentInputError("invalid_ai_output", "请核对旅行条件。"), 502),
            (RuntimeError(private), 503),
        ):
            with self.subTest(error=type(error).__name__), \
                    patch.dict(os.environ, {"AI_ENABLED": "true", "DEEPSEEK_API_KEY": private}), \
                    patch("server.extract_intent", side_effect=error):
                response = self.client.post("/api/intent", json={"text": "请帮我安排一段海岛潜水旅行"})
            self.assertEqual(response.status_code, expected_status)
            self.assertNotIn(private, response.text)

    def test_ai_throttle_caps_model_calls_and_expires(self):
        with patch.dict(os.environ, {"AI_ENABLED": "true", "DEEPSEEK_API_KEY": "http-test-key"}), \
                patch("server.extract_intent", return_value={"contract_version": "fake"}) as extractor, \
                patch("server.time.monotonic", return_value=100.0):
            for _ in range(12):
                response = self.client.post("/api/intent", json={"text": "请帮我安排一段海岛潜水旅行"})
                self.assertEqual(response.status_code, 200)
            blocked = self.client.post("/api/intent", json={"text": "请帮我安排一段海岛潜水旅行"})
            self.assertEqual(blocked.status_code, 429)
            self.assertEqual(extractor.call_count, 12)
            with patch("server.time.monotonic", return_value=161.0):
                allowed = self.client.post("/api/intent", json={"text": "请帮我安排一段海岛潜水旅行"})
                self.assertEqual(allowed.status_code, 200)
                self.assertEqual(extractor.call_count, 13)

    def test_internal_value_error_does_not_escape_safe_ai_boundary(self):
        private = "internal-value-error-sensitive-details"
        with patch.dict(os.environ, {"AI_ENABLED": "true", "DEEPSEEK_API_KEY": "http-test-key"}), \
                patch("server.extract_intent", side_effect=ValueError(private)):
            response = self.client.post("/api/intent", json={"text": "请帮我安排一段海岛潜水旅行"})
        self.assertIn(response.status_code, (422, 503))
        self.assertNotIn(private, response.text)

    def test_unexpected_optimizer_failure_has_safe_message(self):
        with patch("travel_product.service.search", side_effect=RuntimeError("private-internal-path-and-key")):
            response = self.submit()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private-internal-path-and-key", response.text)

    def test_private_files_and_api_documentation_are_not_served(self):
        for route in ("/.env", "/requirements.txt", "/server.py", "/docs", "/openapi.json", "/assets/../server.py"):
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
