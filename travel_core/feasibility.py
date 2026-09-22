"""Independent route constraints and local-time accounting for the engine.

Only input schemas and pinned time-zone parsing are shared with M1. The M1
witness evaluator is not called here, so it can independently check output.
"""

from datetime import date, datetime, time, timedelta, timezone

from benchmark_tools.validation import Dataset, JsonObject, instant, zone

UTC = timezone.utc


def elapsed_minutes(start: datetime, end: datetime) -> int:
    return int((end.astimezone(UTC) - start.astimezone(UTC)).total_seconds() / 60)


def occupied_dates(start: datetime, end: datetime, timezone_name: str) -> tuple[str, ...]:
    tz = zone(timezone_name)
    first = start.astimezone(tz).date()
    last = (end.astimezone(UTC) - timedelta(microseconds=1)).astimezone(tz).date()
    return tuple((first + timedelta(days=n)).isoformat() for n in range((last - first).days + 1))


def daily_minutes(start: datetime, end: datetime, timezone_name: str,
                  start_time: str, end_time: str) -> int:
    if start.astimezone(UTC) >= end.astimezone(UTC):
        return 0
    tz = zone(timezone_name)
    first = start.astimezone(tz).date()
    last = end.astimezone(tz).date()
    total = 0
    for offset in range((last - first).days + 1):
        day = first + timedelta(days=offset)
        lower = datetime.combine(day, time.fromisoformat(start_time), tz).astimezone(UTC)
        upper = datetime.combine(day, time.fromisoformat(end_time), tz).astimezone(UTC)
        overlap_start = max(lower, start.astimezone(UTC))
        overlap_end = min(upper, end.astimezone(UTC))
        if overlap_end > overlap_start:
            total += elapsed_minutes(overlap_start, overlap_end)
    return total


def arrival_at(offer: JsonObject, access: JsonObject, data: Dataset) -> datetime:
    tz = zone(data.destinations[access["destination_id"]]["timezone"])
    return (instant(offer["arrival_at"]).astimezone(UTC)
            + timedelta(minutes=access["to_destination_minutes"])).astimezone(tz)


def build_visit(inbound: JsonObject, outbound: JsonObject, access: JsonObject,
                data: Dataset) -> tuple[JsonObject, tuple[str, ...]]:
    destination = data.destinations[access["destination_id"]]
    a = arrival_at(inbound, access, data)
    b = (instant(outbound["departure_at"]).astimezone(UTC)
         - timedelta(minutes=access["to_airport_minutes"] + data.scoring["airport_buffer_minutes"]))
    b = b.astimezone(zone(destination["timezone"]))
    nights = (b.date() - a.date()).days
    usable = daily_minutes(a, b, destination["timezone"],
                           data.scoring["usable_day_start_local"], data.scoring["usable_day_end_local"])
    failures = []
    if inbound["destination_airport_id"] != access["airport_id"] or outbound["origin_airport_id"] != access["airport_id"]:
        failures.append("visit_airport")
    if b.astimezone(UTC) <= a.astimezone(UTC):
        failures.append("visit_chronology")
    if not destination["min_stay_nights"] <= nights <= destination["max_stay_nights"]:
        failures.append("stay_nights")
    if usable < destination["min_usable_minutes"]:
        failures.append("usable_minutes")
    return {
        "destination_id": destination["id"], "access_id": access["id"],
        "inbound_offer_id": inbound["id"], "outbound_offer_id": outbound["id"],
        "arrival_at": a.isoformat(), "departure_at": b.isoformat(),
        "stay_nights": nights, "usable_minutes": usable,
    }, tuple(failures)


def offer_errors(offer: JsonObject, request: JsonObject) -> tuple[str, ...]:
    failures = []
    ref = zone(request["reference_timezone"])
    first = instant(offer["departure_at"]).astimezone(ref).date()
    last = instant(offer["arrival_at"]).astimezone(ref).date()
    if first < date.fromisoformat(request["window_start_date"]) or last > date.fromisoformat(request["window_end_date"]):
        failures.append("date_window")
    if offer["connection_count"] > request["max_connections_per_offer"]:
        failures.append("connection_limit")
    return tuple(failures)


def check_itinerary(offer_ids: tuple[str, ...], access_ids: tuple[str, ...],
                    request: JsonObject, data: Dataset) -> tuple[tuple[JsonObject, ...], tuple[str, ...]]:
    """Full-path recheck independent of incremental search state."""
    if len(offer_ids) != len(access_ids) + 1 or not access_ids:
        return (), ("visit_count",)
    if not set(offer_ids) <= data.offers.keys() or not set(access_ids) <= data.accesses.keys():
        return (), ("unknown_reference",)
    offers = [data.offers[i] for i in offer_ids]
    accesses = [data.accesses[i] for i in access_ids]
    failures: set[str] = set()
    if len(set(offer_ids)) != len(offer_ids):
        failures.add("duplicate_offer")
    if offers[0]["origin_airport_id"] not in request["origin_airport_ids"]:
        failures.add("origin_airport")
    if offers[-1]["destination_airport_id"] not in request["return_airport_ids"]:
        failures.add("return_airport")
    for offer in offers:
        failures.update(offer_errors(offer, request))
    tz = zone(request["reference_timezone"])
    days = (instant(offers[-1]["arrival_at"]).astimezone(tz).date()
            - instant(offers[0]["departure_at"]).astimezone(tz).date()).days + 1
    if not request["min_trip_days"] <= days <= request["max_trip_days"]:
        failures.add("trip_duration")
    if sum(o["price_minor"] for o in offers) > request["flight_budget_minor"]:
        failures.add("flight_budget")
    visited = [a["destination_id"] for a in accesses]
    if len(set(visited)) != len(visited):
        failures.add("duplicate_destination")
    if not set(request["required_destination_ids"]) <= set(visited):
        failures.add("required_missing")
    if len(set(visited) - set(request["required_destination_ids"])) > request["max_optional_destinations"]:
        failures.add("optional_limit")
    visits = []
    for index, access in enumerate(accesses):
        visit, errors = build_visit(offers[index], offers[index + 1], access, data)
        visits.append(visit)
        failures.update(errors)
    return tuple(visits), tuple(sorted(failures))
