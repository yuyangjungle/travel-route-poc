"""Verify the M8-001 corpus and application boundary before real-model calls."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable

from benchmark_tools.validation import load_dataset
from travel_ui.natural_language import catalog_prompt, extraction_schema
from travel_ui.scenario_library import load_destination_descriptions

from .evaluation import CORPUS_FILE, DATA_DIR, ROOT


SNAPSHOT_FILE = Path(__file__).with_name("frozen-boundary-v1.json")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bundle(paths: Iterable[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.as_posix()):
        digest.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def current_hashes() -> dict[str, str]:
    data = load_dataset(DATA_DIR)
    descriptions = load_destination_descriptions(
        ROOT / "data" / "m6" / "destination_descriptions.json"
    )
    prompt = catalog_prompt(data, descriptions).encode("utf-8")
    schema = json.dumps(extraction_schema(data), ensure_ascii=False, sort_keys=True,
                        separators=(",", ":")).encode("utf-8")
    return {
        "corpus_sha256": _sha256(CORPUS_FILE),
        "prompt_sha256": hashlib.sha256(prompt).hexdigest(),
        "schema_sha256": hashlib.sha256(schema).hexdigest(),
        "natural_language_boundary_sha256": _sha256(ROOT / "travel_ui" / "natural_language.py"),
        "evaluation_code_sha256": _sha256(Path(__file__).with_name("evaluation.py")),
        "optimizer_bundle_sha256": _bundle((ROOT / "travel_core").glob("*.py")),
        "synthetic_data_bundle_sha256": _bundle((ROOT / "data" / "m5").glob("*.json")),
    }


def verify_frozen_boundary() -> dict[str, object]:
    snapshot = json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))
    current = current_hashes()
    differences = {
        name: {"expected": expected, "actual": current.get(name)}
        for name, expected in snapshot["hashes"].items()
        if current.get(name) != expected
    }
    if differences:
        raise ValueError(f"frozen M8 boundary mismatch: {json.dumps(differences, sort_keys=True)}")
    return snapshot
