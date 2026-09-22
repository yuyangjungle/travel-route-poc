"""Validate prepared data, requests and explicitly supplied witness itineraries."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP, localcontext
from functools import lru_cache
from importlib import resources
from importlib.metadata import version
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

JsonObject = dict[str, Any]
UTC = timezone.utc


class ValidationError(ValueError):
    """Stable code plus a field path, suitable for CLI and future tests."""

    def __init__(self, code: str, path: str, message: str):
        self.code, self.path = code, path
        super().__init__(f"{code}: {path}: {message}")


def require(condition: bool, path: str, message: str,
            code: str = "invalid_dataset") -> None:
    if not condition:
        raise ValidationError(code, path, message)


def fields(value: Any, names: str, path: str, code: str = "invalid_dataset") -> None:
    require(isinstance(value, dict), path, "expected object", code)
    for name in names.split():
        require(name in value, f"{path}.{name}", "missing field", code)


def integer(value: Any, path: str, low: int = 0, high: int | None = None,
            code: str = "invalid_dataset") -> None:
    require(type(value) is int and value >= low and (high is None or value <= high),
            path, f"expected integer in [{low}, {high}]", code)


def strings(value: Any, path: str, nonempty: bool = False,
            code: str = "invalid_dataset") -> None:
    require(isinstance(value, list) and all(isinstance(x, str) and x for x in value),
            path, "expected string list", code)
    require(len(value) == len(set(value)) and (not nonempty or bool(value)),
            path, "duplicate or empty IDs", code)


def parse_date(value: Any, path: str, code: str = "invalid_dataset") -> date:
    try:
        result = date.fromisoformat(value)
        require(result.isoformat() == value, path, "use YYYY-MM-DD", code)
        return result
    except (ValueError, TypeError) as exc:
        raise ValidationError(code, path, "invalid date") from exc


def instant(value: Any, path: str = "timestamp") -> datetime:
    try:
        result = datetime.fromisoformat(value)
        require(result.tzinfo is not None and result.utcoffset() is not None,
                path, "timezone offset required")
        require(result.second == result.microsecond == 0, path, "minute precision required")
        return result
    except (ValueError, TypeError) as exc:
        raise ValidationError("invalid_dataset", path, "invalid aware timestamp") from exc


@lru_cache(maxsize=None)
def zone(name: str) -> ZoneInfo:
    # Read pinned package data explicitly: never depend on the host OS tz database.
    require(version("tzdata") == "2024.2", "environment.tzdata", "install requirements.txt")
    require(isinstance(name, str) and ".." not in name and "\\" not in name
            and not name.startswith("/"), "timezone", "invalid IANA name")
    try:
        resource = resources.files("tzdata.zoneinfo").joinpath(*name.split("/"))
        with resource.open("rb") as handle:
            return ZoneInfo.from_file(handle, key=name)
    except (OSError, ValueError) as exc:
        raise ValidationError("invalid_dataset", "timezone", "unknown IANA name") from exc


def read_json(path: Path) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> JsonObject:
        result: JsonObject = {}
        for key, value in pairs:
            require(key not in result, str(path), f"duplicate JSON key {key}")
            result[key] = value
        return result

    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique,
                          parse_constant=lambda s: (_ for _ in ()).throw(ValueError(s)))
    except (OSError, ValueError) as exc:
        raise ValidationError("invalid_dataset", str(path), str(exc)) from exc


def indexed(rows: Any, path: str) -> dict[str, JsonObject]:
    require(isinstance(rows, list), path, "expected list")
    result: dict[str, JsonObject] = {}
    for row in rows:
        fields(row, "id", path)
        require(isinstance(row["id"], str) and bool(row["id"]), path, "invalid ID")
        require(row["id"] not in result, path, f"duplicate ID {row['id']}")
        result[row["id"]] = row
    return result


@dataclass(frozen=True)
class Dataset:
    manifest: JsonObject
    airports: dict[str, JsonObject]
    destinations: dict[str, JsonObject]
    accesses: dict[str, JsonObject]
    offers: dict[str, JsonObject]
    scoring: JsonObject


def load_dataset(directory: Path) -> Dataset:
    data = Dataset(
        read_json(directory / "manifest.json"),
        indexed(read_json(directory / "airports.json"), "airports"),
        indexed(read_json(directory / "destinations.json"), "destinations"),
        indexed(read_json(directory / "accesses.json"), "accesses"),
        indexed(read_json(directory / "offers.json"), "offers"),
        read_json(directory / "scoring.json"),
    )
    validate_dataset(data)
    return data


def validate_dataset(data: Dataset) -> None:
    m = data.manifest
    fields(m, "dataset_id dataset_version schema_version created_at currency fare_profile_id "
           "fare_profile_description preference_tag_ids covered_airport_ids "
           "covered_destination_ids coverage_start_date coverage_end_date coverage_kind coverage_notes", "manifest")
    require(m["currency"] == "CNY", "manifest.currency", "CNY only")
    require(m["coverage_kind"] in {"synthetic", "manual_sample", "mixed"}, "manifest.coverage_kind", "unknown kind")
    for key in ("dataset_id", "dataset_version", "schema_version", "fare_profile_id", "fare_profile_description"):
        require(isinstance(m[key], str) and bool(m[key]), f"manifest.{key}", "nonempty string required")
    instant(m["created_at"], "manifest.created_at")
    start = parse_date(m["coverage_start_date"], "manifest.coverage_start_date")
    end = parse_date(m["coverage_end_date"], "manifest.coverage_end_date")
    require(start <= end, "manifest.coverage", "reversed dates")
    strings(m["coverage_notes"], "manifest.coverage_notes", True)
    for key, records in (("covered_airport_ids", data.airports), ("covered_destination_ids", data.destinations)):
        strings(m[key], f"manifest.{key}", True)
        require(set(m[key]) == set(records), f"manifest.{key}", "coverage IDs must equal loaded catalog")
    strings(m["preference_tag_ids"], "manifest.preference_tag_ids", True)
    for a in data.airports.values():
        fields(a, "name timezone", a["id"])
        require(len(a["id"]) == 3 and a["id"].isalpha() and a["id"].isupper(), a["id"], "IATA required")
        zone(a["timezone"])
    for d in data.destinations.values():
        path = d["id"]
        fields(d, "name timezone tag_scores season_scores_by_month min_stay_nights "
               "recommended_stay_nights max_stay_nights min_usable_minutes metadata_source is_mock", path)
        zone(d["timezone"])
        require(isinstance(d["tag_scores"], dict) and set(d["tag_scores"]) == set(m["preference_tag_ids"]), path, "tag dictionary mismatch")
        require(isinstance(d["season_scores_by_month"], dict) and set(d["season_scores_by_month"]) == {str(i) for i in range(1, 13)}, path, "12 months required")
        for val in [*d["tag_scores"].values(), *d["season_scores_by_month"].values()]:
            integer(val, path, 0, 100)
        for key in ("min_stay_nights", "recommended_stay_nights", "max_stay_nights"):
            integer(d[key], f"{path}.{key}", 1)
        require(d["min_stay_nights"] <= d["recommended_stay_nights"] <= d["max_stay_nights"], path, "invalid stay limits")
        integer(d["min_usable_minutes"], path)
        require(type(d["is_mock"]) is bool and bool(d["metadata_source"]), path, "source required")
    for a in data.accesses.values():
        fields(a, "destination_id airport_id to_destination_minutes to_airport_minutes "
               "to_destination_cost_minor to_airport_cost_minor source_note is_mock", a["id"])
        require(a["destination_id"] in data.destinations and a["airport_id"] in data.airports, a["id"], "unknown access endpoint")
        for key in ("to_destination_minutes", "to_airport_minutes", "to_destination_cost_minor", "to_airport_cost_minor"):
            integer(a[key], f"{a['id']}.{key}")
        require(type(a["is_mock"]) is bool and bool(a["source_note"]), a["id"], "source required")
    require({a["destination_id"] for a in data.accesses.values()} == set(data.destinations), "accesses", "destination without access")
    segment_ids: set[str] = set()
    for offer in data.offers.values():
        p = offer["id"]
        fields(offer, "origin_airport_id destination_airport_id departure_at arrival_at segments "
               "price_minor currency fare_profile_id connection_count self_transfer "
               "protected_connection source_type source_ref observed_at", p)
        require(offer["currency"] == m["currency"] and offer["fare_profile_id"] == m["fare_profile_id"], p, "incompatible fare")
        integer(offer["price_minor"], f"{p}.price_minor")
        integer(offer["connection_count"], f"{p}.connection_count")
        require(offer["self_transfer"] is False and type(offer["protected_connection"]) is bool, p, "self transfer not supported")
        require(offer["source_type"] in {"mock", "manual"} and bool(offer["source_ref"]), p, "source required")
        instant(offer["observed_at"], p)
        segs = offer["segments"]
        require(isinstance(segs, list) and len(segs) > 0, p, "segments required")
        require(offer["connection_count"] == len(segs) - 1, p, "connection count mismatch")
        require(len(segs) == 1 or offer["protected_connection"], p, "protected connection required")
        previous = None
        for seg in segs:
            fields(seg, "id origin_airport_id destination_airport_id departure_at arrival_at", p)
            require(isinstance(seg["id"], str) and bool(seg["id"]) and seg["id"] not in segment_ids, p, "duplicate/invalid segment ID")
            segment_ids.add(seg["id"])
            for endpoint, stamp in (("origin_airport_id", "departure_at"), ("destination_airport_id", "arrival_at")):
                require(seg[endpoint] in data.airports, p, "unknown airport")
                dt = instant(seg[stamp], f"{p}.{stamp}")
                require(dt.utcoffset() == dt.astimezone(zone(data.airports[seg[endpoint]]["timezone"])).utcoffset(), p, "airport timezone mismatch")
            require(instant(seg["arrival_at"]) > instant(seg["departure_at"]), p, "nonpositive flight time")
            if previous:
                require(previous["destination_airport_id"] == seg["origin_airport_id"] and
                        instant(previous["arrival_at"]) < instant(seg["departure_at"]), p, "broken connection")
            previous = seg
        for key in ("origin_airport_id", "departure_at"):
            require(offer[key] == segs[0][key], p, "offer/first segment mismatch")
        for key in ("destination_airport_id", "arrival_at"):
            require(offer[key] == segs[-1][key], p, "offer/last segment mismatch")
        require(start <= instant(offer["departure_at"]).astimezone(zone("Asia/Shanghai")).date() <= end, p, "departure outside coverage")
    s = data.scoring
    fields(s, "scoring_version airport_buffer_minutes usable_day_start_local usable_day_end_local "
           "night_start_reference night_end_reference preferred_bonus_points burden_coefficients "
           "normalization_scales ranking_weights score_rounding_digits", "scoring")
    require(s["scoring_version"] == "score-v1", "scoring", "unsupported scoring version")
    for key in ("airport_buffer_minutes", "preferred_bonus_points", "score_rounding_digits"):
        integer(s[key], f"scoring.{key}")
    require(s["score_rounding_digits"] == 6, "scoring", "v1 uses six decimal places")
    for a, b in (("usable_day_start_local", "usable_day_end_local"), ("night_start_reference", "night_end_reference")):
        try:
            require(time.fromisoformat(s[a]) < time.fromisoformat(s[b]), "scoring", "invalid daily interval")
        except (ValueError, TypeError) as exc:
            raise ValidationError("invalid_dataset", "scoring", "invalid daily interval") from exc
    for key, names in (("burden_coefficients", {"transit_hour", "flight_day", "connection", "night_hour"}),
                       ("normalization_scales", {"cost_minor", "burden", "experience"}),
                       ("ranking_weights", {"cost", "comfort", "experience"})):
        require(isinstance(s[key], dict) and set(s[key]) == names, key, "invalid parameter keys")
        for val in s[key].values():
            require(type(val) in (int, float) and Decimal(str(val)).is_finite() and val >= 0, key, "invalid coefficient")
            if key == "normalization_scales":
                require(val > 0, key, "positive scale required")
    require(sum(Decimal(str(v)) for v in s["ranking_weights"].values()) == 1, "ranking_weights", "weights must sum to one")


def validate_request(request: JsonObject, data: Dataset) -> None:
    code = "invalid_request"
    fields(request, "request_id origin_airport_ids return_airport_ids window_start_date window_end_date "
           "reference_timezone min_trip_days max_trip_days flight_budget_minor currency passenger_count "
           "fare_profile_id required_destination_ids preferred_destination_ids preference_weights "
           "max_optional_destinations max_connections_per_offer allow_self_transfer", "request", code)
    for key in ("origin_airport_ids", "return_airport_ids", "required_destination_ids", "preferred_destination_ids"):
        strings(request[key], key, key in {"origin_airport_ids", "return_airport_ids"}, code)
    require(not set(request["required_destination_ids"]) & set(request["preferred_destination_ids"]), "destinations", "required/preferred overlap", code)
    start, end = (parse_date(request[k], k, code) for k in ("window_start_date", "window_end_date"))
    require(start <= end, "window", "reversed window", code)
    for key in ("min_trip_days", "max_trip_days"):
        integer(request[key], key, 7, 21, code)
    require(request["min_trip_days"] <= request["max_trip_days"], "trip_days", "reversed limits", code)
    integer(request["flight_budget_minor"], "flight_budget_minor", 1, code=code)
    integer(request["max_optional_destinations"], "max_optional_destinations", 0, 4, code)
    integer(request["max_connections_per_offer"], "max_connections_per_offer", code=code)
    require(type(request["allow_self_transfer"]) is bool, "allow_self_transfer", "expected boolean", code)
    require(request["allow_self_transfer"] is False, "allow_self_transfer", "M1 does not support self transfer", "unsupported_request")
    require(request["currency"] == "CNY" and type(request["passenger_count"]) is int and request["passenger_count"] == 1
            and request["reference_timezone"] == "Asia/Shanghai", "request", "unsupported price/time profile", "unsupported_request")
    require(request["fare_profile_id"] == data.manifest["fare_profile_id"], "fare_profile_id", "incompatible fare", "unsupported_request")
    require(isinstance(request["preference_weights"], dict), "preference_weights", "expected object", code)
    for tag, val in request["preference_weights"].items():
        require(tag in data.manifest["preference_tag_ids"], "preference_weights", "unknown tag", code)
        integer(val, "preference_weights", 0, 100, code)
    for key, records in (("origin_airport_ids", data.airports), ("return_airport_ids", data.airports),
                         ("required_destination_ids", data.destinations), ("preferred_destination_ids", data.destinations)):
        require(set(request[key]) <= set(records), key, "catalog does not cover request", "insufficient_coverage")
    require(set(request["origin_airport_ids"] + request["return_airport_ids"]) <= {"PVG", "HGH", "NKG"}, "airports", "unsupported home airport", "unsupported_request")
    require(parse_date(data.manifest["coverage_start_date"], "coverage") <= start <= end <=
            parse_date(data.manifest["coverage_end_date"], "coverage"), "window", "outside declared coverage", "insufficient_coverage")


def minutes(start: datetime, end: datetime) -> int:
    return int((end.astimezone(UTC) - start.astimezone(UTC)).total_seconds() // 60)


def daily_overlap(start: datetime, end: datetime, tz: ZoneInfo,
                  lower: str, upper: str) -> int:
    if end <= start:
        return 0
    day = start.astimezone(tz).date()
    total = 0
    while day <= end.astimezone(tz).date():
        a = datetime.combine(day, time.fromisoformat(lower), tz).astimezone(UTC)
        b = datetime.combine(day, time.fromisoformat(upper), tz).astimezone(UTC)
        total += max(0, minutes(max(start.astimezone(UTC), a), min(end.astimezone(UTC), b)))
        day += timedelta(days=1)
    return total


def flight_dates(start: datetime, end: datetime, tz: ZoneInfo) -> set[str]:
    current = start.astimezone(tz).date()
    last = (end.astimezone(UTC) - timedelta(microseconds=1)).astimezone(tz).date()
    result = set()
    while current <= last:
        result.add(current.isoformat())
        current += timedelta(days=1)
    return result


def evaluate_witness(request: JsonObject, witness: JsonObject, data: Dataset,
                     allowed_offer_ids: set[str]) -> JsonObject:
    """Recompute one supplied path. Does not enumerate or select any route."""
    validate_request(request, data)
    fields(witness, "id offer_ids access_ids", "witness", "invalid_witness")
    strings(witness["offer_ids"], "offer_ids", True, "invalid_witness")
    require(isinstance(witness["access_ids"], list) and all(isinstance(x, str) for x in witness["access_ids"]),
            "access_ids", "expected string list", "invalid_witness")
    require(len(witness["offer_ids"]) == len(witness["access_ids"]) + 1 and len(witness["access_ids"]) >= 1,
            "witness", "one visit per gap between offers", "invalid_witness")
    require(set(witness["offer_ids"]) <= allowed_offer_ids <= set(data.offers), "offer_ids", "unknown/out-of-scenario offer", "invalid_witness")
    require(set(witness["access_ids"]) <= set(data.accesses), "access_ids", "unknown access", "invalid_witness")
    offers = [data.offers[x] for x in witness["offer_ids"]]
    accesses = [data.accesses[x] for x in witness["access_ids"]]
    errors: set[str] = set()

    def check(condition: bool, code: str) -> None:
        if not condition:
            errors.add(code)

    ref = zone(request["reference_timezone"])
    first, last = instant(offers[0]["departure_at"]), instant(offers[-1]["arrival_at"])
    trip_days = (last.astimezone(ref).date() - first.astimezone(ref).date()).days + 1
    check(offers[0]["origin_airport_id"] in request["origin_airport_ids"], "origin_airport")
    check(offers[-1]["destination_airport_id"] in request["return_airport_ids"], "return_airport")
    check(parse_date(request["window_start_date"], "window") <= first.astimezone(ref).date()
          and last.astimezone(ref).date() <= parse_date(request["window_end_date"], "window"), "date_window")
    check(request["min_trip_days"] <= trip_days <= request["max_trip_days"], "trip_duration")
    cost = sum(o["price_minor"] for o in offers)
    check(cost <= request["flight_budget_minor"], "flight_budget")
    check(all(o["connection_count"] <= request["max_connections_per_offer"] for o in offers), "connection_limit")
    destination_ids = [a["destination_id"] for a in accesses]
    check(len(set(destination_ids)) == len(destination_ids), "duplicate_destination")
    check(set(request["required_destination_ids"]) <= set(destination_ids), "required_missing")
    check(len(set(destination_ids) - set(request["required_destination_ids"])) <= request["max_optional_destinations"], "optional_limit")
    ground = sum(a["to_destination_cost_minor"] + a["to_airport_cost_minor"] for a in accesses)
    transit = sum(minutes(instant(o["departure_at"]), instant(o["arrival_at"])) for o in offers)
    transit += sum(a["to_destination_minutes"] + a["to_airport_minutes"] for a in accesses)
    days: set[str] = set()
    night = 0
    s = data.scoring
    for offer in offers:
        for segment in offer["segments"]:
            a, b = instant(segment["departure_at"]), instant(segment["arrival_at"])
            days |= flight_dates(a, b, ref)
            night += daily_overlap(a, b, ref, s["night_start_reference"], s["night_end_reference"])
    visits = []
    with localcontext() as context:
        context.prec = 40
        experience = Decimal(0)
        for index, access in enumerate(accesses):
            inbound, outbound = offers[index:index + 2]
            check(inbound["destination_airport_id"] == access["airport_id"] == outbound["origin_airport_id"], "visit_airport")
            d = data.destinations[access["destination_id"]]
            tz = zone(d["timezone"])
            a = (instant(inbound["arrival_at"]).astimezone(UTC) + timedelta(minutes=access["to_destination_minutes"])).astimezone(tz)
            b = (instant(outbound["departure_at"]).astimezone(UTC) - timedelta(minutes=access["to_airport_minutes"] + s["airport_buffer_minutes"])).astimezone(tz)
            check(b > a, "visit_chronology")
            nights = (b.date() - a.date()).days
            usable = daily_overlap(a, b, tz, s["usable_day_start_local"], s["usable_day_end_local"])
            check(d["min_stay_nights"] <= nights <= d["max_stay_nights"], "stay_nights")
            check(usable >= d["min_usable_minutes"], "usable_minutes")
            weights = request["preference_weights"]
            total_weight = sum(weights.values())
            match = (Decimal(sum(w * d["tag_scores"][tag] for tag, w in weights.items())) / total_weight
                     if total_weight else Decimal(50))
            if d["id"] in request["preferred_destination_ids"]:
                match = min(Decimal(100), match + s["preferred_bonus_points"])
            value = match * min(Decimal(1), Decimal(max(0, nights)) / d["recommended_stay_nights"])
            value *= Decimal(d["season_scores_by_month"][str(a.month)]) / 100
            experience += value
            visits.append({"destination_id": d["id"], "arrival_at": a.isoformat(), "departure_at": b.isoformat(),
                           "stay_nights": nights, "usable_minutes": usable})
        coefficients = {k: Decimal(str(v)) for k, v in s["burden_coefficients"].items()}
        connections = sum(o["connection_count"] for o in offers)
        burden = (Decimal(transit) / 60 * coefficients["transit_hour"] + len(days) * coefficients["flight_day"]
                  + connections * coefficients["connection"] + Decimal(night) / 60 * coefficients["night_hour"])
        scales = {k: Decimal(str(v)) for k, v in s["normalization_scales"].items()}
        weights = {k: Decimal(str(v)) for k, v in s["ranking_weights"].items()}
        score = (100 / (1 + Decimal(cost) / scales["cost_minor"]) * weights["cost"]
                 + 100 / (1 + burden / scales["burden"]) * weights["comfort"]
                 + 100 * experience / (experience + scales["experience"]) * weights["experience"])

        def rounded(value: Decimal) -> str:
            return str(value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))

        metrics = {"trip_days": trip_days, "flight_total_minor": cost, "ground_total_minor": ground,
                   "transit_minutes": transit, "flight_dates": sorted(days), "flight_day_count": len(days),
                   "connection_count": connections, "night_flight_minutes": night,
                   "burden_points": rounded(burden), "experience_points": rounded(experience),
                   "recommendation_score": rounded(score), "visits": visits}
    return {"id": witness["id"], "valid": not errors, "errors": sorted(errors), "metrics": metrics}
