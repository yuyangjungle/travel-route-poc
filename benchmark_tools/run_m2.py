"""Run real search on every M1 scenario and compare complete sets to an oracle."""

import argparse
import json
import sys

from travel_core import search
from travel_core.pareto import route_key

from .oracle import exhaustive_oracle, oracle_front
from .run import ROOT, matches, run_scenario, verify_lock
from .validation import Dataset, JsonObject, ValidationError, load_dataset, read_json, require


def verify_search(scenario: JsonObject, data: Dataset) -> tuple[JsonObject, JsonObject]:
    # Keep M1 assertions unchanged, then independently discover routes.
    run_scenario(scenario, data)
    actual = search(scenario["request"], data, scenario["offer_ids"])
    matches(actual["status"], scenario["expected_search_status"], scenario["id"] + ".status")
    oracle = {}
    if scenario["expected_request_status"] == "ok":
        oracle = exhaustive_oracle(scenario["request"], data, set(scenario["offer_ids"]))
        matches(actual["search_complete"], True, "search_complete")
    else:
        matches(actual["search_complete"], False, "search_complete")
    found = {route_key(r): r for r in actual["feasible_itineraries"]}
    require(set(found) == set(oracle), scenario["id"], "feasible set differs from oracle", "benchmark_mismatch")
    for key, expected_metrics in oracle.items():
        matches(found[key], expected_metrics, scenario["id"] + ".metrics")
    front = {route_key(r) for r in actual["pareto_itineraries"]}
    require(front == oracle_front(oracle), scenario["id"], "Pareto set differs from oracle", "benchmark_mismatch")
    for witness in scenario["witnesses"]:
        key = (tuple(witness["offer_ids"]), tuple(witness["access_ids"]))
        matches(key in found, witness["expected"]["valid"], scenario["id"] + ".witness")
    summary = {
        "id": scenario["id"], "passed": True, "status": actual["status"],
        "feasible_count": len(found), "pareto_count": len(front),
        "oracle_feasible_count": len(oracle), "complete_set_match": True,
        "expanded_states": actual["run_summary"]["expanded_states"],
        "recommendations": [{
            "offer_ids": r["offer_ids"], "destinations": [v["destination_id"] for v in r["visits"]],
            "flight_total_minor": r["flight_total_minor"], "transit_minutes": r["transit_minutes"],
            "burden_points": r["burden_points"], "experience_points": r["experience_points"],
            "recommendation_score": r["recommendation_score"], "labels": r["recommendation_labels"],
        } for r in actual["itineraries"]],
    }
    return summary, actual


def run_m2(scenario_id: str | None = None) -> JsonObject:
    digest = verify_lock(ROOT)
    data = load_dataset(ROOT / "data/m1")
    scenarios = [read_json(p) for p in sorted((ROOT / "benchmarks/scenarios").glob("*.json"))]
    if scenario_id:
        require(scenario_id in {s["id"] for s in scenarios}, "scenario", "unknown scenario", "invalid_request")
        scenarios = [s for s in scenarios if s["id"] == scenario_id]
    return {"benchmark_version": "m2-v1", "fixture_digest": digest,
            "optimizer_executed": True, "oracle": "destination-permutations-and-quote-products-v1",
            "search_version": "exact-dfs-v1", "dataset_version": data.manifest["dataset_version"],
            "scoring_version": data.scoring["scoring_version"],
            "scenarios": [verify_search(s, data)[0] for s in scenarios]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        report = run_m2(args.scenario)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True))
        else:
            for s in report["scenarios"]:
                print(f"PASS {s['id']}: status={s['status']}, feasible={s['feasible_count']}, Pareto={s['pareto_count']}, oracle=match")
            print(f"PASS {len(report['scenarios'])} scenarios; optimizer_executed=True; complete sets match oracle")
        return 0
    except (ValidationError, KeyError, TypeError, ValueError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
