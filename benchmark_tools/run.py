"""Read-only benchmark runner. Checks listed witnesses, never searches routes."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

from .validation import (Dataset, JsonObject, ValidationError, evaluate_witness,
                         fields, load_dataset, read_json, require, strings,
                         validate_request)

ROOT = Path(__file__).resolve().parents[1]


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def verify_lock(root: Path) -> str:
    lock = read_json(root / "benchmarks/fixtures.lock.json")
    fields(lock, "lock_version algorithm files", "lock")
    require(lock["lock_version"] == 1 and lock["algorithm"] == "sha256-canonical-json", "lock", "unsupported format")
    require(isinstance(lock["files"], dict) and bool(lock["files"]), "lock", "missing files")
    actual = {p.relative_to(root).as_posix() for base in (root / "data/m1", root / "benchmarks/scenarios")
              for p in base.glob("*.json")}
    require(actual == set(lock["files"]), "lock", "fixture file set changed", "fixture_drift")
    for name, expected in sorted(lock["files"].items()):
        require(canonical_digest(read_json(root / name)) == expected, name, "fixture content changed", "fixture_drift")
    return canonical_digest(lock)


def matches(actual: Any, expected: Any, path: str = "expected") -> None:
    """Compare manually stated fields; no golden values are generated here."""
    if isinstance(expected, dict):
        require(isinstance(actual, dict), path, "expected object", "benchmark_mismatch")
        for key, value in expected.items():
            require(key in actual, path, f"missing {key}", "benchmark_mismatch")
            matches(actual[key], value, f"{path}.{key}")
    elif isinstance(expected, list):
        require(isinstance(actual, list) and len(actual) == len(expected), path, "list length differs", "benchmark_mismatch")
        for i, (a, b) in enumerate(zip(actual, expected)):
            matches(a, b, f"{path}[{i}]")
    else:
        require(actual == expected, path, f"expected {expected!r}, got {actual!r}", "benchmark_mismatch")


def check_certificate(certificate: JsonObject, request: JsonObject,
                      allowed: set[str], data: Dataset) -> None:
    """Two local, sufficient infeasibility proofs; no graph traversal."""
    fields(certificate, "type explanation", "certificate")
    require(bool(certificate["explanation"]), "certificate", "explanation required")
    offers = [data.offers[x] for x in sorted(allowed)]
    if certificate["type"] == "outbound_price_floor":
        outgoing = [o["price_minor"] for o in offers if o["origin_airport_id"] in request["origin_airport_ids"]]
        require(bool(outgoing) and min(outgoing) > request["flight_budget_minor"], "certificate", "outbound floor does not prove infeasibility", "benchmark_mismatch")
        matches(min(outgoing), certificate["minimum_outbound_minor"], "certificate.minimum_outbound_minor")
    elif certificate["type"] == "no_return_offer":
        require(not any(o["destination_airport_id"] in request["return_airport_ids"] for o in offers), "certificate", "return offer exists", "benchmark_mismatch")
    else:
        raise ValidationError("invalid_dataset", "certificate.type", "unsupported proof")


def run_scenario(scenario: JsonObject, data: Dataset) -> JsonObject:
    fields(scenario, "id title hypothesis requirement_ids assumption_ids offer_ids request "
           "expected_request_status witnesses comparisons expected_search_status", "scenario")
    for key in ("id", "title", "hypothesis"):
        require(isinstance(scenario[key], str) and bool(scenario[key]), key, "nonempty text required")
    for key in ("requirement_ids", "assumption_ids", "offer_ids"):
        strings(scenario[key], key, True)
    require(all(x in {f"FR-{i:02}" for i in range(1, 10)} for x in scenario["requirement_ids"]), "requirement_ids", "unknown requirement")
    require(all(x in {f"A-{i:02}" for i in range(1, 11)} for x in scenario["assumption_ids"]), "assumption_ids", "unknown assumption")
    allowed = set(scenario["offer_ids"])
    require(allowed <= set(data.offers), "offer_ids", "unknown scenario offer")
    require(isinstance(scenario["witnesses"], list) and isinstance(scenario["comparisons"], list), "scenario", "expected lists")
    request_status = "ok"
    try:
        validate_request(scenario["request"], data)
    except ValidationError as exc:
        request_status = exc.code
    matches(request_status, scenario["expected_request_status"], "expected_request_status")
    results: dict[str, JsonObject] = {}
    if request_status != "ok":
        require(not scenario["witnesses"] and not scenario["comparisons"] and "infeasibility_certificate" not in scenario,
                "scenario", "rejected request cannot have witnesses or proof")
        matches(scenario["expected_search_status"], request_status, "expected_search_status")
    else:
        for witness in scenario["witnesses"]:
            fields(witness, "id offer_ids access_ids expected", "witness")
            require(witness["id"] not in results, "witness", "duplicate ID")
            fields(witness["expected"], "valid errors", "witness.expected")
            require(type(witness["expected"]["valid"]) is bool, "witness.expected", "boolean required")
            if witness["expected"]["valid"]:
                fields(witness["expected"], "metrics", "witness.expected")
                fields(witness["expected"]["metrics"], "flight_total_minor transit_minutes flight_day_count", "witness.expected.metrics")
            result = evaluate_witness(scenario["request"], witness, data, allowed)
            matches(result, witness["expected"], witness["id"])
            results[witness["id"]] = result
        for comparison in scenario["comparisons"]:
            fields(comparison, "left right metric expected_delta", "comparison")
            require(comparison["left"] in results and comparison["right"] in results, "comparison", "unknown witness")
            left, right = results[comparison["left"]], results[comparison["right"]]
            require(left["valid"] and right["valid"], "comparison", "only compare feasible witnesses")
            metric = comparison["metric"]
            require(metric in left["metrics"] and metric in right["metrics"], "comparison", "unknown metric")
            difference = Decimal(str(left["metrics"][metric])) - Decimal(str(right["metrics"][metric]))
            matches(difference, Decimal(str(comparison["expected_delta"])), "comparison.delta")
        if "infeasibility_certificate" in scenario:
            check_certificate(scenario["infeasibility_certificate"], scenario["request"], allowed, data)
            matches(scenario["expected_search_status"], "no_feasible_in_dataset", "expected_search_status")
            require(not any(r["valid"] for r in results.values()), "certificate", "contradicting feasible witness")
        else:
            require(any(r["valid"] for r in results.values()), "scenario", "provide a feasible witness or sufficient proof")
            matches(scenario["expected_search_status"], "ok", "expected_search_status")
    return {"id": scenario["id"], "passed": True, "request_status": request_status,
            "future_search_status": scenario["expected_search_status"],
            "proof_checked": "infeasibility_certificate" in scenario,
            "witnesses": list(results.values())}


def run_suite(root: Path = ROOT, scenario_id: str | None = None) -> JsonObject:
    digest = verify_lock(root)
    data = load_dataset(root / "data/m1")
    scenarios = [read_json(p) for p in sorted((root / "benchmarks/scenarios").glob("*.json"))]
    ids = [s["id"] for s in scenarios]
    require(len(ids) == len(set(ids)) and bool(ids), "scenarios", "duplicate/missing scenarios")
    if scenario_id:
        require(scenario_id in ids, "scenario", "unknown scenario", "invalid_request")
        scenarios = [s for s in scenarios if s["id"] == scenario_id]
    return {"benchmark_version": "m1-v1", "fixture_digest": digest,
            "dataset_version": data.manifest["dataset_version"], "scoring_version": data.scoring["scoring_version"],
            "scope": "prepared_witness_validation_only", "optimizer_executed": False,
            "scenarios": [run_scenario(s, data) for s in scenarios]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", help="Run one scenario, e.g. B01")
    parser.add_argument("--json", action="store_true", help="Deterministic JSON on stdout")
    args = parser.parse_args()
    try:
        report = run_suite(scenario_id=args.scenario)
        if args.json:
            print(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2))
        else:
            for result in report["scenarios"]:
                print(f"PASS {result['id']}: {len(result['witnesses'])} witnesses; expected M2 status={result['future_search_status']}")
            print(f"PASS {len(report['scenarios'])} scenarios; optimizer_executed=False")
            print(f"Fixture digest: {report['fixture_digest']}")
        return 0
    except (ValidationError, KeyError, TypeError, ValueError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
