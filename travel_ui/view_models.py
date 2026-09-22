"""Pure input and result mapping shared by UI tests and Streamlit rendering."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from benchmark_tools.validation import Dataset, JsonObject

LABELS = {
    "cheapest": "最便宜",
    "least_exhausting": "最少折腾",
    "strongest_preference_match": "偏好最匹配",
    "best_overall": "综合推荐",
}

DESTINATION_NAMES = {
    "SEMPORNA": "仙本那",
    "KOTA": "亚庇",
    "KL": "吉隆坡",
    "SAIGON": "胡志明市",
    "BALI": "巴厘岛",
    "PHUKET": "普吉岛",
}

PREFERENCE_NAMES = {
    "island": "海岛", "diving": "潜水", "food": "美食", "culture": "文化",
    "nature": "自然", "city": "城市", "relaxation": "放松",
}


def money_to_minor(value: int | float | Decimal) -> int:
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def build_request(*, request_id: str, origins: list[str], returns: list[str],
                  date_window: tuple[date, date], duration: tuple[int, int],
                  budget_cny: int | float | Decimal, required_destinations: list[str],
                  preference_weights: dict[str, int], max_optional_destinations: int,
                  max_connections_per_offer: int, fare_profile_id: str,
                  preferred_destinations: list[str] | None = None) -> JsonObject:
    return {
        "request_id": request_id,
        "origin_airport_ids": list(origins),
        "return_airport_ids": list(returns),
        "window_start_date": date_window[0].isoformat(),
        "window_end_date": date_window[1].isoformat(),
        "reference_timezone": "Asia/Shanghai",
        "min_trip_days": int(duration[0]),
        "max_trip_days": int(duration[1]),
        "flight_budget_minor": money_to_minor(budget_cny),
        "currency": "CNY",
        "passenger_count": 1,
        "fare_profile_id": fare_profile_id,
        "required_destination_ids": list(required_destinations),
        "preferred_destination_ids": list(preferred_destinations or []),
        "preference_weights": {key: int(preference_weights[key]) for key in sorted(preference_weights)},
        "max_optional_destinations": int(max_optional_destinations),
        "max_connections_per_offer": int(max_connections_per_offer),
        "allow_self_transfer": False,
    }


def destination_name(identifier: str) -> str:
    return DESTINATION_NAMES.get(identifier, identifier)


def route_sequence(itinerary: JsonObject) -> str:
    stops = [itinerary["origin_airport_id"]]
    stops.extend(destination_name(visit["destination_id"]) for visit in itinerary["visits"])
    stops.append(itinerary["return_airport_id"])
    return " → ".join(stops)


def label_text(labels: list[str]) -> str:
    return " · ".join(LABELS.get(label, label) for label in labels) or "Pareto 备选"


def why_recommended(itinerary: JsonObject) -> list[str]:
    reasons = []
    labels = set(itinerary["recommendation_labels"])
    if "best_overall" in labels:
        reasons.append("在机票、交通负担和偏好收益的当前权重下综合分最高。")
    if "cheapest" in labels:
        reasons.append("它是当前约束和模拟报价中的最低机票方案。")
    if "least_exhausting" in labels:
        reasons.append("它的交通负担分最低。")
    if "strongest_preference_match" in labels:
        reasons.append("它的偏好与停留收益最高。")
    if not reasons:
        reasons.append("它位于 Pareto 前沿，提供了不同于其他方案的取舍。")
    return reasons


def format_signed(value: int, suffix: str = "") -> str:
    if value == 0:
        return f"相同{suffix}"
    sign = "+" if value > 0 else "−"
    return f"{sign}{abs(value):,}{suffix}"


def tradeoff_text(itinerary: JsonObject) -> list[str]:
    if not itinerary["tradeoffs"]:
        return ["这是综合推荐基准，其他方案与它比较。"]
    tradeoff = itinerary["tradeoffs"][0]
    cost = tradeoff["flight_cost_delta_minor"] // 100
    transit = tradeoff["transit_minutes_delta"]
    days = tradeoff["flight_days_delta"]
    return [
        f"相对综合推荐：机票 {format_signed(cost, ' 元')}，交通 {format_signed(transit, ' 分钟')}，飞行日 {format_signed(days, ' 天')}。"
    ]


def itinerary_card(itinerary: JsonObject, data: Dataset, rank: int) -> JsonObject:
    visits = [{
        "destination": destination_name(visit["destination_id"]),
        "stay_nights": visit["stay_nights"],
        "arrival_at": visit["arrival_at"],
        "departure_at": visit["departure_at"],
        "experience_points": visit["experience_points"],
    } for visit in itinerary["visits"]]
    legs = []
    for offer_id in itinerary["offer_ids"]:
        offer = data.offers[offer_id]
        legs.append({
            "航段": f"{offer['origin_airport_id']} → {offer['destination_airport_id']}",
            "起飞": offer["departure_at"].replace("T", " "),
            "抵达": offer["arrival_at"].replace("T", " "),
            "价格": f"¥{offer['price_minor'] / 100:,.0f}",
            "中转": offer["connection_count"],
        })
    return {
        "itinerary_id": itinerary["id"],
        "rank": rank,
        "label": label_text(itinerary["recommendation_labels"]),
        "route": route_sequence(itinerary),
        "destinations": visits,
        "flight_cost": f"¥{itinerary['flight_total_minor'] / 100:,.0f}",
        "transit": f"{itinerary['transit_minutes'] / 60:g} 小时",
        "burden": f"{float(itinerary['burden_points']):.1f}",
        "experience": f"{float(itinerary['experience_points']):.1f}",
        "overall": f"{float(itinerary['recommendation_score']):.1f}",
        "flight_days": itinerary["flight_day_count"],
        "trip_days": itinerary["trip_days"],
        "why": why_recommended(itinerary),
        "tradeoffs": tradeoff_text(itinerary),
        "legs": legs,
    }


def result_message(result: JsonObject) -> tuple[str, str]:
    status = result["status"]
    if status == "no_feasible_in_dataset":
        return "warning", "在当前模拟报价与约束下没有可行行程。可以提高预算、放宽日期或允许更多可选目的地后重试。"
    if status == "insufficient_coverage":
        return "error", "请求超出样本数据覆盖范围。当前演示只覆盖 2026 年 10 月。"
    if status == "invalid_request":
        return "error", "输入不符合当前原型的规则，请检查日期、时长和必去地点。"
    if status == "unsupported_request":
        return "error", "当前原型不支持这组请求设置。"
    if status == "invalid_dataset":
        return "error", "演示数据加载失败。"
    return "success", f"找到 {result['run_summary']['feasible_count']} 条可行行程，其中 {result['run_summary']['pareto_count']} 条位于 Pareto 前沿。"
