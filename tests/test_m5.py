from __future__ import annotations

import copy
from pathlib import Path
import unittest

from benchmark_tools.m5_performance import DATA_DIR, build_case
from benchmark_tools.validation import load_dataset
from data.m5.generate import serialized_files
from travel_core.search import search


ROOT = Path(__file__).resolve().parents[1]


class M5DataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = load_dataset(DATA_DIR)

    def test_catalog_scale_and_simulated_marking(self):
        self.assertEqual(len(self.data.destinations), 40)
        self.assertEqual(len(self.data.airports), 43)
        self.assertEqual(self.data.manifest["coverage_kind"], "synthetic")
        self.assertIn("SIMULATED DATA ONLY", self.data.manifest["coverage_notes"][0])
        self.assertTrue(all(row["is_mock"] for row in self.data.destinations.values()))
        self.assertTrue(all(row["is_mock"] for row in self.data.accesses.values()))
        self.assertTrue(all(row["source_type"] == "mock" for row in self.data.offers.values()))

    def test_generated_files_are_byte_reproducible(self):
        for name, content in serialized_files().items():
            with self.subTest(name=name):
                self.assertEqual((DATA_DIR / name).read_text(encoding="utf-8"), content)

    def test_realism_dimensions_are_present_and_vary(self):
        destinations = list(self.data.destinations.values())
        self.assertGreater(len({row["recommended_stay_nights"] for row in destinations}), 2)
        self.assertTrue(any(len(set(row["season_scores_by_month"].values())) > 1 for row in destinations))
        for row in destinations:
            self.assertEqual(set(row["tag_scores"]), set(self.data.manifest["preference_tag_ids"]))
        self.assertEqual({offer["connection_count"] for offer in self.data.offers.values()}, {0, 1})

    def test_seasonal_prices_change_for_same_route(self):
        prices = [self.data.offers[f"m{month:02d}-d03-pvg-twu"]["price_minor"]
                  for month in (1, 4, 7, 10)]
        self.assertEqual(len(set(prices)), 4)

    def test_scale_cases_are_nested_and_product_hypotheses_are_explicit(self):
        previous_offers: set[str] = set()
        for case_id, expected_count in (("small", 8), ("medium", 18), ("large", 40)):
            request, offers, metadata = build_case(case_id, self.data)
            self.assertEqual(metadata["active_destination_count"], expected_count)
            self.assertTrue(metadata["hypothesis"])
            self.assertEqual(request["required_destination_ids"], ["SEMPORNA"])
            self.assertTrue(previous_offers <= set(offers))
            previous_offers = set(offers)

    def test_small_search_output_is_deterministic(self):
        request, offers, _ = build_case("small", self.data)
        first = search(request, self.data, offers)
        reordered = copy.deepcopy(self.data)
        reordered.offers.clear()
        reordered.offers.update(reversed(list(self.data.offers.items())))
        second = search(request, reordered, reversed(offers))
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
