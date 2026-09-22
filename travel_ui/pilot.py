"""Privacy-minimal pilot records, independent from Streamlit and storage."""

from __future__ import annotations

import json
from uuid import uuid4

from benchmark_tools.validation import JsonObject


PILOT_RECORD_VERSION = "pilot-record-v1"
RESPONSE_OPTIONS = {"yes", "no", "unsure"}


def create_session_id() -> str:
    """Create an anonymous identifier for one browser session."""
    return f"pilot-{uuid4().hex[:12]}"


def build_product_telemetry(*, session_id: str, scenario_id: str,
                            result: JsonObject, cards: list[JsonObject],
                            completion_seconds: float) -> JsonObject:
    """Capture observable product behavior without personal information."""
    alternatives = [{
        "rank": card["rank"],
        "itinerary_id": card["itinerary_id"],
        "category": card["label"],
        "route": card["route"],
        "flight_cost": card["flight_cost"],
    } for card in cards]
    return {
        "session_id": session_id,
        "scenario_id": scenario_id,
        "result_status": result["status"],
        "feasible_result_produced": result["status"] == "ok" and bool(cards),
        "recommended_alternative_count": len(alternatives),
        "recommended_alternatives_shown": alternatives,
        "task_completion_seconds": max(0, round(completion_seconds)),
        "dataset_version": result.get("dataset_version"),
        "scoring_version": result.get("scoring_version"),
        "search_version": result.get("search_config", {}).get("search_version"),
    }


def build_subjective_feedback(*, selected_itinerary_id: str | None,
                              comprehension_rating: int, trust_rating: int,
                              usefulness_rating: int,
                              discovered_non_obvious_route: str,
                              would_consider_selected_route: str,
                              unclear_point: str, missing_information: str) -> JsonObject:
    """Capture the participant's judgments separately from telemetry."""
    ratings = (comprehension_rating, trust_rating, usefulness_rating)
    if any(isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5
           for value in ratings):
        raise ValueError("ratings must be integers from 1 to 5")
    if discovered_non_obvious_route not in RESPONSE_OPTIONS:
        raise ValueError("invalid discovery response")
    if would_consider_selected_route not in RESPONSE_OPTIONS:
        raise ValueError("invalid consideration response")
    return {
        "selected_itinerary_id": selected_itinerary_id,
        "comprehension_rating_1_to_5": comprehension_rating,
        "trust_rating_1_to_5": trust_rating,
        "usefulness_rating_1_to_5": usefulness_rating,
        "discovered_non_obvious_route": discovered_non_obvious_route,
        "would_consider_selected_route": would_consider_selected_route,
        "unclear_point": unclear_point.strip(),
        "missing_information": missing_information.strip(),
    }


def build_pilot_record(product_telemetry: JsonObject,
                       subjective_feedback: JsonObject) -> JsonObject:
    return {
        "record_version": PILOT_RECORD_VERSION,
        "product_telemetry": product_telemetry,
        "subjective_feedback": subjective_feedback,
    }


def pilot_record_json(record: JsonObject) -> str:
    return json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
