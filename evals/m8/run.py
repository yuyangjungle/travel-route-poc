"""Run the frozen M8 corpus against one configured production candidate model."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

from benchmark_tools.validation import load_dataset
from travel_ui.natural_language import catalog_prompt, extraction_schema
from travel_ui.scenario_library import load_destination_descriptions

from .evaluation import (CORPUS_FILE, DATA_DIR, ROOT, evaluate_runs, load_corpus,
                         result_envelope, verify_manifest)


RESULTS_DIR = Path(__file__).with_name("results")
DESCRIPTION_FILE = ROOT / "data" / "m6" / "destination_descriptions.json"


def select_cases(corpus: dict[str, Any], *, splits: set[str], case_ids: set[str],
                 categories: set[str], limit: int | None) -> list[dict[str, Any]]:
    rows = [row for row in corpus["cases"] if row["split"] in splits]
    if case_ids:
        unknown = case_ids - {row["id"] for row in corpus["cases"]}
        if unknown:
            raise ValueError(f"unknown case ids: {', '.join(sorted(unknown))}")
        rows = [row for row in rows if row["id"] in case_ids]
    if categories:
        rows = [row for row in rows if categories & set(row["categories"])]
    if limit is not None:
        rows = rows[:limit]
    if not rows:
        raise ValueError("case selection is empty")
    return rows


def _usage(response: Any) -> dict[str, int | None]:
    usage = getattr(response, "usage", None)
    return {"input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None)}


def _cost(usage: dict[str, int | None], input_rate: float | None,
          output_rate: float | None) -> float | None:
    if input_rate is None or output_rate is None or usage["input_tokens"] is None or usage["output_tokens"] is None:
        return None
    return round((usage["input_tokens"] * input_rate + usage["output_tokens"] * output_rate) / 1_000_000, 8)


def call_model(client: Any, *, model: str, text: str, instructions: str,
               schema: dict[str, Any], input_rate: float | None,
               output_rate: float | None) -> dict[str, Any]:
    started = time.perf_counter_ns()
    try:
        response = client.responses.create(
            model=model, instructions=instructions, input=text,
            text={"format": {"type": "json_schema", "name": "travel_request_extraction",
                             "strict": True, "schema": schema}},
            store=False,
        )
        latency_ms = round((time.perf_counter_ns() - started) / 1_000_000, 3)
        raw = json.loads(response.output_text)
        usage = _usage(response)
        return {"status": "completed", "model_reported": getattr(response, "model", None),
                "response_id": getattr(response, "id", None), "latency_ms": latency_ms,
                "usage": usage, "estimated_cost_usd": _cost(usage, input_rate, output_rate),
                "raw_structured_result": raw, "error_code": None}
    except Exception as exc:
        return {"status": "failed", "model_reported": None, "response_id": None,
                "latency_ms": round((time.perf_counter_ns() - started) / 1_000_000, 3),
                "usage": {"input_tokens": None, "output_tokens": None, "total_tokens": None},
                "estimated_cost_usd": None, "raw_structured_result": None,
                "error_code": type(exc).__name__}


def write_result(result: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> tuple[dict[str, Any], Path]:
    corpus = load_corpus(CORPUS_FILE)
    digest = verify_manifest()
    splits = {"evaluation", "holdout"} if args.include_holdout else {"evaluation"}
    selected = select_cases(corpus, splits=splits, case_ids=set(args.case or []),
                            categories=set(args.category or []), limit=args.limit)
    selection = {"splits": sorted(splits), "case_ids": [row["id"] for row in selected],
                 "categories": sorted(set(args.category or [])), "case_count": len(selected)}
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_model = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in (args.model or "unconfigured"))
    output = Path(args.output) if args.output else RESULTS_DIR / f"{timestamp}-{safe_model}.json"
    if not args.model:
        result = result_envelope(status="blocked", model="unconfigured", corpus=corpus,
            corpus_digest=digest, selection=selection, repeats=args.repeats,
            reason={"code": "missing_model_configuration", "message": "Set OPENAI_MODEL or pass --model."})
        write_result(result, output); write_result(result, RESULTS_DIR / "latest.json")
        return result, output
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        result = result_envelope(status="blocked", model=args.model, corpus=corpus,
            corpus_digest=digest, selection=selection, repeats=args.repeats,
            reason={"code": "missing_api_key", "message": "OPENAI_API_KEY is not configured; no model calls were made."})
        write_result(result, output); write_result(result, RESULTS_DIR / "latest.json")
        return result, output
    from openai import OpenAI
    data = load_dataset(DATA_DIR)
    descriptions = load_destination_descriptions(DESCRIPTION_FILE)
    instructions = catalog_prompt(data, descriptions)
    schema = extraction_schema(data)
    client = OpenAI(api_key=api_key)
    raw_runs = []
    for row in selected:
        for repeat in range(1, args.repeats + 1):
            raw_runs.append({"case_id": row["id"], "repeat": repeat,
                             **call_model(client, model=args.model, text=row["input_text"],
                                          instructions=instructions, schema=schema,
                                          input_rate=args.input_cost_per_million,
                                          output_rate=args.output_cost_per_million)})
    metrics = evaluate_runs(corpus, raw_runs, data, model=args.model)
    reported = sorted({row["model_reported"] for row in raw_runs if row["model_reported"]})
    result = result_envelope(status="completed", model=args.model, corpus=corpus,
                             corpus_digest=digest, selection=selection, repeats=args.repeats,
                             metrics=metrics)
    result["model_reported_versions"] = reported
    result["pricing_assumption_usd_per_million_tokens"] = {
        "input": args.input_cost_per_million, "output": args.output_cost_per_million,
        "source": "explicit CLI values; null means cost was not estimated"}
    write_result(result, output); write_result(result, RESULTS_DIR / "latest.json")
    return result, output


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model", default=os.environ.get("OPENAI_MODEL"))
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--case", action="append", help="case id; repeat to select several")
    p.add_argument("--category", action="append", help="select any matching category")
    p.add_argument("--limit", type=int)
    p.add_argument("--include-holdout", action="store_true")
    p.add_argument("--input-cost-per-million", type=float)
    p.add_argument("--output-cost-per-million", type=float)
    p.add_argument("--output")
    return p


def main() -> int:
    args = parser().parse_args()
    if args.repeats < 1 or args.repeats > 20 or (args.limit is not None and args.limit < 1):
        print("repeats must be 1-20 and limit must be positive", file=sys.stderr)
        return 2
    try:
        result, output = run(args)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"M8 evaluation failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": result["status"], "output": str(output),
                      "blocking_reason": result["blocking_reason"]}, ensure_ascii=False))
    return 0 if result["status"] in {"completed", "blocked"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
