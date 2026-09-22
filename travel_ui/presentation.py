"""Deterministic M6 explanation, destination and comparison view models."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Iterable

from benchmark_tools.validation import Dataset, JsonObject
from travel_ui.view_models import DESTINATION_NAMES, LABELS, PREFERENCE_NAMES


def display_name(identifier: str, descriptions: dict[str, JsonObject] | None = None) -> str:
    if descriptions and identifier in descriptions:
        return descriptions[identifier]["display_name_zh"]
    return DESTINATION_NAMES.get(identifier, identifier)


def destination_ids(itinerary: JsonObject) -> tuple[str, ...]:
    return tuple(visit["destination_id"] for visit in itinerary["visits"])


def route_sequence(itinerary: JsonObject, descriptions: dict[str, JsonObject]) -> str:
    stops = [itinerary["origin_airport_id"]]
    stops.extend(display_name(identifier, descriptions) for identifier in destination_ids(itinerary))
    stops.append(itinerary["return_airport_id"])
    return " → ".join(stops)


def label_text(labels: list[str]) -> str:
    return " · ".join(LABELS.get(label, label) for label in labels) or "Pareto 备选"


def format_signed(value: int, suffix: str = "") -> str:
    if value == 0:
        return f"相同{suffix}"
    return f"{'+' if value > 0 else '−'}{abs(value):,}{suffix}"


def format_duration(minutes: int) -> str:
    hours, remainder = divmod(minutes, 60)
    return f"{hours} 小时" + (f" {remainder} 分钟" if remainder else "")


def subset_reference(itinerary: JsonObject, alternatives: list[JsonObject]) -> JsonObject | None:
    current = set(destination_ids(itinerary))
    candidates = [item for item in alternatives if item["id"] != itinerary["id"]
                  and set(destination_ids(item)) < current]
    return max(candidates, key=lambda item: (len(item["visits"]), -item["flight_total_minor"]), default=None)


def weighted_preferences(request: JsonObject) -> list[str]:
    return [tag for tag, value in sorted(request.get("preference_weights", {}).items(),
                                         key=lambda item: (-item[1], item[0])) if value > 0]


def explanation_lines(itinerary: JsonObject, alternatives: list[JsonObject], request: JsonObject,
                      data: Dataset, descriptions: dict[str, JsonObject]) -> list[str]:
    reasons: list[str] = []
    labels = set(itinerary["recommendation_labels"])
    cheapest = min(alternatives, key=lambda item: (item["flight_total_minor"], item["id"]))
    easiest = min(alternatives, key=lambda item: (Decimal(item["burden_points"]), item["id"]))
    subset = subset_reference(itinerary, alternatives)
    if subset and itinerary["flight_total_minor"] < subset["flight_total_minor"]:
        added = set(destination_ids(itinerary)) - set(destination_ids(subset))
        names = "、".join(display_name(item, descriptions) for item in sorted(added))
        saving = (subset["flight_total_minor"] - itinerary["flight_total_minor"]) // 100
        reasons.append(f"在本场景模拟报价中，增加{names}后机票少 ¥{saving:,}。")
    elif itinerary["id"] == cheapest["id"]:
        reasons.append("它是当前约束和模拟报价中的最低机票方案。")
    else:
        premium = (itinerary["flight_total_minor"] - cheapest["flight_total_minor"]) // 100
        reasons.append(f"机票比最低价方案多 ¥{premium:,}，差额换取不同的目的地或节奏。")

    if itinerary["id"] == easiest["id"]:
        reasons.append(f"它的交通负担最低：{format_duration(itinerary['transit_minutes'])}交通、{itinerary['flight_day_count']} 个飞行日。")
    else:
        extra_minutes = itinerary["transit_minutes"] - easiest["transit_minutes"]
        change = (f"多 {format_duration(extra_minutes)}" if extra_minutes > 0 else
                  f"少 {format_duration(-extra_minutes)}" if extra_minutes < 0 else "相同")
        reasons.append(f"相对最少折腾方案，交通时间{change}；交通负担评分还计入中转、飞行日和目的地数量。")

    top_tags = weighted_preferences(request)[:2]
    matched = []
    for identifier in destination_ids(itinerary):
        if any(data.destinations[identifier]["tag_scores"].get(tag, 0) >= 70 for tag in top_tags):
            matched.append(display_name(identifier, descriptions))
    if matched and top_tags:
        tag_text = "、".join(PREFERENCE_NAMES.get(tag, tag) for tag in top_tags)
        reasons.append(f"{'、'.join(matched)}与本次最高权重偏好（{tag_text}）匹配。")
    preferred = set(request.get("preferred_destination_ids", [])) & set(destination_ids(itinerary))
    if preferred:
        reasons.append("包含想去但非必去的地点：" + "、".join(
            display_name(item, descriptions) for item in sorted(preferred)) + "。")
    if "best_overall" in labels:
        reasons.append("按固定 score-v1 权重，它在机票、交通负担和体验收益之间的综合分最高。")
    return reasons[:4]


def tradeoff_lines(itinerary: JsonObject, alternatives: list[JsonObject],
                   descriptions: dict[str, JsonObject]) -> list[str]:
    reference = subset_reference(itinerary, alternatives)
    if reference is None:
        reference = min((item for item in alternatives if item["id"] != itinerary["id"]),
                        key=lambda item: (-Decimal(item["recommendation_score"]), item["id"]), default=None)
    if reference is None:
        return ["当前只有一个代表方案，没有同批备选可比较。"]
    cost = (itinerary["flight_total_minor"] - reference["flight_total_minor"]) // 100
    transit = itinerary["transit_minutes"] - reference["transit_minutes"]
    days = itinerary["flight_day_count"] - reference["flight_day_count"]
    added = set(destination_ids(itinerary)) - set(destination_ids(reference))
    removed = set(destination_ids(reference)) - set(destination_ids(itinerary))
    route_change = []
    if added:
        route_change.append("多去" + "、".join(display_name(item, descriptions) for item in sorted(added)))
    if removed:
        route_change.append("不含" + "、".join(display_name(item, descriptions) for item in sorted(removed)))
    return [
        f"相对“{route_sequence(reference, descriptions)}”：机票 {format_signed(cost, ' 元')}，"
        f"交通 {format_signed(transit, ' 分钟')}，飞行日 {format_signed(days, ' 天')}；"
        f"{'；'.join(route_change) or '目的地集合相同，航班、顺序或停留安排有所不同'}。"
    ]


def destination_profile(visit: JsonObject, data: Dataset,
                        descriptions: dict[str, JsonObject]) -> JsonObject:
    identifier = visit["destination_id"]
    optimization = data.destinations[identifier]
    access = data.accesses[visit["access_id"]]
    month = datetime.fromisoformat(visit["arrival_at"]).month
    description = descriptions.get(identifier, {
        "display_name_zh": display_name(identifier), "destination_type": "未分类",
        "suitable_activities": [], "seasonal_note": "暂无展示说明。",
        "transport_difficulty": "未标注", "transport_note": "暂无展示说明。",
    })
    return {
        "destination_id": identifier, "destination": description["display_name_zh"],
        "stay_nights": visit["stay_nights"], "arrival_at": visit["arrival_at"],
        "departure_at": visit["departure_at"], "experience_points": visit["experience_points"],
        "optimization_data": {
            "recommended_stay_nights": optimization["recommended_stay_nights"],
            "season_score": optimization["season_scores_by_month"][str(month)],
            "airport_transfer_minutes": access["to_destination_minutes"] + access["to_airport_minutes"],
        },
        "descriptive_information": {
            "destination_type": description["destination_type"],
            "suitable_activities": list(description["suitable_activities"]),
            "seasonal_note": description["seasonal_note"],
            "transport_difficulty": description["transport_difficulty"],
            "transport_note": description["transport_note"],
        },
    }


def itinerary_card(itinerary: JsonObject, data: Dataset, rank: int,
                   alternatives: list[JsonObject], request: JsonObject,
                   descriptions: dict[str, JsonObject]) -> JsonObject:
    visits = [destination_profile(visit, data, descriptions) for visit in itinerary["visits"]]
    legs = []
    for offer_id in itinerary["offer_ids"]:
        offer = data.offers[offer_id]
        legs.append({
            "航段": f"{offer['origin_airport_id']} → {offer['destination_airport_id']}",
            "起飞": offer["departure_at"].replace("T", " "),
            "抵达": offer["arrival_at"].replace("T", " "),
            "价格": f"¥{offer['price_minor'] / 100:,.0f}", "中转": offer["connection_count"],
        })
    return {
        "itinerary_id": itinerary["id"], "rank": rank,
        "label": label_text(itinerary["recommendation_labels"]),
        "route": route_sequence(itinerary, descriptions), "destinations": visits,
        "destination_ids": list(destination_ids(itinerary)),
        "flight_cost_minor": itinerary["flight_total_minor"],
        "flight_cost": f"¥{itinerary['flight_total_minor'] / 100:,.0f}",
        "transit_minutes": itinerary["transit_minutes"],
        "transit": format_duration(itinerary["transit_minutes"]),
        "burden": f"{float(itinerary['burden_points']):.1f}",
        "experience": f"{float(itinerary['experience_points']):.1f}",
        "overall": f"{float(itinerary['recommendation_score']):.1f}",
        "flight_days": itinerary["flight_day_count"], "connection_count": itinerary["connection_count"],
        "trip_days": itinerary["trip_days"],
        "why": explanation_lines(itinerary, alternatives, request, data, descriptions),
        "tradeoffs": tradeoff_lines(itinerary, alternatives, descriptions), "legs": legs,
    }


def build_itinerary_cards(itineraries: Iterable[JsonObject], data: Dataset,
                          request: JsonObject,
                          descriptions: dict[str, JsonObject]) -> list[JsonObject]:
    alternatives = list(itineraries)
    return [itinerary_card(item, data, rank, alternatives, request, descriptions)
            for rank, item in enumerate(alternatives, 1)]


def comparison_rows(cards: list[JsonObject], descriptions: dict[str, JsonObject]) -> list[JsonObject]:
    if not cards:
        return []
    lowest_cost = min(card["flight_cost_minor"] for card in cards)
    lowest_transit = min(card["transit_minutes"] for card in cards)
    shortest = min(cards, key=lambda card: (len(card["destination_ids"]), card["flight_cost_minor"]))
    base_destinations = set(shortest["destination_ids"])
    rows = []
    for card in cards:
        added = set(card["destination_ids"]) - base_destinations
        rows.append({
            "方案": f"方案 {card['rank']} · {card['label']}", "路线": card["route"],
            "模拟机票": card["flight_cost"],
            "较最低价": format_signed((card["flight_cost_minor"] - lowest_cost) // 100, " 元"),
            "交通时间": card["transit"],
            "较最少交通": format_signed(card["transit_minutes"] - lowest_transit, " 分钟"),
            "新增目的地": "、".join(display_name(item, descriptions) for item in sorted(added)) or "—",
            "飞行日": card["flight_days"], "中转次数": card["connection_count"],
            "体验收益": card["experience"], "主要取舍": card["tradeoffs"][0],
        })
    return rows
