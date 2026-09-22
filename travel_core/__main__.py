"""File-based core invocation, without a UI or service."""

import argparse
import json
from pathlib import Path
import sys

from benchmark_tools.validation import ValidationError, load_dataset, read_json

from .search import search


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path, help="TripRequest JSON file")
    parser.add_argument("--data", type=Path, default=Path(__file__).resolve().parents[1] / "data/m1")
    args = parser.parse_args()
    try:
        result = search(read_json(args.request), load_dataset(args.data))
        print(json.dumps(result, sort_keys=True, ensure_ascii=True, indent=2))
        return 0 if result["search_complete"] else 1
    except (ValidationError, OSError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
