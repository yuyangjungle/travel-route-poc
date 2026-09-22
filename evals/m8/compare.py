"""Create a side-by-side M8 model trade-off report without choosing a winner."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


METRICS = ("schema_valid_response_rate", "field_accuracy", "critical_field_error_rate",
           "invalid_value_rejection_rate", "manual_correction_required_rate", "repeated_run_consistency",
           "unresolved_detection", "latency_ms", "estimated_api_cost_usd",
           "downstream_itinerary_stability", "error_class_counts")


def compare(paths: list[Path]) -> dict[str, Any]:
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    completed = [report for report in reports if report.get("status") == "completed"]
    if len(completed) < 2:
        raise ValueError("comparison requires at least two completed result files")
    corpus = {(report["corpus_version"], report["corpus_sha256"],
               tuple(report["selection"]["case_ids"]), report["repeats"]) for report in completed}
    if len(corpus) != 1:
        raise ValueError("results must use the same corpus, selection, and repeat count")
    rows = []
    for report in completed:
        metrics = report["metrics"]
        rows.append({"model_requested": report["model_requested"],
                     "model_reported_versions": report["model_reported_versions"],
                     **{name: metrics.get(name) for name in METRICS}})
    return {"comparison_version": "m8-model-comparison-v1", "decision": None,
            "decision_note": "This report intentionally presents trade-offs and does not choose a winner.",
            "corpus_version": completed[0]["corpus_version"],
            "corpus_sha256": completed[0]["corpus_sha256"], "models": rows}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("results", nargs="+", type=Path)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    report = compare(args.results)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
