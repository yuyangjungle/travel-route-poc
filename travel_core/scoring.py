"""Deterministic scoring from complete flight and visit records."""

import hashlib
import json
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP, localcontext

from benchmark_tools.validation import Dataset, JsonObject, instant, zone

from .feasibility import daily_minutes, elapsed_minutes, occupied_dates
from .models import SEARCH_CONFIG


def fixed(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def score_itinerary(offer_ids: tuple[str, ...], visits: tuple[JsonObject, ...],
                    request: JsonObject, data: Dataset) -> JsonObject:
    """Input is a feasible route, checked by check_itinerary."""
    offers = [data.offers[i] for i in offer_ids]
    accesses = [data.accesses[v["access_id"]] for v in visits]
    cost = sum(o["price_minor"] for o in offers)
    ground = sum(a["to_destination_cost_minor"] + a["to_airport_cost_minor"] for a in accesses)
    transit = sum(elapsed_minutes(instant(o["departure_at"]), instant(o["arrival_at"])) for o in offers)
    transit += sum(a["to_destination_minutes"] + a["to_airport_minutes"] for a in accesses)
    reference = request["reference_timezone"]
    flight_days: set[str] = set()
    night = 0
    config = data.scoring
    for offer in offers:
        for segment in offer["segments"]:
            a, b = instant(segment["departure_at"]), instant(segment["arrival_at"])
            flight_days.update(occupied_dates(a, b, reference))
            night += daily_minutes(a, b, reference, config["night_start_reference"], config["night_end_reference"])
    connections = sum(o["connection_count"] for o in offers)
    scored_visits = []
    with localcontext() as context:
        context.prec = 40
        total_experience = Decimal(0)
        for visit in visits:
            destination = data.destinations[visit["destination_id"]]
            weights = request["preference_weights"]
            weight_sum = sum(weights.values())
            match = Decimal(50) if not weight_sum else (
                Decimal(sum(weights[tag] * destination["tag_scores"][tag] for tag in sorted(weights))) / weight_sum)
            if destination["id"] in request["preferred_destination_ids"]:
                match = min(Decimal(100), match + config["preferred_bonus_points"])
            stay = min(Decimal(1), Decimal(visit["stay_nights"]) / destination["recommended_stay_nights"])
            month = str(datetime.fromisoformat(visit["arrival_at"]).month)
            value = match * stay * Decimal(destination["season_scores_by_month"][month]) / 100
            total_experience += value
            scored_visits.append({**visit, "experience_points": fixed(value)})
        coefficients = {k: Decimal(str(v)) for k, v in config["burden_coefficients"].items()}
        burden = (Decimal(transit) / 60 * coefficients["transit_hour"]
                  + Decimal(len(flight_days)) * coefficients["flight_day"]
                  + Decimal(connections) * coefficients["connection"]
                  + Decimal(night) / 60 * coefficients["night_hour"])
        scale = {k: Decimal(str(v)) for k, v in config["normalization_scales"].items()}
        weight = {k: Decimal(str(v)) for k, v in config["ranking_weights"].items()}
        combined = (100 / (1 + Decimal(cost) / scale["cost_minor"]) * weight["cost"]
                    + 100 / (1 + burden / scale["burden"]) * weight["comfort"]
                    + 100 * total_experience / (total_experience + scale["experience"]) * weight["experience"])
        b_value, e_value, recommendation = fixed(burden), fixed(total_experience), fixed(combined)
    signature = {
        "dataset_id": data.manifest["dataset_id"], "dataset_version": data.manifest["dataset_version"],
        "schema_version": data.manifest["schema_version"], "scoring_version": config["scoring_version"],
        "search_version": SEARCH_CONFIG["search_version"], "offer_ids": offer_ids,
        "access_ids": [v["access_id"] for v in visits],
    }
    identifier = hashlib.sha256(json.dumps(signature, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    ref = zone(reference)
    return {
        "id": "it-" + identifier, "origin_airport_id": offers[0]["origin_airport_id"],
        "return_airport_id": offers[-1]["destination_airport_id"], "offer_ids": list(offer_ids),
        "visits": scored_visits, "first_departure_at": offers[0]["departure_at"],
        "last_arrival_at": offers[-1]["arrival_at"],
        "trip_days": (instant(offers[-1]["arrival_at"]).astimezone(ref).date()
                      - instant(offers[0]["departure_at"]).astimezone(ref).date()).days + 1,
        "flight_total_minor": cost, "ground_total_minor": ground, "transit_minutes": transit,
        "flight_dates": sorted(flight_days), "flight_day_count": len(flight_days),
        "connection_count": connections, "night_flight_minutes": night,
        "burden_points": b_value, "experience_points": e_value, "preference_match_points": e_value,
        "recommendation_score": recommendation, "recommendation_labels": [], "tradeoffs": [],
    }
