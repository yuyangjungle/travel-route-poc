"""Load M6 decision scenarios and display-only destination descriptions."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from benchmark_tools.validation import JsonObject, read_json, require


SCENARIO_FIELDS = {
    "schema_version", "id", "title", "traveler_profile", "expected_user_intent",
    "decision_priority", "must_have_constraints", "dataset_path", "offer_set_path", "request",
}
DESCRIPTION_FIELDS = {
    "id", "display_name_zh", "destination_type", "suitable_activities",
    "seasonal_note", "transport_difficulty", "transport_note",
}


def load_m6_scenarios(directory: Path, project_root: Path) -> list[JsonObject]:
    scenarios: list[JsonObject] = []
    ids: set[str] = set()
    for path in sorted(directory.glob("*.json")):
        scenario = read_json(path)
        require(set(scenario) == SCENARIO_FIELDS, str(path),
                "unexpected or missing scenario fields", "invalid_dataset")
        require(scenario["schema_version"] == "m6-scenario-v1", str(path),
                "unsupported scenario schema", "invalid_dataset")
        require(scenario["id"] not in ids, str(path), "duplicate scenario ID", "invalid_dataset")
        ids.add(scenario["id"])
        for field in ("id", "title", "traveler_profile", "expected_user_intent",
                      "decision_priority", "dataset_path", "offer_set_path"):
            require(isinstance(scenario[field], str) and bool(scenario[field]),
                    f"{path}.{field}", "nonempty text required", "invalid_dataset")
        require(isinstance(scenario["must_have_constraints"], list)
                and scenario["must_have_constraints"]
                and all(isinstance(value, str) and value for value in scenario["must_have_constraints"]),
                str(path), "must-have constraints required", "invalid_dataset")
        require(isinstance(scenario["request"], dict), str(path),
                "request object required", "invalid_dataset")
        offer_set = read_json(project_root / scenario["offer_set_path"])
        require(set(offer_set) == {"offer_set_id", "dataset_id", "selection_note", "offer_ids"},
                scenario["offer_set_path"], "invalid offer set", "invalid_dataset")
        require(isinstance(offer_set["offer_ids"], list) and offer_set["offer_ids"]
                and len(offer_set["offer_ids"]) == len(set(offer_set["offer_ids"]))
                and all(isinstance(value, str) and value for value in offer_set["offer_ids"]),
                scenario["offer_set_path"], "unique offer IDs required", "invalid_dataset")
        scenario = deepcopy(scenario)
        scenario.update(
            scenario_kind="m6", source_scenario=offer_set["offer_set_id"],
            offer_ids=offer_set["offer_ids"], description=scenario["expected_user_intent"],
        )
        scenarios.append(scenario)
    require(len(scenarios) >= 4, str(directory), "at least four M6 scenarios required", "invalid_dataset")
    return scenarios


def load_destination_descriptions(path: Path) -> dict[str, JsonObject]:
    payload = read_json(path)
    require(set(payload) == {"schema_version", "data_status", "separation_note", "destinations"},
            str(path), "invalid description catalog", "invalid_dataset")
    require(payload["schema_version"] == "m6-destination-description-v1"
            and payload["data_status"] == "simulated_descriptive_metadata",
            str(path), "unsupported description catalog", "invalid_dataset")
    result: dict[str, JsonObject] = {}
    for row in payload["destinations"]:
        require(set(row) == DESCRIPTION_FIELDS, str(path),
                "invalid destination description", "invalid_dataset")
        require(row["id"] not in result, str(path), "duplicate destination description", "invalid_dataset")
        require(isinstance(row["suitable_activities"], list) and row["suitable_activities"],
                row["id"], "activities required", "invalid_dataset")
        result[row["id"]] = row
    return result
