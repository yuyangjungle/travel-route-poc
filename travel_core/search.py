"""Exhaustive deterministic DFS. No beam, dominance pruning or time limits."""

from collections import Counter
from copy import deepcopy
import hashlib
import json
from typing import Iterable

from benchmark_tools.validation import (Dataset, JsonObject, ValidationError, instant,
                                        validate_dataset, validate_request)

from .feasibility import arrival_at, build_visit, check_itinerary, offer_errors
from .models import ItineraryState, SEARCH_CONFIG
from .pareto import pareto_filter, select_recommendations, tie_key
from .scoring import score_itinerary


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=True).encode()).hexdigest()


def search(request: JsonObject, data: Dataset,
           allowed_offer_ids: Iterable[str] | None = None) -> JsonObject:
    """Search only structured data; never accepts witnesses or benchmark answers.

    All feasible routes and the full Pareto set are included for M2 auditing.
    'itineraries' contains only the deterministic representative selection.
    """
    result: JsonObject = {
        "status": "invalid_dataset", "request": None, "dataset_id": None,
        "dataset_version": None, "schema_version": None, "scoring_version": None,
        "search_config": deepcopy(SEARCH_CONFIG), "search_complete": False,
        "optimality_scope": "none", "itineraries": [], "feasible_itineraries": [],
        "pareto_itineraries": [], "coverage_warnings": [], "diagnostics": {},
        "run_summary": {"expanded_states": 0, "feasible_count": 0, "pareto_count": 0, "elapsed_ms": None},
        "data_fingerprint": None, "allowed_offer_ids": [],
    }
    try:
        validate_dataset(data)
    except (ValidationError, KeyError, TypeError, ValueError) as exc:
        result["diagnostics"] = {"message": str(exc)}
        return result
    m = data.manifest
    result.update(dataset_id=m["dataset_id"], dataset_version=m["dataset_version"],
                  schema_version=m["schema_version"], scoring_version=data.scoring["scoring_version"],
                  coverage_warnings=list(m["coverage_notes"]))
    try:
        normalized = deepcopy(request)
        if isinstance(normalized, dict) and "return_airport_ids" not in normalized and "origin_airport_ids" in normalized:
            normalized["return_airport_ids"] = deepcopy(normalized["origin_airport_ids"])
        validate_request(normalized, data)
        for field in ("origin_airport_ids", "return_airport_ids", "required_destination_ids", "preferred_destination_ids"):
            normalized[field] = sorted(normalized[field])
        if isinstance(allowed_offer_ids, (str, bytes)):
            raise ValidationError("invalid_request", "allowed_offer_ids", "expected collection of offer IDs")
        ids = list(data.offers) if allowed_offer_ids is None else list(allowed_offer_ids)
        if not all(isinstance(i, str) for i in ids) or not set(ids) <= data.offers.keys():
            raise ValidationError("invalid_request", "allowed_offer_ids", "unknown offer ID")
        ids = sorted(set(ids))
    except ValidationError as exc:
        result.update(status=exc.code, diagnostics={"path": exc.path, "message": str(exc)})
        return result
    except (KeyError, TypeError, ValueError) as exc:
        result.update(status="invalid_request", diagnostics={"message": str(exc)})
        return result
    request = normalized
    result["request"] = request
    result["allowed_offer_ids"] = ids
    result["data_fingerprint"] = fingerprint({
        "manifest": m, "airports": data.airports, "destinations": data.destinations,
        "accesses": data.accesses, "offers": {i: data.offers[i] for i in ids}, "scoring": data.scoring,
    })
    if set(ids) != data.offers.keys():
        result["coverage_warnings"].append("Search is restricted to the explicitly listed offer subset.")
    offers = sorted((data.offers[i] for i in ids), key=lambda o: (instant(o["departure_at"]), o["id"]))
    accesses = sorted(data.accesses.values(), key=lambda a: (a["destination_id"], a["id"]))
    required = frozenset(request["required_destination_ids"])
    rejections: Counter[str] = Counter()
    feasible: list[JsonObject] = []
    expanded = 0

    def enter(offer: JsonObject, access: JsonObject, parent: ItineraryState | None) -> ItineraryState | None:
        destination = access["destination_id"]
        visited = parent.visited_destination_ids if parent else frozenset()
        if destination in visited:
            rejections["duplicate_destination"] += 1
            return None
        optional = (parent.optional_count if parent else 0) + (destination not in required)
        if optional > request["max_optional_destinations"]:
            rejections["optional_limit"] += 1
            return None
        visited = visited | {destination}
        return ItineraryState(
            current_destination_id=destination, access_id=access["id"],
            destination_arrival_at=arrival_at(offer, access, data),
            first_departure_at=parent.first_departure_at if parent else instant(offer["departure_at"]),
            visited_destination_ids=frozenset(visited), remaining_required_ids=required - visited,
            optional_count=optional, flight_total_minor=(parent.flight_total_minor if parent else 0) + offer["price_minor"],
            offer_ids=(parent.offer_ids if parent else ()) + (offer["id"],),
            access_ids=(parent.access_ids if parent else ()) + (access["id"],),
        )

    def explore(state: ItineraryState) -> None:
        nonlocal expanded
        expanded += 1
        inbound = data.offers[state.offer_ids[-1]]
        access = data.accesses[state.access_id]
        # Intentionally scan all quotes: correctness before indexing or pruning.
        # Each quote implies one departure date and stay duration. Testing all
        # quotes covers all feasible integer nights without a date-index shortcut.
        for outbound in offers:
            if outbound["origin_airport_id"] != access["airport_id"]:
                continue
            errors = offer_errors(outbound, request)
            if errors:
                rejections.update(errors)
                continue
            if state.flight_total_minor + outbound["price_minor"] > request["flight_budget_minor"]:
                rejections["flight_budget"] += 1
                continue
            _, errors = build_visit(inbound, outbound, access, data)
            if errors:
                rejections.update(errors)
                continue
            if outbound["destination_airport_id"] in request["return_airport_ids"]:
                route_offers = state.offer_ids + (outbound["id"],)
                visits, errors = check_itinerary(route_offers, state.access_ids, request, data)
                if errors:
                    rejections.update(errors)
                else:
                    feasible.append(score_itinerary(route_offers, visits, request, data))
            for following in accesses:
                if following["airport_id"] == outbound["destination_airport_id"]:
                    child = enter(outbound, following, state)
                    if child is not None:
                        explore(child)

    for outbound in offers:
        if outbound["origin_airport_id"] not in request["origin_airport_ids"]:
            continue
        errors = offer_errors(outbound, request)
        if errors:
            rejections.update(errors)
            continue
        if outbound["price_minor"] > request["flight_budget_minor"]:
            rejections["flight_budget"] += 1
            continue
        for access in accesses:
            if access["airport_id"] == outbound["destination_airport_id"]:
                initial = enter(outbound, access, None)
                if initial is not None:
                    explore(initial)
    feasible.sort(key=tie_key)
    front = pareto_filter(feasible)
    result.update(
        status="ok" if feasible else "no_feasible_in_dataset", search_complete=True,
        optimality_scope="dataset_and_discrete_model", feasible_itineraries=feasible,
        pareto_itineraries=front, itineraries=select_recommendations(front),
        diagnostics={"rejected_extensions": dict(sorted(rejections.items())),
                     "note": "Rejection counts describe observed extensions, not unique causes of infeasibility."},
        run_summary={"expanded_states": expanded, "feasible_count": len(feasible), "pareto_count": len(front), "elapsed_ms": None},
    )
    return result
