from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from benchmark_tools.validation import load_dataset, read_json
from travel_data import (
    DestinationProvider, FlightProvider, FlightQuery, ProviderError,
    SyntheticDestinationProvider, SyntheticFlightProvider,
)
from travel_data.providers import DEFAULT_PROFILES


ROOT = Path(__file__).resolve().parents[1]


class FlightProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = load_dataset(ROOT / "data" / "m5")
        cls.provider = SyntheticFlightProvider(cls.data)

    def test_provider_preserves_every_offer_and_integer_money(self):
        self.assertIsInstance(self.provider, FlightProvider)
        for offer_id, original in self.data.offers.items():
            with self.subTest(offer=offer_id):
                quote = self.provider.get_offer(offer_id)
                self.assertEqual(quote.to_core_offer(), original)
                self.assertIs(type(quote.price_minor), int)
                self.assertEqual(quote.currency, "CNY")
                self.assertEqual(quote.provenance.updated_at, original["observed_at"])
                self.assertEqual(quote.provenance.source_ref, original["source_ref"])
                self.assertEqual(quote.provenance.version, self.data.manifest["dataset_version"])
                self.assertTrue(quote.provenance.is_simulated)
                self.assertEqual(quote.provenance.confidence, "simulated_unverified")

    def test_route_date_query_does_not_generate_missing_edges(self):
        query = FlightQuery("PVG", "TWU", date(2027, 10, 3))
        result = self.provider.search(query)
        self.assertEqual(result.status, "ok")
        self.assertEqual([quote.id for quote in result.offers], ["m10-d03-pvg-twu"])
        self.assertEqual(result, self.provider.search(query))
        self.assertEqual(json.loads(json.dumps(result.to_dict()))["query"]["departure_date"], "2027-10-03")
        empty = self.provider.search(FlightQuery("PVG", "TWU", date(2027, 2, 3)))
        self.assertEqual(empty.status, "no_sample")
        self.assertEqual(empty.offers, ())
        self.assertIn("不代表现实中没有航班", empty.warnings[0])
        outside = self.provider.search(FlightQuery("PVG", "TWU", date(2028, 10, 3)))
        self.assertEqual(outside.status, "out_of_coverage")
        self.assertEqual(outside.offers, ())

    def test_query_uses_origin_local_date_instead_of_reference_date(self):
        offer = deepcopy(self.data.offers["m10-d18-syd-pvg"])
        for row in [offer, *offer["segments"]]:
            for field in ("departure_at", "arrival_at"):
                row[field] = (datetime.fromisoformat(row[field]) - timedelta(hours=12, minutes=30)).isoformat(timespec="minutes")
        fixture = replace(self.data, offers={offer["id"]: offer})
        provider = SyntheticFlightProvider(fixture)
        # Sydney 00:30 is Shanghai's preceding calendar day.
        found = provider.search(FlightQuery("SYD", "PVG", date(2027, 10, 18)))
        self.assertEqual([quote.id for quote in found.offers], [offer["id"]])
        self.assertEqual(provider.search(FlightQuery("SYD", "PVG", date(2027, 10, 17))).status, "no_sample")

    def test_query_rejects_invalid_or_unknown_input(self):
        for query, expected in [
            (FlightQuery("XXX", "TWU", date(2027, 10, 3)), "unknown_airport"),
            (FlightQuery("PVG", "PVG", date(2027, 10, 3)), "invalid_query"),
            (FlightQuery("PVG", "TWU", "2027-10-03"), "invalid_query"),
            (FlightQuery("PVG", "TWU", datetime(2027, 10, 3)), "invalid_query"),
        ]:
            with self.subTest(query=query), self.assertRaises(ProviderError) as caught:
                self.provider.search(query)
            self.assertEqual(caught.exception.code, expected)
        with self.assertRaises(ProviderError) as caught:
            self.provider.get_offer("missing")
        self.assertEqual(caught.exception.code, "unknown_offer")

    def test_returned_values_cannot_mutate_provider_or_source(self):
        data = deepcopy(self.data)
        provider = SyntheticFlightProvider(data)
        original = deepcopy(data.offers["m10-d03-pvg-twu"])
        exported = provider.get_offer(original["id"]).to_core_offer()
        exported["segments"][0]["id"] = "changed"
        exported["price_minor"] = 1
        data.offers[original["id"]]["price_minor"] = 2
        self.assertEqual(provider.get_offer(original["id"]).to_core_offer(), original)

    def test_order_is_independent_of_dataset_insertion_order(self):
        alternatives = {}
        for offer_id, price in (("sample-expensive", 99999), ("sample-b", 12345), ("sample-a", 12345)):
            offer = deepcopy(self.data.offers["m10-d03-pvg-twu"])
            offer["id"] = offer_id
            offer["price_minor"] = price
            alternatives[offer_id] = offer
        provider = SyntheticFlightProvider(replace(self.data, offers=alternatives))
        reversed_provider = SyntheticFlightProvider(replace(self.data, offers=dict(reversed(list(alternatives.items())))))
        query = FlightQuery("PVG", "TWU", date(2027, 10, 3))
        self.assertEqual(provider.search(query), reversed_provider.search(query))
        self.assertEqual([offer.id for offer in provider.search(query).offers],
                         ["sample-a", "sample-b", "sample-expensive"])

    def test_synthetic_provider_rejects_real_data_label(self):
        data = replace(self.data, manifest={**self.data.manifest, "coverage_kind": "manual_sample"})
        with self.assertRaises(ProviderError) as caught:
            SyntheticFlightProvider(data)
        self.assertEqual(caught.exception.code, "invalid_dataset")


class DestinationProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = load_dataset(ROOT / "data" / "m5")
        cls.provider = SyntheticDestinationProvider(cls.data)

    def test_all_forty_profiles_have_separate_versioned_sources(self):
        self.assertIsInstance(self.provider, DestinationProvider)
        profiles = self.provider.list_destinations()
        self.assertEqual(len(profiles), 40)
        self.assertEqual({profile.id for profile in profiles}, set(self.data.destinations))
        for profile in profiles:
            with self.subTest(destination=profile.id):
                self.assertTrue(profile.descriptive.suitable_activities)
                self.assertTrue(profile.descriptive.best_season_months)
                self.assertTrue(profile.airport_ids)
                self.assertTrue(profile.descriptive.provenance.is_simulated)
                self.assertEqual(profile.descriptive.provenance.confidence, "simulated_unverified")
                self.assertIsNotNone(datetime.fromisoformat(profile.descriptive.provenance.updated_at).tzinfo)
                self.assertEqual(profile.optimization.provenance.source_ref, self.data.manifest["dataset_id"])
                self.assertNotEqual(profile.optimization.provenance.source_ref, profile.descriptive.provenance.source_ref)
                source = self.data.destinations[profile.id]
                self.assertEqual(profile.optimization.recommended_stay_nights, source["recommended_stay_nights"])
                self.assertEqual(dict(profile.optimization.tag_scores), source["tag_scores"])
                self.assertEqual(dict(profile.optimization.season_scores_by_month), source["season_scores_by_month"])
                json.dumps(profile.to_dict(), ensure_ascii=False)

    def test_preference_match_explains_existing_tags_without_new_route_score(self):
        matches = self.provider.match_preferences("SEMPORNA", {"food": 30, "diving": 100, "city": 0})
        self.assertEqual([match.tag_id for match in matches], ["diving", "food"])
        self.assertEqual(matches[0].preference_weight, 100)
        self.assertEqual(matches[0].destination_tag_score, self.data.destinations["SEMPORNA"]["tag_scores"]["diving"])
        self.assertEqual(self.provider.match_preferences("SEMPORNA", {}), ())
        self.assertEqual(self.provider.match_preferences("SEMPORNA", {"city": 0}), ())
        for weights in ({"unknown": 50}, {"diving": True}, {"diving": 101}, {"diving": -1}, {"diving": 1.5}):
            with self.subTest(weights=weights), self.assertRaises(ProviderError) as caught:
                self.provider.match_preferences("SEMPORNA", weights)
            self.assertEqual(caught.exception.code, "invalid_preferences")

    def test_descriptive_edits_do_not_mutate_optimization_data(self):
        before = asdict(self.data)
        payload = read_json(DEFAULT_PROFILES)
        payload["destinations"][0]["suitable_activities"] = ["另一项模拟活动"]
        payload["destinations"][0]["best_season_months"] = [12]
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "profiles.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            changed = SyntheticDestinationProvider(self.data, path)
        self.assertEqual(changed.get_destination("SEMPORNA").optimization,
                         self.provider.get_destination("SEMPORNA").optimization)
        self.assertNotEqual(changed.get_destination("SEMPORNA").descriptive,
                            self.provider.get_destination("SEMPORNA").descriptive)
        exported = changed.get_destination("SEMPORNA").to_dict()
        exported["optimization"]["tag_scores"]["diving"] = 0
        self.assertEqual(asdict(self.data), before)
        self.assertEqual(changed.get_destination("SEMPORNA").optimization,
                         self.provider.get_destination("SEMPORNA").optimization)

    def test_missing_or_malformed_descriptions_are_rejected(self):
        payload = read_json(DEFAULT_PROFILES)
        variants = []
        missing = deepcopy(payload)
        missing["destinations"].pop()
        variants.append(missing)
        duplicate = deepcopy(payload)
        duplicate["destinations"].append(deepcopy(duplicate["destinations"][0]))
        variants.append(duplicate)
        month = deepcopy(payload)
        month["destinations"][0]["best_season_months"] = [True]
        variants.append(month)
        confidence = deepcopy(payload)
        confidence["confidence"] = "verified"
        variants.append(confidence)
        variants.append([])
        timestamp = deepcopy(payload)
        timestamp["updated_at"] = "2026-09-21T12:00:00"
        variants.append(timestamp)
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "profiles.json"
            for index, variant in enumerate(variants):
                with self.subTest(variant=index), self.assertRaises(ProviderError) as caught:
                    path.write_text(json.dumps(variant), encoding="utf-8")
                    SyntheticDestinationProvider(self.data, path)
                self.assertEqual(caught.exception.code, "invalid_profiles")

    def test_unknown_destination_has_explicit_error(self):
        with self.assertRaises(ProviderError) as caught:
            self.provider.get_destination("PARIS")
        self.assertEqual(caught.exception.code, "unknown_destination")


if __name__ == "__main__":
    unittest.main()
