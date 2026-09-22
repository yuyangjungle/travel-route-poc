"""M2 correctness tests: independent oracle, discovery, constraints and Pareto."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from benchmark_tools.oracle import exhaustive_oracle, oracle_front
from benchmark_tools.run import ROOT, matches, verify_lock
from benchmark_tools.run_m2 import run_m2
from benchmark_tools.validation import load_dataset, read_json, validate_dataset, zone
from travel_core import search
from travel_core.models import ItineraryState
from travel_core.pareto import dominates, pareto_filter, route_key, select_recommendations


class SearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_dataset(ROOT / "data/m1")
        cls.b01 = read_json(ROOT / "benchmarks/scenarios/B01.json")

    def request(self, **changes):
        result = deepcopy(self.b01["request"])
        result.update(changes)
        return result

    def compare_oracle(self, request, data, allowed=None):
        allowed = set(data.offers) if allowed is None else set(allowed)
        result = search(request, data, allowed)
        self.assertTrue(result["search_complete"])
        oracle = exhaustive_oracle(request, data, allowed)
        found = {route_key(r): r for r in result["feasible_itineraries"]}
        self.assertEqual(set(found), set(oracle))
        for key in found:
            matches(found[key], oracle[key])
        self.assertEqual({route_key(r) for r in result["pareto_itineraries"]}, oracle_front(oracle))
        return result

    def offer(self, identifier, origin, destination, day, price=50000):
        a = datetime(2026, 10, day, 8, tzinfo=zone(self.data.airports[origin]["timezone"]))
        b = datetime(2026, 10, day, 12, tzinfo=zone(self.data.airports[destination]["timezone"]))
        segment = {"id": identifier + "-s1", "origin_airport_id": origin,
                   "destination_airport_id": destination, "departure_at": a.isoformat(), "arrival_at": b.isoformat()}
        return {"id": identifier, **{k: v for k, v in segment.items() if k != "id"},
                "segments": [segment], "price_minor": price, "currency": "CNY",
                "fare_profile_id": self.data.manifest["fare_profile_id"],
                "connection_count": 0, "self_transfer": False, "protected_connection": False,
                "source_type": "mock", "source_ref": "M2 test:" + identifier,
                "observed_at": "2026-09-15T12:00:00+08:00"}

    def with_offers(self, *offers):
        return replace(deepcopy(self.data), offers={o["id"]: o for o in offers})

    def test_all_twenty_existing_scenarios(self):
        report = run_m2()
        self.assertEqual(len(report["scenarios"]), 20)
        self.assertTrue(all(s["passed"] for s in report["scenarios"]))
        self.assertTrue(report["optimizer_executed"])

    def test_combined_dataset_not_just_witness_subsets(self):
        result = self.compare_oracle(self.request(origin_airport_ids=["PVG", "HGH"]), self.data)
        self.assertGreater(result["run_summary"]["feasible_count"], 2)

    def test_discovers_route_not_in_any_witness(self):
        data = deepcopy(self.data)
        cheap = deepcopy(data.offers["twu_pvg"])
        cheap["id"], cheap["price_minor"] = "new-unlisted-return", 50000
        cheap["segments"][0]["id"] = "new-unlisted-return-s1"
        data.offers[cheap["id"]] = cheap
        result = self.compare_oracle(self.request(), data, ["pvg_twu", cheap["id"]])
        self.assertEqual(result["itineraries"][0]["offer_ids"], ["pvg_twu", cheap["id"]])
        self.assertEqual(result["itineraries"][0]["flight_total_minor"], 230000)

    def test_both_destination_orders_are_explored(self):
        data = self.with_offers(
            self.offer("start-island", "PVG", "TWU", 3),
            self.offer("start-city", "PVG", "KUL", 3),
            self.offer("island-city", "TWU", "KUL", 8),
            self.offer("city-island", "KUL", "TWU", 8),
            self.offer("island-home", "TWU", "PVG", 14),
            self.offer("city-home", "KUL", "PVG", 14),
        )
        result = self.compare_oracle(self.request(required_destination_ids=["SEMPORNA", "KL"]), data)
        orders = {tuple(v["destination_id"] for v in r["visits"]) for r in result["feasible_itineraries"]}
        self.assertEqual(orders, {("SEMPORNA", "KL"), ("KL", "SEMPORNA")})

    def test_flexible_dates_produce_different_stays(self):
        data = self.with_offers(self.offer("out", "PVG", "TWU", 3),
                                self.offer("return-14", "TWU", "PVG", 14),
                                self.offer("return-15", "TWU", "PVG", 15))
        result = self.compare_oracle(self.request(), data)
        self.assertEqual({r["visits"][0]["stay_nights"] for r in result["feasible_itineraries"]}, {11, 12})

    def test_multiple_access_airports_same_destination(self):
        data = self.with_offers(self.offer("out-twu", "PVG", "TWU", 3),
                                self.offer("out-bki", "PVG", "BKI", 3),
                                self.offer("home-twu", "TWU", "PVG", 14),
                                self.offer("home-bki", "BKI", "PVG", 14))
        access = deepcopy(data.accesses["SEMPORNA_TWU"])
        access.update(id="SEMPORNA_BKI", airport_id="BKI")
        data.accesses[access["id"]] = access
        result = self.compare_oracle(self.request(max_optional_destinations=0), data)
        self.assertEqual(len(result["feasible_itineraries"]), 2)
        for route in result["feasible_itineraries"]:
            self.assertEqual(data.offers[route["offer_ids"][0]]["destination_airport_id"],
                             data.offers[route["offer_ids"][-1]]["origin_airport_id"])

    def test_four_optional_stops_plus_required(self):
        airports = ["PVG", "TWU", "KUL", "BKI", "DPS", "HKT", "PVG"]
        days = [1, 5, 8, 11, 14, 18]
        data = self.with_offers(*(self.offer(f"chain-{i}", a, b, day)
                                  for i, (a, b, day) in enumerate(zip(airports, airports[1:], days))))
        request = self.request(min_trip_days=7, max_trip_days=21)
        result = self.compare_oracle(request, data)
        self.assertEqual(len(result["feasible_itineraries"]), 1)
        self.assertEqual(len(result["feasible_itineraries"][0]["visits"]), 5)
        request["max_optional_destinations"] = 3
        self.assertEqual(self.compare_oracle(request, data)["status"], "no_feasible_in_dataset")

    def test_many_required_destinations_have_no_hidden_total_cap(self):
        airports = ["PVG", "TWU", "KUL", "BKI", "DPS", "HKT", "SGN", "PVG"]
        days = [1, 4, 7, 10, 13, 16, 20]
        data = self.with_offers(*(self.offer(f"required-{i}", a, b, day)
                                  for i, (a, b, day) in enumerate(zip(airports, airports[1:], days))))
        result = self.compare_oracle(self.request(min_trip_days=7, max_trip_days=21,
                                                 required_destination_ids=list(data.destinations),
                                                 max_optional_destinations=0), data)
        self.assertEqual(len(result["feasible_itineraries"]), 1)
        self.assertEqual(len(result["feasible_itineraries"][0]["visits"]), 6)

    def test_empty_required_and_zero_optional_is_infeasible(self):
        result = self.compare_oracle(self.request(required_destination_ids=[], max_optional_destinations=0), self.data)
        self.assertEqual(result["status"], "no_feasible_in_dataset")

    def test_empty_offer_subset_is_not_full_dataset(self):
        result = search(self.request(), self.data, [])
        self.assertEqual(result["status"], "no_feasible_in_dataset")
        self.assertEqual(result["allowed_offer_ids"], [])
        self.assertTrue(result["search_complete"])

    def test_date_window_blocks_early_departure_and_late_return(self):
        for changes in ({"window_start_date": "2026-10-04"}, {"window_end_date": "2026-10-13"}):
            result = self.compare_oracle(self.request(**changes), self.data, ["pvg_twu", "twu_pvg"])
            self.assertEqual(result["status"], "no_feasible_in_dataset")

    def test_trip_length_both_bounds(self):
        for minimum, maximum in ((13, 18), (7, 11)):
            result = self.compare_oracle(self.request(min_trip_days=minimum, max_trip_days=maximum),
                                         self.data, ["pvg_twu", "twu_pvg"])
            self.assertEqual(result["status"], "no_feasible_in_dataset")

    def test_minimum_usable_time_independently_enforced(self):
        data = deepcopy(self.data)
        data.destinations["SEMPORNA"]["min_usable_minutes"] = 8000
        result = self.compare_oracle(self.request(), data, ["pvg_twu", "twu_pvg"])
        self.assertEqual(result["status"], "no_feasible_in_dataset")

    def test_maximum_nights_enforced(self):
        data = deepcopy(self.data)
        data.destinations["SEMPORNA"]["max_stay_nights"] = 10
        result = self.compare_oracle(self.request(), data, ["pvg_twu", "twu_pvg"])
        self.assertEqual(result["status"], "no_feasible_in_dataset")

    def test_one_minor_unit_budget_boundary(self):
        for budget, count in ((360000, 1), (359999, 0)):
            result = self.compare_oracle(self.request(flight_budget_minor=budget),
                                         self.data, ["pvg_twu", "twu_pvg"])
            self.assertEqual(len(result["feasible_itineraries"]), count)

    def test_invalid_dataset_is_not_reported_as_no_solution(self):
        data = deepcopy(self.data)
        data.offers["pvg_twu"]["price_minor"] = -1
        result = search(self.request(), data)
        self.assertEqual(result["status"], "invalid_dataset")
        self.assertFalse(result["search_complete"])
        self.assertEqual(result["optimality_scope"], "none")

    def test_request_and_subset_errors(self):
        for request, allowed, status in (
            (None, None, "invalid_request"),
            (self.request(allow_self_transfer=True), None, "unsupported_request"),
            (self.request(required_destination_ids=["UNKNOWN"]), None, "insufficient_coverage"),
            (self.request(), ["UNKNOWN"], "invalid_request"),
            (self.request(), "pvg_twu", "invalid_request"),
        ):
            with self.subTest(status=status):
                result = search(request, self.data, allowed)
                self.assertEqual(result["status"], status)
                self.assertFalse(result["search_complete"])

    def test_missing_return_airports_defaults_to_origin(self):
        request = self.request()
        del request["return_airport_ids"]
        result = search(request, self.data, self.b01["offer_ids"])
        self.assertEqual(result["request"]["return_airport_ids"], ["PVG"])
        self.assertEqual(len(result["feasible_itineraries"]), 1)
        self.assertNotIn("return_airport_ids", request)

    def test_saturated_stay_and_neutral_preferences(self):
        result = self.compare_oracle(self.request(preference_weights={}), self.data, ["pvg_twu", "twu_pvg"])
        self.assertEqual(result["feasible_itineraries"][0]["experience_points"], "50.000000")

    def test_seasonality_uses_visit_start_month(self):
        data = deepcopy(self.data)
        data.destinations["SEMPORNA"]["season_scores_by_month"]["10"] = 25
        result = self.compare_oracle(self.request(), data, ["pvg_twu", "twu_pvg"])
        self.assertEqual(result["feasible_itineraries"][0]["experience_points"], "25.000000")

    def test_fractional_scores_and_preferred_bonus(self):
        request = self.request(preference_weights={"island": 31, "diving": 17, "food": 53},
                               preferred_destination_ids=["KL"])
        self.compare_oracle(request, self.data, self.b01["offer_ids"])

    def test_strict_pareto_preserves_equal_routes_removes_dominated(self):
        route = search(self.request(), self.data, ["pvg_twu", "twu_pvg"])["feasible_itineraries"][0]
        equal = deepcopy(route)
        equal["id"], equal["offer_ids"] = "equal", ["alternate-out", "alternate-return"]
        worse = deepcopy(route)
        worse["id"], worse["offer_ids"] = "worse", ["worse-out", "worse-return"]
        worse["flight_total_minor"] += 1
        self.assertFalse(dominates(route, equal))
        self.assertFalse(dominates(equal, route))
        self.assertTrue(dominates(route, worse))
        self.assertEqual({r["id"] for r in pareto_filter([worse, route, equal, route])}, {route["id"], "equal"})

    def test_recommendation_extremes_and_label_merge(self):
        result = search(self.request(), self.data, self.b01["offer_ids"])
        by_label = {label: r for r in result["itineraries"] for label in r["recommendation_labels"]}
        self.assertEqual(by_label["cheapest"]["flight_total_minor"], 280000)
        self.assertEqual(by_label["least_exhausting"]["burden_points"], "18.000000")
        self.assertEqual(by_label["best_overall"]["flight_total_minor"], 280000)
        self.assertEqual(len(result["itineraries"]), 2)
        self.assertTrue(all(not r["recommendation_labels"] for r in result["pareto_itineraries"]))

    def test_selection_keeps_extremes_before_diversity_limit(self):
        original = search(self.request(), self.data, ["pvg_twu", "twu_pvg"])["feasible_itineraries"][0]
        pool = []
        for i in range(7):
            route = deepcopy(original)
            route.update(id=str(i), offer_ids=[f"out-{i}", f"back-{i}"],
                         flight_total_minor=100000 + i * 10000,
                         burden_points=f"{30 - i}.000000", recommendation_score=f"{40 + i}.000000")
            route["visits"][0]["destination_id"] = f"D{i}"
            pool.append(route)
        selected = select_recommendations(pareto_filter(pool))
        self.assertLessEqual(len(selected), 5)
        self.assertIn("0", {r["id"] for r in selected})
        self.assertIn("6", {r["id"] for r in selected})

    def test_input_order_does_not_change_results(self):
        first = search(self.request(), self.data, self.b01["offer_ids"])
        data = deepcopy(self.data)
        data.offers.clear()
        data.offers.update(reversed(list(self.data.offers.items())))
        data.accesses.clear()
        data.accesses.update(reversed(list(self.data.accesses.items())))
        second = search(self.request(), data, reversed(self.b01["offer_ids"]))
        self.assertEqual(first, second)

    def test_no_input_mutation_or_fixture_rewrite(self):
        request, data = self.request(), deepcopy(self.data)
        before_request, before_data = deepcopy(request), deepcopy(data)
        digest = verify_lock(ROOT)
        search(request, data)
        self.assertEqual(request, before_request)
        self.assertEqual(data, before_data)
        self.assertEqual(verify_lock(ROOT), digest)

    def test_search_state_is_immutable(self):
        state = ItineraryState("SEMPORNA", "SEMPORNA_TWU", datetime(2026, 10, 3),
                               datetime(2026, 10, 3), frozenset({"SEMPORNA"}), frozenset(),
                               0, 180000, ("pvg_twu",), ("SEMPORNA_TWU",))
        with self.assertRaises(FrozenInstanceError):
            state.flight_total_minor = 0

    def test_cli_is_deterministic_across_hash_seeds(self):
        with tempfile.TemporaryDirectory(prefix="travel-m2-") as directory:
            path = Path(directory) / "request.json"
            path.write_text(json.dumps(self.request()), encoding="utf-8")
            outputs = []
            for seed in ("1", "917"):
                process = subprocess.run([sys.executable, "-m", "travel_core", "--request", str(path)],
                                         cwd=ROOT, env={**os.environ, "PYTHONHASHSEED": seed},
                                         capture_output=True, check=True)
                outputs.append(process.stdout)
            self.assertEqual(outputs[0], outputs[1])
            self.assertTrue(json.loads(outputs[0])["search_complete"])


if __name__ == "__main__":
    unittest.main()
