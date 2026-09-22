"""Load version-controlled demo presets without coupling them to Streamlit."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from benchmark_tools.validation import JsonObject, read_json, require


def load_demo_cases(directory: Path) -> list[JsonObject]:
    cases = [read_json(path) for path in sorted(directory.glob("*.json"))]
    require(bool(cases), "demos", "no demo cases", "invalid_dataset")
    ids: set[str] = set()
    for case in cases:
        require(set(case) == {"id", "title", "description", "source_scenario", "offer_ids", "request"},
                "demo", "unexpected or missing fields", "invalid_dataset")
        require(case["id"] not in ids, "demo.id", "duplicate demo", "invalid_dataset")
        ids.add(case["id"])
        require(all(isinstance(case[key], str) and case[key] for key in
                    ("id", "title", "description", "source_scenario")),
                "demo", "text fields are required", "invalid_dataset")
        require(isinstance(case["offer_ids"], list), "demo.offer_ids", "list required", "invalid_dataset")
        require(isinstance(case["request"], dict), "demo.request", "object required", "invalid_dataset")
    return cases


def case_by_id(cases: list[JsonObject], case_id: str) -> JsonObject:
    for case in cases:
        if case["id"] == case_id:
            return deepcopy(case)
    raise KeyError(case_id)
