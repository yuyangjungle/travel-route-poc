"""Execute the frozen 120-case M8-002 evaluation with auditable retries."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sys
import time
from typing import Any

from benchmark_tools.validation import load_dataset
from travel_ui.natural_language import (NaturalLanguageInputError, catalog_prompt,
                                        extraction_schema, validate_extraction)
from travel_ui.scenario_library import load_destination_descriptions

from .evaluation import CORPUS_FILE, DATA_DIR, ROOT, load_corpus, verify_manifest
from .frozen import SNAPSHOT_FILE, verify_frozen_boundary
from .reporting import write_reports


RUNNER_VERSION = "m8-real-runner-v1"
RUNS_DIR = Path(__file__).with_name("runs")
PROVIDERS = {
    "openai": {"base_url": None, "api_key_env": "OPENAI_API_KEY"},
    "deepseek": {"base_url": "https://api.deepseek.com", "api_key_env": "DEEPSEEK_API_KEY"},
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def safe_model_name(model: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in model)


def selected_cases() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    corpus = load_corpus(CORPUS_FILE)
    cases = [case for case in corpus["cases"] if case["split"] == "evaluation"]
    if len(cases) != 120 or any(case["split"] == "holdout" for case in cases):
        raise ValueError("M8-002 requires exactly 120 evaluation cases and forbids holdout")
    return corpus, cases


def create_manifest(*, provider: str, model: str, repeats: int, input_rate: float,
                    output_rate: float, key_configured: bool,
                    cases: list[dict[str, Any]]) -> dict[str, Any]:
    snapshot = verify_frozen_boundary()
    digest = verify_manifest()
    started = utc_now()
    run_id = (datetime.now(timezone.utc).strftime("m8-002-%Y%m%dT%H%M%SZ-")
              + safe_model_name(provider) + "-" + safe_model_name(model))
    case_ids = [case["id"] for case in cases]
    return {
        "manifest_version": "m8-002-run-manifest-v1", "run_id": run_id,
        "utc_start_time": started, "status_at_creation": "planned" if key_configured else "blocked_preflight",
        "corpus_version": snapshot["corpus_version"], "corpus_sha256": digest,
        "prompt_contract_version": snapshot["prompt_contract_version"],
        "structured_output_schema_version": snapshot["structured_output_schema_version"],
        "evaluation_code_version": snapshot["evaluation_code_version"],
        "frozen_boundary_version": snapshot["boundary_version"],
        "frozen_boundary_sha256": hashlib.sha256(SNAPSHOT_FILE.read_bytes()).hexdigest(),
        "frozen_hashes": snapshot["hashes"], "runner_version": RUNNER_VERSION,
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "reporting_sha256": hashlib.sha256(Path(__file__).with_name("reporting.py").read_bytes()).hexdigest(),
        "provider": provider, "requested_model": model,
        "openai_sdk_version": importlib.metadata.version("openai"),
        "python_version": platform.python_version(), "repetition_count": repeats,
        "evaluation_subset": {"split": "evaluation", "case_count": len(cases),
                              "case_ids": case_ids,
                              "case_ids_sha256": hashlib.sha256(canonical_bytes(case_ids)).hexdigest(),
                              "holdout_consumed": False},
        "call_order": "repeat-major then corpus order", "expected_call_count": len(cases) * repeats,
        "token_pricing_assumptions": {"currency": "USD", "unit": "per_million_tokens",
                                      "input": input_rate, "output": output_rate,
                                      "source": "explicit CLI values confirmed before the run"},
        "api_configuration": {"api_key_configured": key_configured,
                              "api_key_persisted": False, "store": False,
                              "sdk_automatic_retries": 0, "explicit_max_retries": 2,
                              "tools_supplied": False,
                              "base_url": PROVIDERS[provider]["base_url"] or "OpenAI SDK default",
                              "api_key_environment_variable": PROVIDERS[provider]["api_key_env"]},
    }


def write_immutable_manifest(run_dir: Path, manifest: dict[str, Any]) -> None:
    run_dir.mkdir(parents=True, exist_ok=False)
    payload = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    path = run_dir / "manifest.json"
    path.write_text(payload, encoding="utf-8")
    (run_dir / "manifest.sha256").write_text(
        hashlib.sha256(path.read_bytes()).hexdigest() + "\n", encoding="ascii"
    )


def _usage(response: Any) -> dict[str, int | None]:
    usage = getattr(response, "usage", None)
    return {"input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None)}


def _cost(usage: dict[str, int | None], input_rate: float,
          output_rate: float) -> float | None:
    if usage["input_tokens"] is None or usage["output_tokens"] is None:
        return None
    return round((usage["input_tokens"] * input_rate +
                  usage["output_tokens"] * output_rate) / 1_000_000, 8)


def _error_details(exc: Exception) -> dict[str, Any]:
    return {"type": type(exc).__name__, "status_code": getattr(exc, "status_code", None),
            "code": getattr(exc, "code", None),
            "message_redacted": "Transport/API failure; exception text intentionally not persisted."}


def _retryable(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    return type(exc).__name__ in {"APIConnectionError", "APITimeoutError", "RateLimitError",
                                  "InternalServerError"} or status in {408, 409, 429} or (
                                      isinstance(status, int) and status >= 500)


def execute_call(client: Any, *, case: dict[str, Any], repeat: int, model: str,
                 instructions: str, schema: dict[str, Any], data: Any,
                 input_rate: float, output_rate: float) -> dict[str, Any]:
    attempts = []
    logical_started = time.perf_counter_ns()
    for attempt in range(1, 4):
        attempt_started_at = utc_now()
        started = time.perf_counter_ns()
        try:
            response = client.responses.create(
                model=model, instructions=instructions, input=case["input_text"],
                text={"format": {"type": "json_schema", "name": "travel_request_extraction",
                                 "strict": True, "schema": schema}}, store=False,
            )
            attempt_latency = round((time.perf_counter_ns() - started) / 1_000_000, 3)
            attempts.append({"attempt": attempt, "started_at": attempt_started_at,
                             "completed_at": utc_now(), "status": "response_received",
                             "latency_ms": attempt_latency, "retryable": False, "error": None})
            raw_text = getattr(response, "output_text", None)
            raw = None
            parse_status = "invalid_json"
            validation_status = "not_run"
            validation_error = None
            try:
                raw = json.loads(raw_text) if isinstance(raw_text, str) else None
                parse_status = "parsed" if isinstance(raw, dict) else "invalid_top_level"
            except (json.JSONDecodeError, TypeError):
                pass
            if parse_status == "parsed":
                try:
                    validate_extraction(raw, data, request_id=case["id"], model=model)
                    validation_status = "valid"
                except NaturalLanguageInputError as exc:
                    validation_status = "rejected"
                    validation_error = {"type": type(exc).__name__, "code": exc.code,
                                        "message_redacted": "Local validation rejected structured output."}
            usage = _usage(response)
            return {
                "case_id": case["id"], "repeat": repeat, "requested_model": model,
                "model_reported": getattr(response, "model", None),
                "response_id": getattr(response, "id", None), "response_status": getattr(response, "status", None),
                "status": "completed", "parse_status": parse_status,
                "validation_status": validation_status, "validation_error": validation_error,
                "schema_valid": validation_status == "valid", "raw_structured_result": raw,
                "raw_response_text": raw_text if raw is None else None,
                "usage": usage, "latency_ms": attempt_latency,
                "logical_elapsed_ms": round((time.perf_counter_ns() - logical_started) / 1_000_000, 3),
                "estimated_cost_usd": _cost(usage, input_rate, output_rate),
                "retry_count": attempt - 1, "attempts": attempts, "error": None,
            }
        except Exception as exc:
            latency = round((time.perf_counter_ns() - started) / 1_000_000, 3)
            retryable = _retryable(exc)
            attempts.append({"attempt": attempt, "started_at": attempt_started_at,
                             "completed_at": utc_now(), "status": "api_error",
                             "latency_ms": latency, "retryable": retryable,
                             "error": _error_details(exc)})
            if not retryable or attempt == 3:
                return {"case_id": case["id"], "repeat": repeat, "requested_model": model,
                        "model_reported": None, "response_id": None, "response_status": None,
                        "status": "failed", "parse_status": "not_run",
                        "validation_status": "not_run", "validation_error": None,
                        "schema_valid": False, "raw_structured_result": None,
                        "raw_response_text": None,
                        "usage": {"input_tokens": None, "output_tokens": None, "total_tokens": None},
                        "latency_ms": None,
                        "logical_elapsed_ms": round((time.perf_counter_ns() - logical_started) / 1_000_000, 3),
                        "estimated_cost_usd": None, "retry_count": attempt - 1,
                        "attempts": attempts, "error": _error_details(exc)}
            time.sleep(2 ** (attempt - 1))
    raise AssertionError("unreachable")


def execute(args: argparse.Namespace) -> tuple[Path, str]:
    _, cases = selected_cases()
    provider = PROVIDERS[args.provider]
    api_key = os.environ.get(provider["api_key_env"], "").strip()
    manifest = create_manifest(provider=args.provider, model=args.model, repeats=args.repeats,
                               input_rate=args.input_cost_per_million,
                               output_rate=args.output_cost_per_million,
                               key_configured=bool(api_key), cases=cases)
    run_dir = RUNS_DIR / manifest["run_id"]
    write_immutable_manifest(run_dir, manifest)
    if not api_key:
        status = {"status": "blocked", "at": utc_now(), "reason": {
            "code": "missing_api_key",
            "message": f"{provider['api_key_env']} is not configured; zero API calls made."}}
        (run_dir / "status.json").write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
        return run_dir, "blocked"
    if args.preflight_only:
        status = {"status": "preflight_complete", "at": utc_now(), "reason": None}
        (run_dir / "status.json").write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
        return run_dir, "preflight_complete"
    from openai import OpenAI
    client_options = {"api_key": api_key, "max_retries": 0}
    if provider["base_url"]:
        client_options["base_url"] = provider["base_url"]
    client = OpenAI(**client_options)
    data = load_dataset(DATA_DIR)
    descriptions = load_destination_descriptions(ROOT / "data" / "m6" / "destination_descriptions.json")
    instructions = catalog_prompt(data, descriptions)
    schema = extraction_schema(data)
    calls_path = run_dir / "calls.jsonl"
    with calls_path.open("a", encoding="utf-8", newline="\n") as stream:
        for repeat in range(1, args.repeats + 1):
            for case in cases:
                record = execute_call(client, case=case, repeat=repeat, model=args.model,
                                      instructions=instructions, schema=schema, data=data,
                                      input_rate=args.input_cost_per_million,
                                      output_rate=args.output_cost_per_million)
                stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                stream.flush()
    result_path, failure_path = write_reports(run_dir)
    status = {"status": "completed", "at": utc_now(), "calls_recorded": len(cases) * args.repeats,
              "results": result_path.name, "failures": failure_path.name}
    (run_dir / "status.json").write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    return run_dir, "completed"


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--provider", choices=sorted(PROVIDERS), default="openai")
    p.add_argument("--model", default=os.environ.get("OPENAI_MODEL"), required=False)
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--input-cost-per-million", type=float, required=True)
    p.add_argument("--output-cost-per-million", type=float, required=True)
    p.add_argument("--preflight-only", action="store_true")
    p.add_argument("--score-run", type=Path,
                   help="regenerate results from an existing run without API access")
    return p


def main() -> int:
    args = parser().parse_args()
    if args.score_run:
        try:
            result, failures = write_reports(args.score_run)
        except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
            print(f"M8-002 scoring failed: {exc}", file=sys.stderr)
            return 2
        print(json.dumps({"status": "regenerated", "results": str(result),
                          "failures": str(failures)}))
        return 0
    if not args.model or args.repeats < 3 or args.input_cost_per_million < 0 or args.output_cost_per_million < 0:
        print("model is required; repeats must be >=3; pricing must be non-negative", file=sys.stderr)
        return 2
    try:
        run_dir, status = execute(args)
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"M8-002 preflight/execution failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": status, "run_dir": str(run_dir)}, ensure_ascii=False))
    return 0 if status in {"completed", "preflight_complete", "blocked"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
