"""M3 adapters and reproducible demo cases."""

from datetime import date
import json
from pathlib import Path
import unittest

from streamlit.testing.v1 import AppTest

from benchmark_tools.run import ROOT
from benchmark_tools.validation import load_dataset, validate_request
from travel_core import search
from travel_ui.demo_cases import case_by_id, load_demo_cases
from travel_ui.natural_language import (NaturalLanguageInputError,
                                        OpenAITravelRequestExtractor,
                                        extraction_schema, validate_extraction)
from travel_ui.pilot import (build_pilot_record, build_product_telemetry,
                             build_subjective_feedback, create_session_id,
                             pilot_record_json)
from travel_ui.presentation import build_itinerary_cards, comparison_rows
from travel_ui.scenario_library import (load_destination_descriptions,
                                        load_m6_scenarios)
from travel_ui.view_models import (build_request, itinerary_card,
                                   money_to_minor, result_message)


class UiAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_dataset(ROOT / "data/m1")
        cls.cases = load_demo_cases(ROOT / "demos")

    def run_case(self, identifier):
        case = case_by_id(self.cases, identifier)
        validate_request(case["request"], self.data)
        return search(case["request"], self.data, case["offer_ids"])

    def test_four_required_demo_cases_exist(self):
        self.assertEqual(
            {case["id"] for case in self.cases},
            {"required-destination", "extra-destination-cheaper",
             "budget-changes-recommendation", "no-feasible-itinerary"},
        )

    def test_required_destination_case(self):
        result = self.run_case("required-destination")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["feasible_itineraries"]), 1)
        self.assertEqual([v["destination_id"] for v in result["itineraries"][0]["visits"]], ["SEMPORNA"])

    def test_extra_destination_reduces_airfare_case(self):
        result = self.run_case("extra-destination-cheaper")
        self.assertEqual(result["status"], "ok")
        routes = {tuple(v["destination_id"] for v in item["visits"]): item for item in result["pareto_itineraries"]}
        self.assertEqual(routes[("SEMPORNA",)]["flight_total_minor"], 360000)
        self.assertEqual(routes[("SEMPORNA", "KL")]["flight_total_minor"], 280000)
        self.assertGreater(routes[("SEMPORNA", "KL")]["transit_minutes"], routes[("SEMPORNA",)]["transit_minutes"])

    def test_budget_changes_recommendation_case(self):
        result = self.run_case("budget-changes-recommendation")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["feasible_itineraries"]), 1)
        self.assertEqual([v["destination_id"] for v in result["itineraries"][0]["visits"]], ["SEMPORNA", "KL"])
        self.assertEqual(result["itineraries"][0]["flight_total_minor"], 280000)

    def test_no_feasible_case(self):
        result = self.run_case("no-feasible-itinerary")
        self.assertEqual(result["status"], "no_feasible_in_dataset")
        self.assertEqual(result["itineraries"], [])
        level, message = result_message(result)
        self.assertEqual(level, "warning")
        self.assertIn("没有可行行程", message)

    def test_form_values_map_to_existing_request_schema(self):
        request = build_request(
            request_id="ui-test", origins=["PVG"], returns=["PVG", "HGH"],
            date_window=(date(2026, 10, 1), date(2026, 10, 31)),
            duration=(12, 18), budget_cny=3000,
            required_destinations=["SEMPORNA"],
            preference_weights={"food": 40, "island": 100, "diving": 80},
            max_optional_destinations=4, max_connections_per_offer=2,
            fare_profile_id=self.data.manifest["fare_profile_id"],
        )
        validate_request(request, self.data)
        self.assertEqual(request["flight_budget_minor"], 300000)
        self.assertEqual(list(request["preference_weights"]), ["diving", "food", "island"])
        self.assertFalse(request["allow_self_transfer"])

    def test_money_conversion_uses_decimal_rounding(self):
        self.assertEqual(money_to_minor("2800.005"), 280001)
        self.assertEqual(money_to_minor(0.1), 10)

    def test_card_contains_required_product_content(self):
        result = self.run_case("extra-destination-cheaper")
        cards = [itinerary_card(item, self.data, rank)
                 for rank, item in enumerate(result["itineraries"], 1)]
        self.assertEqual(len(cards), 2)
        for card in cards:
            self.assertTrue(card["route"])
            self.assertTrue(card["destinations"])
            self.assertRegex(card["flight_cost"], r"^¥")
            self.assertTrue(card["burden"])
            self.assertTrue(card["experience"])
            self.assertTrue(card["overall"])
            self.assertTrue(card["why"])
            self.assertTrue(card["tradeoffs"])
            self.assertTrue(card["legs"])
            self.assertTrue(card["itinerary_id"])
        self.assertIn("仙本那", cards[0]["route"] + cards[1]["route"])
        self.assertTrue(any("吉隆坡" in card["route"] for card in cards))

    def test_case_loader_returns_copy(self):
        first = case_by_id(self.cases, "required-destination")
        first["request"]["flight_budget_minor"] = 1
        second = case_by_id(self.cases, "required-destination")
        self.assertEqual(second["request"]["flight_budget_minor"], 500000)

    def test_same_demo_produces_same_result(self):
        first = self.run_case("extra-destination-cheaper")
        second = self.run_case("extra-destination-cheaper")
        self.assertEqual(first, second)

    def test_pilot_record_separates_telemetry_and_feedback(self):
        result = self.run_case("extra-destination-cheaper")
        cards = [itinerary_card(item, self.data, rank)
                 for rank, item in enumerate(result["itineraries"], 1)]
        telemetry = build_product_telemetry(
            session_id="pilot-test", scenario_id="extra-destination-cheaper",
            result=result, cards=cards, completion_seconds=42.4,
        )
        feedback = build_subjective_feedback(
            selected_itinerary_id=cards[0]["itinerary_id"],
            comprehension_rating=4, trust_rating=3, usefulness_rating=5,
            discovered_non_obvious_route="yes",
            would_consider_selected_route="unsure",
            unclear_point="  交通负担  ", missing_information="真实价格",
        )
        record = build_pilot_record(telemetry, feedback)
        self.assertEqual(set(record), {"record_version", "product_telemetry", "subjective_feedback"})
        self.assertTrue(telemetry["feasible_result_produced"])
        self.assertEqual(telemetry["recommended_alternative_count"], 2)
        self.assertEqual(telemetry["task_completion_seconds"], 42)
        self.assertEqual(feedback["unclear_point"], "交通负担")
        self.assertNotIn("name", pilot_record_json(record).lower())
        self.assertEqual(json.loads(pilot_record_json(record)), record)

    def test_no_result_telemetry_has_no_alternatives(self):
        result = self.run_case("no-feasible-itinerary")
        telemetry = build_product_telemetry(
            session_id="pilot-test", scenario_id="no-feasible-itinerary",
            result=result, cards=[], completion_seconds=-1,
        )
        self.assertFalse(telemetry["feasible_result_produced"])
        self.assertEqual(telemetry["recommended_alternatives_shown"], [])
        self.assertEqual(telemetry["task_completion_seconds"], 0)

    def test_feedback_validation_rejects_bad_values(self):
        with self.assertRaises(ValueError):
            build_subjective_feedback(
                selected_itinerary_id=None, comprehension_rating=0,
                trust_rating=3, usefulness_rating=3,
                discovered_non_obvious_route="yes",
                would_consider_selected_route="no",
                unclear_point="", missing_information="",
            )

    def test_session_identifier_is_anonymous_shape(self):
        identifier = create_session_id()
        self.assertRegex(identifier, r"^pilot-[0-9a-f]{12}$")


class M6DecisionPresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_dataset(ROOT / "data/m5")
        cls.scenarios = load_m6_scenarios(ROOT / "scenarios/m6", ROOT)
        cls.descriptions = load_destination_descriptions(
            ROOT / "data/m6/destination_descriptions.json"
        )

    def run_scenario(self, identifier):
        scenario = next(item for item in self.scenarios if item["id"] == identifier)
        validate_request(scenario["request"], self.data)
        return scenario, search(scenario["request"], self.data, scenario["offer_ids"])

    def test_four_realistic_scenarios_define_intent_without_answers(self):
        self.assertEqual(
            {item["id"] for item in self.scenarios},
            {"m6-graduation-trip", "m6-island-diving-trip",
             "m6-long-holiday-exploration", "m6-premium-relaxation-trip"},
        )
        for scenario in self.scenarios:
            self.assertTrue(scenario["traveler_profile"])
            self.assertTrue(scenario["expected_user_intent"])
            self.assertTrue(scenario["must_have_constraints"])
            self.assertNotIn("expected_itineraries", scenario)
            self.assertNotIn("expected_optimizer_answer", scenario)
            self.assertEqual(len(scenario["offer_ids"]), 264)

    def test_all_scenarios_are_feasible_and_deterministic(self):
        for scenario in self.scenarios:
            with self.subTest(scenario=scenario["id"]):
                first = search(scenario["request"], self.data, scenario["offer_ids"])
                second = search(scenario["request"], self.data, reversed(scenario["offer_ids"]))
                self.assertEqual(first, second)
                self.assertEqual(first["status"], "ok")
                self.assertGreaterEqual(len(first["itineraries"]), 2)

    def test_explanations_cover_price_burden_match_and_tradeoff(self):
        scenario, result = self.run_scenario("m6-island-diving-trip")
        cards = build_itinerary_cards(
            result["itineraries"], self.data, scenario["request"], self.descriptions
        )
        text = " ".join(line for card in cards for line in card["why"] + card["tradeoffs"])
        self.assertIn("模拟报价", text)
        self.assertIn("交通", text)
        self.assertIn("潜水", text)
        self.assertIn("相对", text)

    def test_destination_metadata_is_separate_from_optimization_data(self):
        scenario, result = self.run_scenario("m6-premium-relaxation-trip")
        cards = build_itinerary_cards(
            result["itineraries"], self.data, scenario["request"], self.descriptions
        )
        visit = cards[0]["destinations"][0]
        self.assertEqual(set(visit), {
            "destination_id", "destination", "stay_nights", "arrival_at", "departure_at",
            "experience_points", "optimization_data", "descriptive_information",
        })
        self.assertIn("recommended_stay_nights", visit["optimization_data"])
        self.assertIn("suitable_activities", visit["descriptive_information"])
        changed = {key: dict(value) for key, value in self.descriptions.items()}
        changed[visit["destination_id"]]["seasonal_note"] = "changed display text"
        self.assertEqual(
            result,
            search(scenario["request"], self.data, scenario["offer_ids"]),
        )

    def test_comparison_table_has_decision_differences(self):
        scenario, result = self.run_scenario("m6-graduation-trip")
        cards = build_itinerary_cards(
            result["itineraries"], self.data, scenario["request"], self.descriptions
        )
        rows = comparison_rows(cards, self.descriptions)
        self.assertEqual(len(rows), len(cards))
        self.assertEqual(set(rows[0]), {
            "方案", "路线", "模拟机票", "较最低价", "交通时间", "较最少交通",
            "新增目的地", "飞行日", "中转次数", "体验收益", "主要取舍",
        })
        self.assertTrue(any(row["较最低价"] != "相同 元" for row in rows))
        self.assertTrue(any(row["新增目的地"] != "—" for row in rows))


class M7NaturalLanguageInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_dataset(ROOT / "data/m5")
        cls.descriptions = load_destination_descriptions(
            ROOT / "data/m6/destination_descriptions.json"
        )
        cls.scenario = next(
            item for item in load_m6_scenarios(ROOT / "scenarios/m6", ROOT)
            if item["id"] == "m6-island-diving-trip"
        )

    def valid_payload(self):
        return {
            "origin_airport_ids": ["PVG"], "return_airport_ids": ["PVG", "HGH"],
            "window_start_date": "2027-10-01", "window_end_date": "2027-10-22",
            "min_trip_days": 10, "max_trip_days": 14, "flight_budget_cny": 8000,
            "required_destination_ids": [], "preferred_destination_ids": ["SEMPORNA"],
            "preference_weights": {
                "island": 100, "diving": 100, "food": 20, "culture": 0,
                "nature": 40, "city": 0, "relaxation": 60,
            },
            "max_optional_destinations": 3, "max_connections_per_offer": 1,
            "summary": "上海出发的十月至十四日潜水海岛探索",
            "assumptions": ["十月按 2027 年模拟数据窗口解释"],
            "unresolved_mentions": [],
        }

    def test_structured_output_becomes_existing_trip_request(self):
        extracted = validate_extraction(
            self.valid_payload(), self.data, request_id="ai-test", model="test-model"
        )
        validate_request(extracted.request, self.data)
        self.assertEqual(extracted.request["flight_budget_minor"], 800000)
        self.assertEqual(extracted.request["preferred_destination_ids"], ["SEMPORNA"])
        self.assertFalse(extracted.request["allow_self_transfer"])
        self.assertNotIn("summary", extracted.request)

    def test_schema_allowlists_catalog_and_all_preference_tags(self):
        schema = extraction_schema(self.data)
        destination_enum = schema["properties"]["preferred_destination_ids"]["items"]["enum"]
        self.assertEqual(set(destination_enum), set(self.data.destinations))
        self.assertEqual(
            set(schema["properties"]["preference_weights"]["required"]),
            set(self.data.manifest["preference_tag_ids"]),
        )
        self.assertFalse(schema["additionalProperties"])

    def test_invalid_or_invented_values_are_rejected(self):
        cases = []
        unknown = self.valid_payload()
        unknown["preferred_destination_ids"] = ["ATLANTIS"]
        cases.append(unknown)
        overlap = self.valid_payload()
        overlap["required_destination_ids"] = ["SEMPORNA"]
        cases.append(overlap)
        invalid_budget = self.valid_payload()
        invalid_budget["flight_budget_cny"] = True
        cases.append(invalid_budget)
        missing = self.valid_payload()
        missing.pop("summary")
        cases.append(missing)
        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(NaturalLanguageInputError):
                    validate_extraction(payload, self.data, request_id="bad", model="test")

    def test_openai_adapter_requests_strict_json_without_flight_data(self):
        payload = self.valid_payload()

        class FakeResponses:
            def __init__(self):
                self.kwargs = None

            def create(self, **kwargs):
                self.kwargs = kwargs
                return type("Response", (), {"output_text": json.dumps(payload)})()

        responses = FakeResponses()
        client = type("Client", (), {"responses": responses})()
        extractor = OpenAITravelRequestExtractor(
            api_key="test-key", model="test-model", client=client
        )
        first = extractor.extract(
            "I have 10-14 days in October from Shanghai. I like diving and beaches.",
            self.data, self.descriptions,
        )
        second = extractor.extract(
            "I have 10-14 days in October from Shanghai. I like diving and beaches.",
            self.data, self.descriptions,
        )
        self.assertEqual(first, second)
        self.assertFalse(responses.kwargs["store"])
        self.assertEqual(responses.kwargs["text"]["format"]["type"], "json_schema")
        self.assertTrue(responses.kwargs["text"]["format"]["strict"])
        self.assertNotIn("m10-d03", responses.kwargs["instructions"])

    def test_bad_model_output_and_short_input_fail_safely(self):
        class BadResponses:
            def create(self, **kwargs):
                return type("Response", (), {"output_text": "not-json"})()

        extractor = OpenAITravelRequestExtractor(
            api_key="test-key", model="test-model",
            client=type("Client", (), {"responses": BadResponses()})(),
        )
        with self.assertRaises(NaturalLanguageInputError):
            extractor.extract("too short", self.data, self.descriptions)
        with self.assertRaises(NaturalLanguageInputError):
            extractor.extract(
                "上海十月出发旅行十到十四天，喜欢海岛和潜水。",
                self.data, self.descriptions,
            )

    def test_missing_configuration_and_service_failure_use_safe_errors(self):
        with self.assertRaises(NaturalLanguageInputError) as missing:
            OpenAITravelRequestExtractor(api_key="", model="test-model", client=object())
        self.assertEqual(missing.exception.code, "ai_not_configured")

        class FailedResponses:
            def create(self, **kwargs):
                raise RuntimeError("provider detail must not reach the user")

        extractor = OpenAITravelRequestExtractor(
            api_key="test-key", model="test-model",
            client=type("Client", (), {"responses": FailedResponses()})(),
        )
        with self.assertRaises(NaturalLanguageInputError) as failure:
            extractor.extract(
                "上海十月出发旅行十到十四天，喜欢海岛和潜水。",
                self.data, self.descriptions,
            )
        self.assertEqual(failure.exception.code, "ai_service_error")
        self.assertNotIn("provider detail", str(failure.exception))

    def test_validated_request_keeps_optimizer_deterministic(self):
        extracted = validate_extraction(
            self.valid_payload(), self.data, request_id="ai-test", model="test-model"
        )
        first = search(extracted.request, self.data, self.scenario["offer_ids"])
        second = search(extracted.request, self.data, reversed(self.scenario["offer_ids"]))
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "ok")
        self.assertGreaterEqual(len(first["itineraries"]), 2)


class StreamlitSmokeTests(unittest.TestCase):
    @staticmethod
    def manual_app():
        app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20).run()
        app.radio[0].set_value("手动规划模式").run()
        return app

    def test_discovery_mode_has_safe_manual_fallback_without_api_key(self):
        app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20).run()
        self.assertEqual(app.exception, [])
        self.assertEqual(app.radio[0].value, "发现模式")
        warnings = "\n".join(item.value for item in app.warning)
        self.assertIn("OPENAI_API_KEY", warnings)
        self.assertTrue(app.button[0].disabled)
        text = "\n".join(item.value for item in app.caption)
        self.assertIn("AI 只把文字转换为结构化条件", text)

    def test_default_m6_demo_renders_comparison_and_explanations(self):
        app = self.manual_app()
        self.assertEqual(app.exception, [])
        app.button[0].click().run()
        self.assertEqual(app.exception, [])
        self.assertIn("找到 16 条可行行程", app.success[0].value)
        self.assertEqual(len([metric for metric in app.metric if metric.label == "预计机票"]), 5)
        self.assertGreaterEqual(len(app.dataframe), 6)
        self.assertIn("较最低价", app.dataframe[0].value.columns)
        text = "\n".join(element.value for element in app.markdown)
        self.assertIn("Why recommended", text)
        self.assertIn("Trade-offs", text)
        self.assertIn("优化数据", text)
        self.assertIn("展示信息（模拟）", text)
        self.assertIn("仙本那", text)
        self.assertIn("本次测试反馈", [element.value for element in app.subheader])
        self.assertEqual(app.selectbox[1].value, "unsure")
        self.assertEqual(app.selectbox[2].value, "unsure")

    def test_no_feasible_demo_has_clear_empty_state(self):
        app = self.manual_app()
        app.selectbox[0].select("no-feasible-itinerary").run()
        app.button[0].click().run()
        self.assertEqual(app.exception, [])
        self.assertIn("没有可行行程", app.warning[0].value)
        self.assertEqual(len(app.metric), 0)
        captions = "\n".join(element.value for element in app.caption)
        self.assertIn("没有自动放宽", captions)
        text = "\n".join(element.value for element in app.markdown)
        self.assertIn("不表示现实市场一定没有航班", text)

    def test_feedback_record_can_be_generated_after_selection(self):
        app = self.manual_app()
        app.button[0].click().run()
        itinerary_id = app.session_state["last_run"]["result"]["itineraries"][0]["id"]
        app.selectbox[0].select(itinerary_id)
        app.select_slider[0].set_value(4)
        app.button[1].click().run()
        self.assertEqual(app.exception, [])
        record = app.session_state["pilot_record"]
        self.assertEqual(record["subjective_feedback"]["selected_itinerary_id"], itinerary_id)
        self.assertEqual(record["subjective_feedback"]["comprehension_rating_1_to_5"], 4)
        self.assertEqual(len(app.get("download_button")), 1)


if __name__ == "__main__":
    unittest.main()
