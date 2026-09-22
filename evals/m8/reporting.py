"""Regenerate M8-002 metrics, failure taxonomy, and readiness from saved calls."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
from typing import Any

from benchmark_tools.validation import load_dataset

from .evaluation import DATA_DIR, evaluate_runs, load_corpus, verify_manifest
from .frozen import verify_frozen_boundary


REPORT_VERSION = "m8-002-report-v1"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _ratio(numerator: int, denominator: int) -> dict[str, int | float | None]:
    return {"numerator": numerator, "denominator": denominator,
            "rate": numerator / denominator if denominator else None,
            "percentage": round(100 * numerator / denominator, 3) if denominator else None}


def _distribution(values: list[float]) -> dict[str, int | float | None]:
    if not values:
        return {"count": 0, "min": None, "median": None, "p90": None, "p95": None,
                "max": None, "mean": None}
    ordered = sorted(values)
    percentile = lambda p: ordered[min(len(ordered) - 1, int((len(ordered) - 1) * p))]
    return {"count": len(values), "min": round(ordered[0], 3),
            "median": round(statistics.median(ordered), 3),
            "p90": round(percentile(.90), 3), "p95": round(percentile(.95), 3),
            "max": round(ordered[-1], 3), "mean": round(statistics.mean(ordered), 3)}


def _detailed_metrics(base: dict[str, Any], completed: list[dict[str, Any]],
                      all_calls: list[dict[str, Any]], expected_calls: int) -> dict[str, Any]:
    evaluations = [row["evaluation"] for row in base["runs"]]
    schema_count = sum(item["schema_valid"] for item in evaluations)
    critical_items = [field for item in evaluations for field in item["field_results"]
                      if field["severity"] == "critical"]
    unresolved = base["unresolved_detection"]
    expected_rejections = [item for item in evaluations if item["expected_rejection"]]
    manual_count = sum(item["manual_correction_required"] for item in evaluations)
    latencies = [float(row["latency_ms"]) for row in completed]
    input_tokens = [row["usage"]["input_tokens"] for row in completed
                    if row.get("usage", {}).get("input_tokens") is not None]
    output_tokens = [row["usage"]["output_tokens"] for row in completed
                     if row.get("usage", {}).get("output_tokens") is not None]
    total_tokens = [row["usage"]["total_tokens"] for row in completed
                    if row.get("usage", {}).get("total_tokens") is not None]
    costs = [row["estimated_cost_usd"] for row in completed
             if row.get("estimated_cost_usd") is not None]
    successful = len(completed)
    failed = len(all_calls) - successful
    for values in base["field_accuracy"].values():
        values["percentage"] = round(values["rate"] * 100, 3)
    consistency = base["repeated_run_consistency"]
    consistency["raw_exact_percentage"] = (
        round(consistency["raw_exact_rate"] * 100, 3)
        if consistency["raw_exact_rate"] is not None else None
    )
    consistency["validated_trip_request_percentage"] = (
        round(consistency["validated_trip_request_rate"] * 100, 3)
        if consistency["validated_trip_request_rate"] is not None else None
    )
    downstream = base["downstream_itinerary_stability"]
    downstream["impact_percentages"] = {
        name: round(count * 100 / downstream["evaluated_case_count"], 3)
        if downstream["evaluated_case_count"] else None
        for name, count in downstream["impact_counts"].items()
    }
    base.update({
        "api_calls": {"expected": expected_calls, "recorded": len(all_calls),
                      "successful": successful, "failed": failed,
                      "missing": max(0, expected_calls - len(all_calls)),
                      "success": _ratio(successful, expected_calls)},
        "schema_valid_response": _ratio(schema_count, successful),
        "critical_field_errors": _ratio(
            sum(not field["correct"] for field in critical_items), len(critical_items)
        ),
        "invalid_value_rejection": _ratio(
            sum(item["invalid_value_rejected"] for item in expected_rejections),
            len(expected_rejections),
        ),
        "manual_correction_required": _ratio(manual_count, successful),
        "unresolved_detection_counts": {
            "true_positive": unresolved["true_positive"],
            "false_positive": unresolved["false_positive"],
            "false_negative": unresolved["false_negative"],
            "precision": unresolved["precision"], "recall": unresolved["recall"],
            "precision_percentage": round(unresolved["precision"] * 100, 3)
            if unresolved["precision"] is not None else None,
            "recall_percentage": round(unresolved["recall"] * 100, 3)
            if unresolved["recall"] is not None else None,
        },
        "latency_ms": _distribution(latencies),
        "token_usage": {
            "input": {"total": sum(input_tokens), **_distribution([float(v) for v in input_tokens])},
            "output": {"total": sum(output_tokens), **_distribution([float(v) for v in output_tokens])},
            "combined": {"total": sum(total_tokens), **_distribution([float(v) for v in total_tokens])},
        },
        "estimated_api_cost_usd": {
            "total": round(sum(costs), 8) if costs else None,
            "priced_call_count": len(costs), "successful_call_count": successful,
            "per_successful_call": round(sum(costs) / successful, 8) if costs and successful else None,
        },
    })
    return base


def failure_taxonomy(corpus: dict[str, Any], scored_runs: list[dict[str, Any]]) -> dict[str, Any]:
    cases = {case["id"]: case for case in corpus["cases"]}
    by_class = Counter()
    by_category: dict[str, Counter[str]] = defaultdict(Counter)
    examples: list[dict[str, Any]] = []
    for row in scored_runs:
        evaluation = row["evaluation"]
        error_class = evaluation["error_class"]
        by_class[error_class] += 1
        if error_class in {"none", "harmless_variation"}:
            continue
        case = cases[row["case_id"]]
        for category in case["categories"]:
            by_category[category][error_class] += 1
        examples.append({
            "case_id": row["case_id"], "repeat": row["repeat"],
            "error_class": error_class, "categories": case["categories"],
            "field_errors": [{"field": item["field"], "severity": item["severity"],
                              "expected": item["expected"], "actual": item["actual"]}
                             for item in evaluation["field_results"] if not item["correct"]],
            "unresolved": evaluation["unresolved"],
            "redaction_note": "Synthetic corpus case; original prose omitted from failure export.",
        })
    return {"error_class_counts": dict(by_class),
            "material_error_count": len(examples),
            "category_counts": {key: dict(value) for key, value in sorted(by_category.items())},
            "examples": examples}


def readiness(metrics: dict[str, Any], expected_calls: int) -> dict[str, Any]:
    unresolved = metrics["unresolved_detection"]
    consistency = metrics["repeated_run_consistency"]
    downstream = metrics["downstream_itinerary_stability"]
    impacts = downstream["impact_counts"]
    evaluated_downstream = downstream["evaluated_case_count"]
    cost = metrics["estimated_api_cost_usd"]["per_successful_call"]
    error_counts = metrics["error_class_counts"]
    gates = {
        "all_api_calls_completed": metrics["api_calls"]["successful"] == expected_calls,
        "schema_valid_at_least_99_percent": metrics["schema_valid_response"]["rate"] is not None and metrics["schema_valid_response"]["rate"] >= .99,
        "critical_error_at_most_1_percent": metrics["critical_field_errors"]["rate"] is not None and metrics["critical_field_errors"]["rate"] <= .01,
        "dangerous_semantic_errors_zero": error_counts.get("dangerous_semantic_error", 0) == 0,
        "unresolved_precision_at_least_95_percent": unresolved["precision"] is not None and unresolved["precision"] >= .95,
        "unresolved_recall_at_least_95_percent": unresolved["recall"] is not None and unresolved["recall"] >= .95,
        "invalid_value_rejection_100_percent": metrics["invalid_value_rejection"]["rate"] == 1.0,
        "manual_correction_at_most_10_percent": metrics["manual_correction_required"]["rate"] is not None and metrics["manual_correction_required"]["rate"] <= .10,
        "trip_request_consistency_at_least_98_percent": consistency["validated_trip_request_rate"] is not None and consistency["validated_trip_request_rate"] >= .98,
        "material_intent_violations_zero": impacts.get("violates_user_intent_materially", 0) == 0,
        "no_feasible_changes_zero": impacts.get("causes_no_feasible_solution", 0) == 0,
        "feasible_set_changes_at_most_2_percent": (impacts.get("changes_feasible_itinerary_set", 0) / evaluated_downstream <= .02) if evaluated_downstream else True,
        "latency_p95_at_most_10_seconds": metrics["latency_ms"]["p95"] is not None and metrics["latency_ms"]["p95"] <= 10_000,
        "cost_at_most_002_usd_per_request": cost is not None and cost <= .02,
    }
    semantic_gates = ["schema_valid_at_least_99_percent", "critical_error_at_most_1_percent",
                      "dangerous_semantic_errors_zero", "unresolved_precision_at_least_95_percent",
                      "unresolved_recall_at_least_95_percent", "invalid_value_rejection_100_percent",
                      "material_intent_violations_zero", "no_feasible_changes_zero"]
    if all(gates.values()):
        classification = "A. Ready for pilot use with confirmation"
    elif all(gates[name] for name in semantic_gates):
        classification = "B. Conditionally ready with clearly bounded risks"
    else:
        classification = "C. Not ready; prompt/schema iteration required"
    return {"criteria_source": "docs/m8-evaluation-methodology.md#预先冻结的-pilot-准入门槛",
            "classification": classification, "all_gates_passed": all(gates.values()),
            "gates": gates}


def regenerate(run_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    verify_manifest()
    verify_frozen_boundary()
    manifest_path = run_dir / "manifest.json"
    expected_manifest_digest = (run_dir / "manifest.sha256").read_text(encoding="ascii").strip()
    actual_manifest_digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    if actual_manifest_digest != expected_manifest_digest:
        raise ValueError("immutable run manifest digest mismatch")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    calls = read_jsonl(run_dir / "calls.jsonl")
    allowed_case_ids = set(manifest["evaluation_subset"]["case_ids"])
    call_keys = [(row["case_id"], row["repeat"]) for row in calls]
    if (any(case_id not in allowed_case_ids for case_id, _ in call_keys)
            or len(call_keys) != len(set(call_keys))
            or any(not 1 <= repeat <= manifest["repetition_count"] for _, repeat in call_keys)):
        raise ValueError("saved calls do not match immutable run selection")
    completed = [row for row in calls if row["status"] == "completed"]
    corpus = load_corpus()
    data = load_dataset(DATA_DIR)
    base = evaluate_runs(corpus, completed, data, model=manifest["requested_model"])
    metrics = _detailed_metrics(base, completed, calls, manifest["expected_call_count"])
    taxonomy = failure_taxonomy(corpus, metrics["runs"])
    decision = readiness(metrics, manifest["expected_call_count"])
    report = {
        "report_version": REPORT_VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": manifest["run_id"],
        "manifest_sha256": (run_dir / "manifest.sha256").read_text(encoding="ascii").strip(),
        "metrics": metrics, "failure_taxonomy_summary": {
            key: taxonomy[key] for key in ("error_class_counts", "material_error_count", "category_counts")
        }, "readiness": decision,
    }
    return report, taxonomy


def write_reports(run_dir: Path) -> tuple[Path, Path]:
    report, taxonomy = regenerate(run_dir)
    result_path = run_dir / "results.json"
    failure_path = run_dir / "failures-redacted.json"
    result_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    failure_path.write_text(json.dumps(taxonomy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result_path, failure_path
