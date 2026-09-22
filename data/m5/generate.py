"""Generate the checked-in M5 synthetic Asia-Pacific dataset.

The generator is deliberately formula based and has no random source. Running it
twice with the same source produces byte-identical JSON files.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Any

from benchmark_tools.validation import zone


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "data" / "m5"
HOME_AIRPORTS = (
    ("PVG", "Shanghai Pudong", "Asia/Shanghai"),
    ("HGH", "Hangzhou Xiaoshan", "Asia/Shanghai"),
    ("NKG", "Nanjing Lukou", "Asia/Shanghai"),
)
TAGS = ("island", "diving", "food", "culture", "nature", "city", "relaxation")

# id, display name, airport, airport name, timezone, profile, recommended nights
DESTINATIONS = (
    ("SEMPORNA", "Semporna", "TWU", "Tawau", "Asia/Kuching", "dive", 4),
    ("KL", "Kuala Lumpur", "KUL", "Kuala Lumpur", "Asia/Kuala_Lumpur", "hub", 3),
    ("SAIGON", "Ho Chi Minh City", "SGN", "Tan Son Nhat", "Asia/Ho_Chi_Minh", "food_city", 3),
    ("BALI", "Bali", "DPS", "Denpasar", "Asia/Makassar", "island", 5),
    ("PHUKET", "Phuket", "HKT", "Phuket", "Asia/Bangkok", "island", 4),
    ("SINGAPORE", "Singapore", "SIN", "Singapore Changi", "Asia/Singapore", "hub", 3),
    ("BANGKOK", "Bangkok", "BKK", "Suvarnabhumi", "Asia/Bangkok", "food_city", 4),
    ("CEBU", "Cebu", "CEB", "Mactan-Cebu", "Asia/Manila", "dive", 4),
    ("TOKYO", "Tokyo", "NRT", "Narita", "Asia/Tokyo", "city", 5),
    ("SEOUL", "Seoul", "ICN", "Incheon", "Asia/Seoul", "city", 4),
    ("TAIPEI", "Taipei", "TPE", "Taoyuan", "Asia/Taipei", "food_city", 4),
    ("MANILA", "Manila", "MNL", "Ninoy Aquino", "Asia/Manila", "hub", 3),
    ("HANOI", "Hanoi", "HAN", "Noi Bai", "Asia/Ho_Chi_Minh", "culture", 4),
    ("DANANG", "Da Nang", "DAD", "Da Nang", "Asia/Ho_Chi_Minh", "beach", 4),
    ("KOTA", "Kota Kinabalu", "BKI", "Kota Kinabalu", "Asia/Kuching", "nature", 4),
    ("PALAWAN", "Palawan", "PPS", "Puerto Princesa", "Asia/Manila", "nature_island", 5),
    ("OSAKA", "Osaka", "KIX", "Kansai", "Asia/Tokyo", "food_city", 4),
    ("HONG_KONG", "Hong Kong", "HKG", "Hong Kong", "Asia/Hong_Kong", "hub", 3),
    ("CHIANG_MAI", "Chiang Mai", "CNX", "Chiang Mai", "Asia/Bangkok", "culture", 4),
    ("PENANG", "Penang", "PEN", "Penang", "Asia/Kuala_Lumpur", "food_island", 3),
    ("LOMBOK", "Lombok", "LOP", "Lombok", "Asia/Makassar", "dive", 4),
    ("BORACAY", "Boracay", "MPH", "Caticlan", "Asia/Manila", "island", 4),
    ("PHU_QUOC", "Phu Quoc", "PQC", "Phu Quoc", "Asia/Ho_Chi_Minh", "island", 4),
    ("SIEM_REAP", "Siem Reap", "SAI", "Siem Reap-Angkor", "Asia/Phnom_Penh", "culture", 4),
    ("JAKARTA", "Jakarta", "CGK", "Soekarno-Hatta", "Asia/Jakarta", "hub", 3),
    ("YOGYAKARTA", "Yogyakarta", "YIA", "Yogyakarta International", "Asia/Jakarta", "culture", 4),
    ("SIARGAO", "Siargao", "IAO", "Sayak", "Asia/Manila", "nature_island", 5),
    ("LANGKAWI", "Langkawi", "LGK", "Langkawi", "Asia/Kuala_Lumpur", "island", 4),
    ("PHNOM_PENH", "Phnom Penh", "PNH", "Phnom Penh", "Asia/Phnom_Penh", "culture", 3),
    ("LUANG_PRABANG", "Luang Prabang", "LPQ", "Luang Prabang", "Asia/Vientiane", "culture", 4),
    ("MALE", "Maldives", "MLE", "Velana", "Indian/Maldives", "dive", 6),
    ("PALAU", "Palau", "ROR", "Roman Tmetuchl", "Pacific/Palau", "dive", 6),
    ("MACAU", "Macau", "MFM", "Macau", "Asia/Macau", "food_city", 2),
    ("VIENTIANE", "Vientiane", "VTE", "Wattay", "Asia/Vientiane", "culture", 3),
    ("YANGON", "Yangon", "RGN", "Yangon", "Asia/Yangon", "culture", 3),
    ("GUAM", "Guam", "GUM", "Antonio B. Won Pat", "Pacific/Guam", "island", 5),
    ("CAIRNS", "Cairns", "CNS", "Cairns", "Australia/Brisbane", "nature", 5),
    ("SYDNEY", "Sydney", "SYD", "Sydney", "Australia/Sydney", "city", 6),
    ("AUCKLAND", "Auckland", "AKL", "Auckland", "Pacific/Auckland", "nature", 6),
    ("NADI", "Fiji", "NAN", "Nadi", "Pacific/Fiji", "nature_island", 6),
)

PROFILE_TAGS = {
    "dive": (95, 100, 55, 35, 85, 20, 90),
    "island": (95, 65, 70, 35, 75, 25, 95),
    "nature_island": (90, 70, 55, 40, 95, 20, 95),
    "food_island": (75, 35, 100, 70, 55, 55, 80),
    "beach": (85, 45, 75, 55, 70, 45, 90),
    "nature": (45, 25, 65, 65, 100, 35, 80),
    "culture": (20, 5, 80, 100, 70, 55, 65),
    "food_city": (10, 0, 100, 80, 35, 95, 45),
    "city": (5, 0, 85, 85, 45, 100, 40),
    "hub": (15, 5, 90, 65, 40, 100, 50),
}
ISLAND_PROFILES = {"dive", "island", "nature_island", "food_island", "beach"}
REMOTE_AIRPORTS = {"TWU", "LOP", "MPH", "PPS", "IAO", "LPQ", "MLE", "ROR", "GUM", "CNS", "AKL", "NAN"}
HUB_AIRPORTS = ("SIN", "KUL", "BKK", "MNL", "NRT")
SEASONS = (1, 4, 7, 10)


def seasonal_scores(profile: str) -> dict[str, int]:
    scores: dict[str, int] = {}
    for month in range(1, 13):
        if profile in ISLAND_PROFILES:
            value = (88, 92, 96, 94, 82, 72, 68, 72, 78, 88, 92, 90)[month - 1]
        elif profile in {"city", "food_city", "hub", "culture"}:
            value = (82, 84, 90, 94, 88, 76, 72, 74, 84, 96, 92, 86)[month - 1]
        else:
            value = (86, 88, 92, 94, 84, 75, 72, 74, 84, 92, 90, 88)[month - 1]
        scores[str(month)] = value
    return scores


def local_datetime(year: int, month: int, day: int, hour: int, airport: str,
                   timezones: dict[str, str]) -> datetime:
    return datetime(year, month, day, hour, 0, tzinfo=zone(timezones[airport]))


def iso(moment: datetime) -> str:
    return moment.isoformat(timespec="minutes")


def direct_minutes(origin_index: int, destination_index: int) -> int:
    return 120 + ((origin_index * 37 + destination_index * 53) % 7) * 35


def make_offer(offer_id: str, origin: str, destination: str, departure: datetime,
               price_minor: int, airport_order: dict[str, int], timezones: dict[str, str],
               connected: bool) -> dict[str, Any]:
    segments: list[dict[str, Any]] = []
    if connected:
        hub = next(code for code in HUB_AIRPORTS if code not in {origin, destination})
        first_arrival = (departure.astimezone(zone("UTC")) + timedelta(minutes=150)).astimezone(zone(timezones[hub]))
        second_departure = (first_arrival.astimezone(zone("UTC")) + timedelta(minutes=120)).astimezone(zone(timezones[hub]))
        arrival = (second_departure.astimezone(zone("UTC")) + timedelta(minutes=210)).astimezone(zone(timezones[destination]))
        segment_points = ((origin, hub, departure, first_arrival),
                          (hub, destination, second_departure, arrival))
    else:
        duration = direct_minutes(airport_order[origin], airport_order[destination])
        arrival = (departure.astimezone(zone("UTC")) + timedelta(minutes=duration)).astimezone(zone(timezones[destination]))
        segment_points = ((origin, destination, departure, arrival),)
    for number, (segment_origin, segment_destination, start, end) in enumerate(segment_points, 1):
        segments.append({
            "id": f"{offer_id}-s{number}",
            "origin_airport_id": segment_origin,
            "destination_airport_id": segment_destination,
            "departure_at": iso(start),
            "arrival_at": iso(end),
        })
    return {
        "id": offer_id,
        "origin_airport_id": origin,
        "destination_airport_id": destination,
        "departure_at": segments[0]["departure_at"],
        "arrival_at": segments[-1]["arrival_at"],
        "segments": segments,
        "price_minor": price_minor,
        "currency": "CNY",
        "fare_profile_id": "adult-cny-tax-cabin7kg-no-checked-v1",
        "connection_count": len(segments) - 1,
        "self_transfer": False,
        "protected_connection": len(segments) > 1,
        "source_type": "mock",
        "source_ref": f"m5-deterministic-generator:{offer_id}",
        "observed_at": "2026-09-16T12:00:00+08:00",
    }


def generate() -> dict[str, Any]:
    airports = [{"id": code, "name": name, "timezone": timezone}
                for code, name, timezone in HOME_AIRPORTS]
    airports += [{"id": airport, "name": airport_name, "timezone": timezone}
                 for _, _, airport, airport_name, timezone, _, _ in DESTINATIONS]
    timezones = {row["id"]: row["timezone"] for row in airports}
    airport_order = {row["id"]: index for index, row in enumerate(airports)}

    destinations = []
    accesses = []
    for index, (destination_id, name, airport, _, timezone, profile, recommended) in enumerate(DESTINATIONS):
        minimum = 2 if recommended <= 4 else 3
        destinations.append({
            "id": destination_id,
            "name": f"{name} (SIMULATED profile)",
            "timezone": timezone,
            "tag_scores": dict(zip(TAGS, PROFILE_TAGS[profile], strict=True)),
            "season_scores_by_month": seasonal_scores(profile),
            "min_stay_nights": minimum,
            "recommended_stay_nights": recommended,
            "max_stay_nights": min(18, recommended + 8),
            "min_usable_minutes": 720 if minimum == 2 else 1080,
            "metadata_source": "M5 simulated profile generated from a category template; not researched travel advice.",
            "is_mock": True,
        })
        transfer = 25 + (index * 17) % 76
        accesses.append({
            "id": f"{destination_id}_{airport}",
            "destination_id": destination_id,
            "airport_id": airport,
            "to_destination_minutes": transfer,
            "to_airport_minutes": transfer + 10,
            "to_destination_cost_minor": 2000 + transfer * 40,
            "to_airport_cost_minor": 2200 + transfer * 40,
            "source_note": "SIMULATED fixed ground transfer; available at any time and not a real timetable.",
            "is_mock": True,
        })

    offers: list[dict[str, Any]] = []
    destination_airports = [row[2] for row in DESTINATIONS]
    home_codes = [row[0] for row in HOME_AIRPORTS]

    # Four snapshots expose seasonal fare changes for the same routes.
    for month in SEASONS:
        seasonal_percent = {1: 88, 4: 100, 7: 126, 10: 108}[month]
        for home_index, home in enumerate(home_codes):
            for destination_index, airport in enumerate(destination_airports):
                base = 70000 + destination_index * 4300 + home_index * 7000
                remote = airport in REMOTE_AIRPORTS
                outward_id = f"m{month:02d}-d03-{home.lower()}-{airport.lower()}"
                return_id = f"m{month:02d}-d18-{airport.lower()}-{home.lower()}"
                offers.append(make_offer(outward_id, home, airport,
                                         local_datetime(2027, month, 3, 8, home, timezones),
                                         base * seasonal_percent // 100, airport_order, timezones, remote))
                offers.append(make_offer(return_id, airport, home,
                                         local_datetime(2027, month, 18, 13, airport, timezones),
                                         (base + 12000) * seasonal_percent // 100,
                                         airport_order, timezones, remote))

    # October has enough alternate dates and inter-destination edges for search scaling.
    for home_index, home in enumerate(home_codes):
        for destination_index, airport in enumerate(destination_airports):
            base = 68000 + destination_index * 3900 + home_index * 6500
            for day in (2, 5):
                offer_id = f"m10-d{day:02d}-{home.lower()}-{airport.lower()}"
                if not any(o["id"] == offer_id for o in offers):
                    offers.append(make_offer(offer_id, home, airport,
                                             local_datetime(2027, 10, day, 8, home, timezones),
                                             base + day * 1300, airport_order, timezones,
                                             airport in REMOTE_AIRPORTS))
            for day in (12, 15, 21):
                offer_id = f"m10-d{day:02d}-{airport.lower()}-{home.lower()}"
                offers.append(make_offer(offer_id, airport, home,
                                         local_datetime(2027, 10, day, 13, airport, timezones),
                                         base + 9000 + day * 900, airport_order, timezones,
                                         airport in REMOTE_AIRPORTS))

    # A deterministic 12-neighbour graph gives multi-stop choices without a fully
    # connected 40-node timetable. Major hubs are inserted into every node's set.
    count = len(destination_airports)
    for origin_index, origin in enumerate(destination_airports):
        neighbours = {destination_airports[(origin_index + offset) % count]
                      for offset in (1, 2, 3, 5, 8, 13, 17, 23)}
        neighbours.update(HUB_AIRPORTS)
        neighbours.discard(origin)
        for destination in sorted(neighbours):
            destination_index = destination_airports.index(destination)
            for day in (5, 8, 11, 14, 17):
                offer_id = f"m10-d{day:02d}-{origin.lower()}-{destination.lower()}"
                price = 32000 + ((origin_index * 71 + destination_index * 43 + day * 19) % 110) * 900
                offers.append(make_offer(offer_id, origin, destination,
                                         local_datetime(2027, 10, day, 10, origin, timezones),
                                         price, airport_order, timezones, False))

    # Remove duplicate IDs created where the neighbour ring and hub set overlap.
    offers_by_id = {offer["id"]: offer for offer in offers}
    offers = [offers_by_id[key] for key in sorted(offers_by_id)]
    scoring = json.loads((ROOT / "data" / "m1" / "scoring.json").read_text(encoding="utf-8"))
    manifest = {
        "dataset_id": "asia-pacific-m5-synthetic",
        "dataset_version": "1.0.0",
        "schema_version": "1.0.0",
        "created_at": "2026-09-16T12:00:00+08:00",
        "currency": "CNY",
        "fare_profile_id": "adult-cny-tax-cabin7kg-no-checked-v1",
        "fare_profile_description": "SIMULATED one-adult CNY fare; taxes and cabin bag assumed; not purchasable.",
        "preference_tag_ids": list(TAGS),
        "covered_airport_ids": [row["id"] for row in airports],
        "covered_destination_ids": [row["id"] for row in destinations],
        "coverage_start_date": "2027-01-01",
        "coverage_end_date": "2027-10-31",
        "coverage_kind": "synthetic",
        "coverage_notes": [
            "SIMULATED DATA ONLY: every fare, timetable, transfer, tag, season score and stay recommendation is invented.",
            "Four seasonal snapshots illustrate deterministic variation; they do not estimate real prices or availability.",
            "October contains a denser graph for scalability experiments; missing edges mean missing fixture coverage, not no real flight.",
        ],
    }
    return {"manifest": manifest, "airports": airports, "destinations": destinations,
            "accesses": accesses, "offers": offers, "scoring": scoring}


def serialized_files() -> dict[str, str]:
    return {f"{name}.json": json.dumps(value, ensure_ascii=False, indent=2) + "\n"
            for name, value in generate().items()}


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate or verify deterministic M5 data")
    parser.add_argument("--check", action="store_true", help="fail if checked-in JSON differs")
    args = parser.parse_args()
    expected = serialized_files()
    if args.check:
        mismatches = [name for name, content in expected.items()
                      if not (OUTPUT / name).exists() or (OUTPUT / name).read_text(encoding="utf-8") != content]
        if mismatches:
            parser.error("generated files differ: " + ", ".join(mismatches))
        print(f"M5 dataset is reproducible: {len(expected)} files")
        return 0
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name, content in expected.items():
        (OUTPUT / name).write_text(content, encoding="utf-8")
    print(f"Wrote {len(expected)} deterministic files to {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
