"""Measure the unchanged exact search against deterministic M5 scale cases."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import statistics
import sys
import time
import tracemalloc
from datetime import datetime
from typing import Any

from benchmark_tools.validation import Dataset, load_dataset, read_json
from travel_core.search import search


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "m5"
CASE_FILE = ROOT / "benchmarks" / "m5" / "scales.json"
REPORT_FILE = ROOT / "benchmarks" / "m5" / "latest-performance.json"


def active_airports(data: Dataset, destination_ids: set[str]) -> set[str]:
    return {access["airport_id"] for access in data.accesses.values()
            if access["destination_id"] in destination_ids}


def build_case(case_id: str, data: Dataset | None = None) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    data = data or load_dataset(DATA_DIR)
    suite = read_json(CASE_FILE)
    try:
        case = next(item for item in suite["cases"] if item["id"] == case_id)
    except StopIteration as exc:
        raise ValueError(f"unknown M5 performance case: {case_id}") from exc
    ordered_destinations = list(data.destinations)
    destinations = set(ordered_destinations[:case["active_destination_count"]])
    airports = active_airports(data, destinations)
    homes = ("PVG", "HGH", "NKG")
    airport_sequence = [access["airport_id"] for access in data.accesses.values()
                        if access["destination_id"] in destinations]
    airport_index = {airport: index for index, airport in enumerate(airport_sequence)}
    offsets_by_case = {"small": {1, 2}, "medium": {1, 2, 3}, "large": {1, 2, 3, 5}}

    def included(offer: dict[str, Any]) -> bool:
        if not offer["departure_at"].startswith("2027-10-"):
            return False
        origin, destination = offer["origin_airport_id"], offer["destination_airport_id"]
        day = datetime.fromisoformat(offer["departure_at"]).day
        if origin in homes and destination in airports:
            return day == 3 and origin == homes[airport_index[destination] % len(homes)]
        if origin in airports and destination in homes:
            return day in {12, 15, 21} and destination == homes[(airport_index[origin] + 1) % len(homes)]
        if origin in airports and destination in airports:
            offset = airport_index[destination] - airport_index[origin]
            return day in {8, 11, 14, 17} and offset in offsets_by_case[case_id]
        return False

    allowed = sorted(offer["id"] for offer in data.offers.values() if included(offer))
    request = {
        "request_id": f"M5-{case_id}",
        "origin_airport_ids": ["PVG", "HGH", "NKG"],
        "return_airport_ids": ["PVG", "HGH", "NKG"],
        "window_start_date": suite["common_window"]["window_start_date"],
        "window_end_date": suite["common_window"]["window_end_date"],
        "reference_timezone": "Asia/Shanghai",
        "min_trip_days": 7,
        "max_trip_days": 21,
        "flight_budget_minor": 1200000,
        "currency": "CNY",
        "passenger_count": 1,
        "fare_profile_id": data.manifest["fare_profile_id"],
        "required_destination_ids": case["required_destination_ids"],
        "preferred_destination_ids": case["preferred_destination_ids"],
        "preference_weights": {"island": 90, "diving": 100, "food": 70,
                               "culture": 30, "nature": 75, "city": 20, "relaxation": 80},
        "max_optional_destinations": case["max_optional_destinations"],
        "max_connections_per_offer": 1,
        "allow_self_transfer": False,
    }
    metadata = {**case, "active_destination_ids": ordered_destinations[:case["active_destination_count"]],
                "allowed_offer_count": len(allowed)}
    return request, allowed, metadata


def result_digest(result: dict[str, Any]) -> str:
    stable = {
        "status": result["status"],
        "expanded_states": result["run_summary"]["expanded_states"],
        "feasible_ids": [row["id"] for row in result["feasible_itineraries"]],
        "pareto_ids": [row["id"] for row in result["pareto_itineraries"]],
        "recommendation_ids": [row["id"] for row in result["itineraries"]],
    }
    payload = json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def measure(case_id: str) -> dict[str, Any]:
    data = load_dataset(DATA_DIR)
    request, allowed, metadata = build_case(case_id, data)
    timed_results: list[dict[str, Any]] = []
    runtime_samples_ms: list[float] = []
    for _ in range(3):
        started = time.perf_counter_ns()
        timed_results.append(search(request, data, allowed))
        runtime_samples_ms.append((time.perf_counter_ns() - started) / 1_000_000)
    result = timed_results[0]
    tracemalloc.start()
    memory_result = search(request, data, allowed)
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    if not result["search_complete"]:
        raise RuntimeError(f"case {case_id} did not complete: {result['status']}")
    digest = result_digest(result)
    if any(digest != result_digest(item) for item in [*timed_results[1:], memory_result]):
        raise RuntimeError(f"case {case_id} changed between repeated timing and memory runs")
    return {
        "case_id": case_id,
        "hypothesis": metadata["hypothesis"],
        "active_destination_count": metadata["active_destination_count"],
        "active_airport_count": metadata["active_destination_count"] + 3,
        "allowed_offer_count": metadata["allowed_offer_count"],
        "max_optional_destinations": metadata["max_optional_destinations"],
        "expanded_states": result["run_summary"]["expanded_states"],
        "runtime_ms": round(statistics.median(runtime_samples_ms), 3),
        "runtime_samples_ms": [round(value, 3) for value in runtime_samples_ms],
        "peak_traced_memory_bytes": peak_bytes,
        "feasible_candidate_count": result["run_summary"]["feasible_count"],
        "pareto_candidate_count": result["run_summary"]["pareto_count"],
        "recommendation_count": len(result["itineraries"]),
        "result_digest": digest,
        "status": result["status"],
    }


def run_suite(case_ids: list[str] | None = None) -> dict[str, Any]:
    suite = read_json(CASE_FILE)
    ids = case_ids or [case["id"] for case in suite["cases"]]
    return {
        "benchmark_version": suite["benchmark_version"],
        "dataset_id": "asia-pacific-m5-synthetic",
        "measurement_note": "Runtime is the median of three uninstrumented exact-search runs and excludes dataset loading. Peak memory is measured in a separate identical run with tracemalloc and covers Python allocations only.",
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "cases": [measure(case_id) for case_id in ids],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic M5 scalability cases")
    parser.add_argument("--case", choices=("small", "medium", "large"), action="append")
    parser.add_argument("--write", action="store_true", help="write benchmarks/m5/latest-performance.json")
    args = parser.parse_args()
    report = run_suite(args.case)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.write:
        REPORT_FILE.write_text(rendered, encoding="utf-8")
    sys.stdout.buffer.write(rendered.encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
