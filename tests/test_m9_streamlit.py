"""User-visible confirmation boundaries for the M9 Streamlit journey."""

from copy import deepcopy
from datetime import date
import os
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from benchmark_tools.run import ROOT
from travel_core import search
from travel_ui.scenario_library import load_m6_scenarios


class M9StreamlitJourneyTests(unittest.TestCase):
    def setUp(self):
        scenario = load_m6_scenarios(ROOT / "scenarios/m6", ROOT)[1]
        request = scenario["request"]
        fields = {name: deepcopy(request[name]) for name in (
            "origin_airport_ids", "return_airport_ids", "window_start_date", "window_end_date",
            "min_trip_days", "max_trip_days", "required_destination_ids", "preferred_destination_ids",
            "preference_weights", "max_optional_destinations", "max_connections_per_offer", "allow_self_transfer",
        )}
        fields["flight_budget_cny"] = request["flight_budget_minor"] // 100
        self.draft = {
            "contract_version": "m9-intent-v2", "fields": fields,
            "summary": "已提取旅行条件，请核对。", "assumptions": ["返回机场需要确认。"],
            "unresolved_mentions": [], "missing_fields": [], "model": "test-model",
        }
        self.environment = patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-only-no-network"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.extractor = patch("travel_product.intent.extract_intent", side_effect=lambda *a, **kw: deepcopy(self.draft))
        self.mock_extract = self.extractor.start()
        self.addCleanup(self.extractor.stop)
        self.optimizer = patch("travel_core.search", wraps=search)
        self.mock_search = self.optimizer.start()
        self.addCleanup(self.optimizer.stop)

    def extract(self):
        app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20).run()
        app.button(key="ai-parse").click().run()
        self.assertEqual(app.exception, [])
        self.assertEqual(self.mock_search.call_count, 0)
        return app

    @staticmethod
    def confirm_button(app):
        return next(button for button in app.button if button.label == "确认条件并生成多个方案")

    def test_extraction_requires_explicit_confirmation_and_applies_edited_fields(self):
        app = self.extract()
        self.confirm_button(app).click().run()
        self.assertEqual(self.mock_search.call_count, 0)
        self.assertIn("勾选确认", app.error[0].value)
        app.number_input(key="ai-confirm-1-budget").set_value(5000)
        app.multiselect(key="ai-confirm-1-origins").set_value(["HGH"])
        app.date_input(key="ai-confirm-1-start").set_value(date(2027, 10, 2))
        app.checkbox(key="ai-confirm-1-acknowledged").check()
        self.confirm_button(app).click().run()
        self.assertEqual(app.exception, [])
        self.assertEqual(self.mock_search.call_count, 1)
        actual = app.session_state["last_run"]["request"]
        self.assertEqual(actual["flight_budget_minor"], 500000)
        self.assertEqual(actual["origin_airport_ids"], ["HGH"])
        self.assertEqual(actual["window_start_date"], "2027-10-02")

    def test_changed_text_clears_extraction_and_previous_results(self):
        app = self.extract()
        app.checkbox(key="ai-confirm-1-acknowledged").check()
        self.confirm_button(app).click().run()
        self.assertIn("last_run", app.session_state)
        app.text_area(key="ai_text").set_value("预算改成 2000 元，其他条件重新确认。").run()
        self.assertEqual(app.exception, [])
        self.assertNotIn("ai_extraction", app.session_state)
        self.assertNotIn("last_run", app.session_state)
        self.assertFalse(any(button.label == "确认条件并生成多个方案" for button in app.button))

    def test_missing_values_remain_empty_and_cannot_be_confirmed(self):
        self.draft["fields"].update(origin_airport_ids=None, window_start_date=None,
                                     min_trip_days=None, flight_budget_cny=None)
        self.draft["missing_fields"] = ["origin_airport_ids", "window_start_date", "min_trip_days", "flight_budget_cny"]
        app = self.extract()
        self.assertIsNone(app.date_input(key="ai-confirm-1-start").value)
        self.assertIsNone(app.number_input(key="ai-confirm-1-budget").value)
        self.assertEqual(app.multiselect(key="ai-confirm-1-origins").value, [])
        app.checkbox(key="ai-confirm-1-acknowledged").check()
        self.confirm_button(app).click().run()
        self.assertEqual(self.mock_search.call_count, 0)
        self.assertIn("请补全", app.error[0].value)

    def test_unresolved_mentions_require_separate_resolution(self):
        self.draft["unresolved_mentions"] = ["目录没有南极洲，请明确如何处理。"]
        app = self.extract()
        app.checkbox(key="ai-confirm-1-acknowledged").check()
        self.confirm_button(app).click().run()
        self.assertEqual(self.mock_search.call_count, 0)
        app.checkbox(key="ai-confirm-1-resolved").check()
        self.confirm_button(app).click().run()
        self.assertEqual(app.exception, [])
        self.assertEqual(self.mock_search.call_count, 1)

    def test_unsupported_self_transfer_is_not_silently_removed(self):
        self.draft["fields"]["allow_self_transfer"] = True
        app = self.extract()
        self.assertTrue(app.checkbox(key="ai-confirm-1-self-transfer").value)
        app.checkbox(key="ai-confirm-1-acknowledged").check()
        self.confirm_button(app).click().run()
        self.assertEqual(self.mock_search.call_count, 0)
        self.assertIn("自行转机", app.error[0].value)

    def test_provider_error_does_not_reveal_credentials(self):
        self.mock_extract.side_effect = RuntimeError("Authorization: Bearer test-secret-never-display")
        app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20).run()
        app.button(key="ai-parse").click().run()
        self.assertEqual(app.exception, [])
        self.assertEqual(self.mock_search.call_count, 0)
        self.assertIn("暂时无法提取", app.error[0].value)
        text = " ".join(element.value for kind in (app.error, app.caption, app.markdown) for element in kind)
        self.assertNotIn("test-secret-never-display", text)
        self.assertNotIn("Authorization", text)


if __name__ == "__main__":
    unittest.main()
