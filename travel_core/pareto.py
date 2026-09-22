"""Exact pairwise Pareto comparison, followed by deterministic selection."""

from decimal import Decimal

from benchmark_tools.validation import JsonObject


def route_key(route: JsonObject) -> tuple:
    return tuple(route["offer_ids"]), tuple(v["access_id"] for v in route["visits"])


def tie_key(route: JsonObject) -> tuple:
    return (route["flight_total_minor"], Decimal(route["burden_points"]),
            -Decimal(route["experience_points"]), *route_key(route))


def dominates(left: JsonObject, right: JsonObject) -> bool:
    a = (left["flight_total_minor"], Decimal(left["burden_points"]), -Decimal(left["experience_points"]))
    b = (right["flight_total_minor"], Decimal(right["burden_points"]), -Decimal(right["experience_points"]))
    return all(x <= y for x, y in zip(a, b)) and any(x < y for x, y in zip(a, b))


def pareto_filter(routes: list[JsonObject]) -> list[JsonObject]:
    # Duplicate path IDs are not independent alternatives. Equal objective values
    # on different routes remain non-dominated because dominance must be strict.
    unique = {route_key(r): r for r in sorted(routes, key=tie_key)}
    pool = list(unique.values())
    return sorted([r for r in pool if not any(dominates(other, r) for other in pool)], key=tie_key)


def select_recommendations(front: list[JsonObject]) -> list[JsonObject]:
    if not front:
        return []
    selected: dict[str, JsonObject] = {}
    selectors = (
        ("cheapest", lambda r: (r["flight_total_minor"], tie_key(r))),
        ("least_exhausting", lambda r: (Decimal(r["burden_points"]), tie_key(r))),
        ("strongest_preference_match", lambda r: (-Decimal(r["experience_points"]), tie_key(r))),
        ("best_overall", lambda r: (-Decimal(r["recommendation_score"]), tie_key(r))),
    )
    for label, key in selectors:
        best = min(front, key=key)
        item = selected.setdefault(best["id"], {**best, "recommendation_labels": [], "tradeoffs": []})
        item["recommendation_labels"].append(label)
    ranked = sorted(front, key=lambda r: (-Decimal(r["recommendation_score"]), tie_key(r)))
    orders = {tuple(v["destination_id"] for v in r["visits"]) for r in selected.values()}
    for route in ranked:
        order = tuple(v["destination_id"] for v in route["visits"])
        if len(selected) >= 5:
            break
        if route["id"] not in selected and order not in orders:
            selected[route["id"]] = {**route, "recommendation_labels": [], "tradeoffs": []}
            orders.add(order)
    # Preserve extremes and fill up to three, even if only dates differ.
    for route in ranked:
        if len(selected) >= min(3, len(front)):
            break
        selected.setdefault(route["id"], {**route, "recommendation_labels": [], "tradeoffs": []})
    baseline = ranked[0]
    for route in selected.values():
        if route["id"] != baseline["id"]:
            route["tradeoffs"] = [{
                "relative_to_itinerary_id": baseline["id"], "baseline_label": "best_overall",
                "flight_cost_delta_minor": route["flight_total_minor"] - baseline["flight_total_minor"],
                "transit_minutes_delta": route["transit_minutes"] - baseline["transit_minutes"],
                "flight_days_delta": route["flight_day_count"] - baseline["flight_day_count"],
            }]
    return sorted(selected.values(), key=lambda r: (-Decimal(r["recommendation_score"]), tie_key(r)))
