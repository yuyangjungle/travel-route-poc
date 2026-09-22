"""Corpus validation, field scoring, run aggregation, and downstream comparison."""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable

from benchmark_tools.m5_performance import build_case
from benchmark_tools.validation import Dataset, load_dataset
from travel_core import search
from travel_ui.natural_language import NaturalLanguageInputError, validate_extraction


ROOT = Path(__file__).resolve().parents[2]
CORPUS_FILE = Path(__file__).with_name("corpus-v1.json")
MANIFEST_FILE = Path(__file__).with_name("manifest.json")
DATA_DIR = ROOT / "data" / "m5"
SEVERITIES = {"minor", "major", "critical"}
IMPACT_CLASSES = {
    "no_material_itinerary_effect", "changes_ranking_only",
    "changes_feasible_itinerary_set", "causes_no_feasible_solution",
    "violates_user_intent_materially",
}
ERROR_CLASSES = {"none", "harmless_variation", "correctable_error", "blocking_error",
                 "dangerous_semantic_error"}


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_corpus(path: Path = CORPUS_FILE) -> dict[str, Any]:
    corpus = json.loads(path.read_text(encoding="utf-8"))
    validate_corpus(corpus)
    return corpus


def validate_corpus(corpus: dict[str, Any]) -> None:
    required_top = {"corpus_version", "prompt_contract_version", "created_at", "data_policy",
                    "splits", "cases"}
    if set(corpus) != required_top:
        raise ValueError("corpus top-level fields differ from v1 contract")
    cases = corpus["cases"]
    if not isinstance(cases, list) or not 120 <= len(cases) <= 200:
        raise ValueError("corpus must contain 120-200 cases")
    ids: set[str] = set()
    for case in cases:
        required = {"id", "split", "language", "categories", "input_text", "expectation"}
        if set(case) != required:
            raise ValueError(f"{case.get('id')}: case fields differ from v1 contract")
        if case["id"] in ids or case["split"] not in {"evaluation", "holdout"}:
            raise ValueError(f"{case['id']}: duplicate id or invalid split")
        ids.add(case["id"])
        exp = case["expectation"]
        exp_required = {"expected_outcome", "fields", "acceptable_alternatives",
                        "expected_unresolved_mentions", "manual_correction_expected", "notes"}
        if set(exp) != exp_required:
            raise ValueError(f"{case['id']}: expectation fields differ from v1 contract")
        for field, spec in exp["fields"].items():
            if set(spec) != {"expected", "severity"} or spec["severity"] not in SEVERITIES:
                raise ValueError(f"{case['id']}.{field}: invalid field expectation")
        if exp["expected_outcome"] not in {"valid", "valid_with_unresolved", "reject"}:
            raise ValueError(f"{case['id']}: invalid expected outcome")
    declared = corpus["splits"]
    actual = Counter(c["split"] for c in cases)
    if declared != dict(actual):
        raise ValueError("declared split counts do not match cases")


def verify_manifest(corpus_path: Path = CORPUS_FILE, manifest_path: Path = MANIFEST_FILE) -> str:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digest = sha256_file(corpus_path)
    if digest != manifest["corpus_sha256"]:
        raise ValueError("frozen M8 corpus digest mismatch")
    corpus = load_corpus(corpus_path)
    if manifest["corpus_version"] != corpus["corpus_version"] or manifest["case_count"] != len(corpus["cases"]):
        raise ValueError("M8 manifest metadata mismatch")
    return digest


def _normalized(value: str) -> str:
    return re.sub(r"[\W_]+", "", value.casefold(), flags=re.UNICODE)


def _unresolved_stats(expected: list[str], actual: list[str]) -> tuple[int, int, int]:
    expected_norm = [_normalized(x) for x in expected]
    actual_norm = [_normalized(x) for x in actual]
    matched_e: set[int] = set()
    matched_a: set[int] = set()
    for ei, e in enumerate(expected_norm):
        for ai, a in enumerate(actual_norm):
            if ei not in matched_e and ai not in matched_a and e and (e in a or a in e):
                matched_e.add(ei)
                matched_a.add(ai)
                break
    return len(matched_e), len(actual) - len(matched_a), len(expected) - len(matched_e)


def _alternative_matches(payload: dict[str, Any], alternatives: list[dict[str, Any]]) -> bool:
    return any(all(payload.get(k) == v for k, v in alt["fields"].items()) for alt in alternatives)


def score_payload(case: dict[str, Any], payload: Any, data: Dataset, *, model: str) -> dict[str, Any]:
    exp = case["expectation"]
    try:
        extracted = validate_extraction(payload, data, request_id=case["id"], model=model)
        schema_valid = True
        validation_error = None
    except (NaturalLanguageInputError, KeyError, TypeError, ValueError) as exc:
        extracted = None
        schema_valid = False
        validation_error = getattr(exc, "code", type(exc).__name__)

    expected_rejection = exp["expected_outcome"] == "reject"
    invalid_value_correct = expected_rejection and not schema_valid
    field_results: list[dict[str, Any]] = []
    payload_dict = payload if isinstance(payload, dict) else {}
    alt_match = _alternative_matches(payload_dict, exp["acceptable_alternatives"])
    for field, spec in exp["fields"].items():
        actual = payload_dict.get(field)
        matched = actual == spec["expected"]
        if not matched and alt_match:
            matched = any(field in alt["fields"] and actual == alt["fields"][field]
                          for alt in exp["acceptable_alternatives"])
        field_results.append({"field": field, "severity": spec["severity"], "correct": matched,
                              "expected": spec["expected"], "actual": actual})
    actual_unresolved = payload_dict.get("unresolved_mentions", [])
    if not isinstance(actual_unresolved, list) or not all(isinstance(x, str) for x in actual_unresolved):
        actual_unresolved = []
    unresolved_tp, unresolved_fp, unresolved_fn = _unresolved_stats(
        exp["expected_unresolved_mentions"], actual_unresolved
    )
    incorrect = [item for item in field_results if not item["correct"]]
    if expected_rejection:
        error_class = "none" if invalid_value_correct else "dangerous_semantic_error" if schema_valid else "blocking_error"
    elif not schema_valid:
        error_class = "blocking_error"
    elif any(item["severity"] == "critical" for item in incorrect) or unresolved_fn:
        error_class = "dangerous_semantic_error"
    elif any(item["severity"] == "major" for item in incorrect) or unresolved_fp:
        error_class = "correctable_error"
    elif incorrect:
        error_class = "harmless_variation"
    else:
        error_class = "none"
    manual = error_class in {"correctable_error", "blocking_error", "dangerous_semantic_error"}
    return {
        "schema_valid": schema_valid,
        "expected_rejection": expected_rejection,
        "invalid_value_rejected": invalid_value_correct,
        "validation_error": validation_error,
        "field_results": field_results,
        "unresolved": {"true_positive": unresolved_tp, "false_positive": unresolved_fp,
                       "false_negative": unresolved_fn},
        "error_class": error_class,
        "manual_correction_required": manual,
        "request": deepcopy(extracted.request) if extracted else None,
    }


def _route_ids(result: dict[str, Any], key: str) -> list[str]:
    return [row["id"] for row in result.get(key, [])]


def classify_downstream_impact(left: dict[str, Any], right: dict[str, Any], *,
                               material_intent_violation: bool = False) -> str:
    if material_intent_violation:
        return "violates_user_intent_materially"
    left_no_solution = left.get("status") != "ok" or not left.get("feasible_itineraries")
    right_no_solution = right.get("status") != "ok" or not right.get("feasible_itineraries")
    if left_no_solution != right_no_solution:
        return "causes_no_feasible_solution"
    left_feasible = set(_route_ids(left, "feasible_itineraries"))
    right_feasible = set(_route_ids(right, "feasible_itineraries"))
    if left_feasible != right_feasible:
        return "changes_feasible_itinerary_set"
    if (_route_ids(left, "pareto_itineraries") != _route_ids(right, "pareto_itineraries") or
            _route_ids(left, "itineraries") != _route_ids(right, "itineraries")):
        return "changes_ranking_only"
    return "no_material_itinerary_effect"


def downstream_for_requests(requests: list[dict[str, Any]], data: Dataset | None = None, *,
                            material_intent_violation: bool = False) -> dict[str, Any]:
    unique: dict[str, dict[str, Any]] = {}
    for request in requests:
        stable = deepcopy(request)
        stable.pop("request_id", None)
        key = hashlib.sha256(canonical_bytes(stable)).hexdigest()
        unique.setdefault(key, request)
    if len(unique) < 2:
        return {"evaluated": False, "reason": "fewer_than_two_distinct_valid_requests", "comparisons": []}
    data = data or load_dataset(DATA_DIR)
    _, allowed, _ = build_case("small", data)
    outputs = [(key, search(request, data, allowed)) for key, request in sorted(unique.items())]
    comparisons = []
    order = {"no_material_itinerary_effect": 0, "changes_ranking_only": 1,
             "changes_feasible_itinerary_set": 2, "causes_no_feasible_solution": 3,
             "violates_user_intent_materially": 4}
    for index in range(1, len(outputs)):
        left, right = outputs[0], outputs[index]
        impact = classify_downstream_impact(
            left[1], right[1], material_intent_violation=material_intent_violation
        )
        comparisons.append({"left_request_digest": left[0], "right_request_digest": right[0],
                            "impact": impact, "left_status": left[1]["status"],
                            "right_status": right[1]["status"]})
    worst = max((row["impact"] for row in comparisons), key=order.get)
    return {"evaluated": True, "benchmark": "m5-small-offer-subset-v1", "worst_impact": worst,
            "comparisons": comparisons}


def evaluate_runs(corpus: dict[str, Any], raw_runs: list[dict[str, Any]], data: Dataset,
                  *, model: str) -> dict[str, Any]:
    cases = {case["id"]: case for case in corpus["cases"]}
    scored: list[dict[str, Any]] = []
    requests_by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in raw_runs:
        case = cases[run["case_id"]]
        score = score_payload(case, run.get("raw_structured_result"), data, model=model)
        record = {**run, "evaluation": score}
        scored.append(record)
        if score["request"] is not None:
            requests_by_case[run["case_id"]].append(score["request"])
    field_counts: Counter[tuple[str, str]] = Counter()
    classes = Counter()
    unresolved = Counter()
    for row in scored:
        ev = row["evaluation"]
        classes[ev["error_class"]] += 1
        unresolved.update(ev["unresolved"])
        for item in ev["field_results"]:
            field_counts[(item["field"], "total")] += 1
            field_counts[(item["field"], "correct")] += int(item["correct"])
            field_counts[(item["severity"], "total")] += 1
            field_counts[(item["severity"], "correct")] += int(item["correct"])
    repeated = defaultdict(list)
    for row in scored:
        repeated[row["case_id"]].append(row)
    raw_consistent = 0
    request_consistent = 0
    repeatable = 0
    downstream: dict[str, Any] = {}
    for case_id, rows in repeated.items():
        if len(rows) < 2:
            continue
        repeatable += 1
        payloads = [canonical_bytes(row.get("raw_structured_result")) for row in rows]
        raw_consistent += int(len(set(payloads)) == 1)
        request_payloads = [canonical_bytes(row["evaluation"]["request"])
                            for row in rows if row["evaluation"]["request"] is not None]
        request_consistent += int(len(request_payloads) == len(rows) and len(set(request_payloads)) == 1)
        material = any(row["evaluation"]["error_class"] == "dangerous_semantic_error"
                       for row in rows)
        downstream[case_id] = downstream_for_requests(
            requests_by_case[case_id], data, material_intent_violation=material
        )
    total = len(scored)
    schema_valid = sum(row["evaluation"]["schema_valid"] for row in scored)
    critical_total = field_counts[("critical", "total")]
    critical_correct = field_counts[("critical", "correct")]
    latency = [row["latency_ms"] for row in scored if isinstance(row.get("latency_ms"), (int, float))]
    costs = [row["estimated_cost_usd"] for row in scored if isinstance(row.get("estimated_cost_usd"), (int, float))]
    tp, fp, fn = unresolved["true_positive"], unresolved["false_positive"], unresolved["false_negative"]
    return {
        "run_count": total,
        "schema_valid_response_rate": schema_valid / total if total else None,
        "field_accuracy": {field: {"correct": field_counts[(field, "correct")],
                                    "total": field_counts[(field, "total")],
                                    "rate": field_counts[(field, "correct")] / field_counts[(field, "total")]}
                           for field in sorted({k[0] for k in field_counts if k[0] not in SEVERITIES})
                           if field_counts[(field, "total")]},
        "critical_field_error_rate": ((critical_total - critical_correct) / critical_total
                                      if critical_total else None),
        "unresolved_detection": {"precision": tp / (tp + fp) if tp + fp else None,
                                 "recall": tp / (tp + fn) if tp + fn else None,
                                 "true_positive": tp, "false_positive": fp, "false_negative": fn},
        "invalid_value_rejection_rate": _rate(scored, "expected_rejection", "invalid_value_rejected"),
        "manual_correction_required_rate": (sum(row["evaluation"]["manual_correction_required"] for row in scored) / total
                                             if total else None),
        "error_class_counts": dict(classes),
        "repeated_run_consistency": {
            "eligible_cases": repeatable,
            "raw_exact_consistent_cases": raw_consistent,
            "raw_exact_rate": raw_consistent / repeatable if repeatable else None,
            "validated_trip_request_consistent_cases": request_consistent,
            "validated_trip_request_rate": request_consistent / repeatable if repeatable else None,
        },
        "downstream_itinerary_stability": summarize_downstream(downstream),
        "latency_ms": _distribution(latency),
        "estimated_api_cost_usd": {"total": round(sum(costs), 8) if costs else None,
                                   "priced_run_count": len(costs), "run_count": total},
        "downstream_by_case": downstream,
        "runs": scored,
    }


def _rate(rows: list[dict[str, Any]], eligible_key: str, success_key: str) -> float | None:
    eligible = [row for row in rows if row["evaluation"][eligible_key]]
    return (sum(row["evaluation"][success_key] for row in eligible) / len(eligible)) if eligible else None


def _distribution(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "mean": None, "p50": None, "p95": None}
    ordered = sorted(values)
    percentile = lambda p: ordered[min(len(ordered) - 1, int((len(ordered) - 1) * p))]
    return {"count": len(values), "mean": round(sum(values) / len(values), 3),
            "p50": round(percentile(.5), 3), "p95": round(percentile(.95), 3)}


def summarize_downstream(rows: dict[str, Any]) -> dict[str, Any]:
    counts = Counter(row.get("worst_impact") for row in rows.values() if row.get("evaluated"))
    return {"eligible_case_count": len(rows), "evaluated_case_count": sum(counts.values()),
            "impact_counts": dict(counts)}


def result_envelope(*, status: str, model: str, corpus: dict[str, Any], corpus_digest: str,
                    selection: dict[str, Any], repeats: int, reason: dict[str, str] | None = None,
                    metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"evaluation_version": "m8-evaluator-v1", "status": status, "model_requested": model,
            "model_reported_versions": [], "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "corpus_version": corpus["corpus_version"], "corpus_sha256": corpus_digest,
            "prompt_contract_version": corpus["prompt_contract_version"], "selection": selection,
            "repeats": repeats, "blocking_reason": reason, "metrics": metrics}
