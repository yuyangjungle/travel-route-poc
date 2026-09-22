"""Presentation orchestration; route search and scoring stay in travel_core."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path
import os

from benchmark_tools.validation import load_dataset, validate_request, ValidationError
from travel_core import search
from travel_ui.scenario_library import load_destination_descriptions, load_m6_scenarios
from travel_ui.presentation import build_itinerary_cards, comparison_rows
from travel_ui.view_models import PREFERENCE_NAMES
from travel_data import SyntheticDestinationProvider, SyntheticFlightProvider

ROOT = Path(__file__).resolve().parents[1]
PRODUCT_VERSION = "m9-product-v1"


@lru_cache(maxsize=1)
def resources():
    data = load_dataset(ROOT / "data/m5")
    descriptions = load_destination_descriptions(ROOT / "data/m6/destination_descriptions.json")
    scenarios = load_m6_scenarios(ROOT / "scenarios/m6", ROOT)
    return data, descriptions, scenarios


@lru_cache(maxsize=1)
def providers():
    data, _, _ = resources()
    return SyntheticFlightProvider(data), SyntheticDestinationProvider(data)


def request_fields(request: dict) -> dict:
    fields = {key: deepcopy(request[key]) for key in (
        "origin_airport_ids", "return_airport_ids", "window_start_date", "window_end_date",
        "min_trip_days", "max_trip_days", "required_destination_ids", "preferred_destination_ids",
        "preference_weights", "max_optional_destinations", "max_connections_per_offer", "allow_self_transfer",
    )}
    fields["flight_budget_cny"] = request["flight_budget_minor"] // 100
    return fields


def confirmed_request(fields: dict) -> dict:
    data, _, _ = resources()
    if not isinstance(fields, dict):
        raise ValueError("请完整填写旅行条件。")
    budget = fields.get("flight_budget_cny")
    if type(budget) is not int or not 1 <= budget <= 50000:
        raise ValueError("请输入 1–50,000 元的单人机票预算。")
    required_keys = set(request_fields(resources()[2][0]["request"]))
    if set(fields) != required_keys:
        raise ValueError("旅行条件字段不完整或包含未知字段，请重新检查。")
    for key in ("min_trip_days", "max_trip_days"):
        if type(fields[key]) is not int or not 7 <= fields[key] <= 21:
            raise ValueError("旅行长度需要在 7–21 天之间。")
    connections = fields["max_connections_per_offer"]
    if type(connections) is not int or not 0 <= connections <= 2:
        raise ValueError("每程中转次数需要在 0–2 之间。")
    request = deepcopy(fields)
    request.pop("flight_budget_cny")
    request.update(request_id="m9-confirmed", flight_budget_minor=budget * 100,
                   reference_timezone="Asia/Shanghai", currency="CNY", passenger_count=1,
                   fare_profile_id=data.manifest["fare_profile_id"])
    validate_request(request, data)
    return request


def bootstrap() -> dict:
    data, descriptions, scenarios = resources()
    offer_ids = scenarios[0]["offer_ids"]
    served = {data.offers[i]["destination_airport_id"] for i in offer_ids}
    covered = {a["destination_id"] for a in data.accesses.values() if a["airport_id"] in served}
    examples = {
        "m6-graduation-trip": "从上海或杭州出发，2027年10月1日到22日间玩7到12天，机票5000以内，喜欢海滩、美食，想去普吉岛。",
        "m6-island-diving-trip": "2027年10月从上海出发，12到18天，仙本那一定要去，也想去巴厘岛，机票9000以内，最喜欢潜水和海岛，每程最多中转一次。",
        "m6-long-holiday-exploration": "10月从上海出发，12天左右，想去潜水，不想太折腾，机票预算8000以内，目的地可以灵活安排。",
        "m6-premium-relaxation-trip": "2027年10月1日到22日，从上海出发玩10到16天，机票12000以内，喜欢海岛和放松，尽量少折腾，想去巴厘岛。",
    }
    return {
        "product_version": PRODUCT_VERSION,
        "ai_available": bool(os.environ.get("DEEPSEEK_API_KEY", "").strip()) and os.environ.get("AI_ENABLED", "true").lower() == "true",
        "data_status": "simulated", "dataset_version": data.manifest["dataset_version"],
        "coverage": {"start": "2027-10-01", "end": "2027-10-22", "offer_count": len(offer_ids),
                     "destination_count": len(covered), "catalog_count": len(data.destinations),
                     "note": "公开演示使用 2027 年 10 月固定模拟报价；其他日期或目录地点可能没有报价。"},
        "airports": [{"id": i, "name": n} for i, n in [("PVG", "上海浦东"), ("HGH", "杭州萧山"), ("NKG", "南京禄口")]],
        "destinations": [{"id": i, "name": providers()[1].get_destination(i).descriptive.display_name_zh,
                          "in_demo_network": i in covered} for i in sorted(data.destinations)],
        "preferences": PREFERENCE_NAMES,
        "scenarios": [{"id": x["id"], "title": x["title"], "intent": x["expected_user_intent"],
                       "example": examples[x["id"]], "fields": request_fields(x["request"])} for x in scenarios],
    }


def optimize(fields: dict, *, confirmed: bool) -> dict:
    if confirmed is not True:
        raise ValueError("请先核对并确认旅行条件，再生成方案。")
    request = confirmed_request(fields)
    data, descriptions, scenarios = resources()
    result = search(request, data, scenarios[0]["offer_ids"])
    cards = build_itinerary_cards(result["itineraries"], data, request, descriptions)
    for card, itinerary in zip(cards, result["itineraries"]):
        card["labels"] = itinerary["recommendation_labels"]
        card["start_date"] = data.offers[itinerary["offer_ids"][0]]["departure_at"][:10]
        card["end_date"] = data.offers[itinerary["offer_ids"][-1]]["arrival_at"][:10]
        flights, destinations = providers()
        card["offer_sources"] = [asdict(flights.get_offer(i).provenance) | {
            "id": i, "observed_at": flights.get_offer(i).observed_at,
        } for i in itinerary["offer_ids"]]
        for visit in card["destinations"]:
            identifier = visit["destination_id"]
            visit["knowledge"] = destinations.get_destination(identifier).to_dict()
            visit["preference_matches"] = [asdict(match) for match in destinations.match_preferences(
                identifier, request["preference_weights"])]
    return {"product_version": PRODUCT_VERSION, "status": result["status"], "request": request,
            "cards": cards, "comparison": comparison_rows(cards, descriptions),
            "summary": result["run_summary"], "search_complete": result["search_complete"],
            "dataset_version": result["dataset_version"], "scoring_version": result["scoring_version"],
            "data_fingerprint": result["data_fingerprint"], "data_status": "simulated",
            "scope": "仅对固定的 264 条模拟报价和当前约束做完整搜索，不代表真实市场最低价。",
            "diagnostics": result["diagnostics"]}
