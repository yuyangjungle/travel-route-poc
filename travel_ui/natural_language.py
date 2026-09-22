"""Optional LLM boundary for converting travel prose into a validated TripRequest.

The model never sees flight offers and never returns an itinerary. It can only
select values from the loaded dataset catalog. Every response is validated
again before it reaches the deterministic optimizer.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from typing import Any

from benchmark_tools.validation import (Dataset, JsonObject, ValidationError,
                                        validate_request)


AI_EXTRACTION_FIELDS = {
    "origin_airport_ids", "return_airport_ids", "window_start_date",
    "window_end_date", "min_trip_days", "max_trip_days", "flight_budget_cny",
    "required_destination_ids", "preferred_destination_ids", "preference_weights",
    "max_optional_destinations", "max_connections_per_offer", "summary",
    "assumptions", "unresolved_mentions",
}


class NaturalLanguageInputError(ValueError):
    """A safe, user-facing failure at the optional AI input boundary."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ExtractedTravelRequest:
    request: JsonObject
    summary: str
    assumptions: tuple[str, ...]
    unresolved_mentions: tuple[str, ...]
    model: str


def _integer(value: Any, field: str, lower: int, upper: int | None = None) -> int:
    if type(value) is not int or value < lower or (upper is not None and value > upper):
        limit = f"{lower}–{upper}" if upper is not None else f">={lower}"
        raise NaturalLanguageInputError("invalid_ai_output", f"{field} 必须是整数 {limit}。")
    return value


def _strings(value: Any, field: str, *, allow_empty: bool = True,
             max_count: int | None = None, max_length: int | None = None) -> list[str]:
    if (not isinstance(value, list) or (not allow_empty and not value)
            or any(not isinstance(item, str) or not item.strip() for item in value)):
        raise NaturalLanguageInputError("invalid_ai_output", f"{field} 必须是字符串列表。")
    cleaned = [item.strip() for item in value]
    if len(cleaned) != len(set(cleaned)):
        raise NaturalLanguageInputError("invalid_ai_output", f"{field} 不能包含重复值。")
    if max_count is not None and len(cleaned) > max_count:
        raise NaturalLanguageInputError("invalid_ai_output", f"{field} 项目过多。")
    if max_length is not None and any(len(item) > max_length for item in cleaned):
        raise NaturalLanguageInputError("invalid_ai_output", f"{field} 文本过长。")
    return cleaned


def extraction_schema(data: Dataset) -> JsonObject:
    """Build the strict schema from the current catalog allowlists."""
    home_airports = sorted(set(data.airports) & {"PVG", "HGH", "NKG"})
    destinations = sorted(data.destinations)
    tags = list(data.manifest["preference_tag_ids"])
    return {
        "type": "object",
        "additionalProperties": False,
        "required": sorted(AI_EXTRACTION_FIELDS),
        "properties": {
            "origin_airport_ids": {
                "type": "array",
                "items": {"type": "string", "enum": home_airports},
            },
            "return_airport_ids": {
                "type": "array",
                "items": {"type": "string", "enum": home_airports},
            },
            "window_start_date": {"type": "string"},
            "window_end_date": {"type": "string"},
            "min_trip_days": {"type": "integer"},
            "max_trip_days": {"type": "integer"},
            "flight_budget_cny": {"type": "integer"},
            "required_destination_ids": {
                "type": "array",
                "items": {"type": "string", "enum": destinations},
            },
            "preferred_destination_ids": {
                "type": "array",
                "items": {"type": "string", "enum": destinations},
            },
            "preference_weights": {
                "type": "object", "additionalProperties": False,
                "required": tags,
                "properties": {
                    tag: {"type": "integer"}
                    for tag in tags
                },
            },
            "max_optional_destinations": {
                "type": "integer",
            },
            "max_connections_per_offer": {
                "type": "integer",
            },
            "summary": {"type": "string"},
            "assumptions": {
                "type": "array", "items": {"type": "string"},
            },
            "unresolved_mentions": {
                "type": "array", "items": {"type": "string"},
            },
        },
    }


def catalog_prompt(data: Dataset, descriptions: dict[str, JsonObject]) -> str:
    airports = ", ".join(
        f"{identifier}={data.airports[identifier]['name']}"
        for identifier in sorted(set(data.airports) & {"PVG", "HGH", "NKG"})
    )
    destinations = []
    for identifier in sorted(data.destinations):
        description = descriptions.get(identifier, {})
        name = description.get("display_name_zh") or data.destinations[identifier]["name"].replace(
            " (SIMULATED profile)", ""
        )
        destinations.append(f"{identifier}={name}")
    tags = ", ".join(data.manifest["preference_tag_ids"])
    return (
        "你只负责把用户旅行描述提取为结构化约束，不生成路线，不判断航班，也不补造地点。\n"
        f"允许出发/返程机场：{airports}\n"
        f"允许目的地：{', '.join(destinations)}\n"
        f"允许偏好标签：{tags}\n"
        f"数据日期覆盖：{data.manifest['coverage_start_date']} 至 "
        f"{data.manifest['coverage_end_date']}。本演示的密集报价集中在 2027 年 10 月。\n"
        "规则：地点只能输出上方 ID；用户提到目录外地点时放入 unresolved_mentions，不能发明 ID。"
        "未说明的偏好权重填 0。模糊月份映射到覆盖范围内完整窗口，并在 assumptions 说明。"
        "预算只表示单人机票预算。'不想太多中转'通常映射为每张报价最多 1 次中转。"
        "required 只用于用户明确表示必须去的地点；感兴趣地点放 preferred。"
    )


def validate_extraction(payload: Any, data: Dataset, *, request_id: str,
                        model: str) -> ExtractedTravelRequest:
    """Validate model JSON and produce the unchanged optimizer request shape."""
    if not isinstance(payload, dict) or set(payload) != AI_EXTRACTION_FIELDS:
        raise NaturalLanguageInputError("invalid_ai_output", "AI 返回字段不完整或包含未知字段。")
    origins = _strings(payload["origin_airport_ids"], "origin_airport_ids", allow_empty=False)
    returns = _strings(payload["return_airport_ids"], "return_airport_ids", allow_empty=False)
    required = _strings(payload["required_destination_ids"], "required_destination_ids")
    preferred = _strings(payload["preferred_destination_ids"], "preferred_destination_ids")
    assumptions = _strings(
        payload["assumptions"], "assumptions", max_count=8, max_length=200
    )
    unresolved = _strings(
        payload["unresolved_mentions"], "unresolved_mentions", max_count=8, max_length=200
    )
    if set(required) & set(preferred):
        raise NaturalLanguageInputError("invalid_ai_output", "必去和想去目的地不能重叠。")
    tags = set(data.manifest["preference_tag_ids"])
    weights = payload["preference_weights"]
    if not isinstance(weights, dict) or set(weights) != tags:
        raise NaturalLanguageInputError("invalid_ai_output", "偏好权重必须完整匹配当前数据目录。")
    validated_weights = {
        tag: _integer(weights[tag], f"preference_weights.{tag}", 0, 100)
        for tag in sorted(tags)
    }
    summary = payload["summary"]
    if not isinstance(summary, str) or not summary.strip() or len(summary.strip()) > 300:
        raise NaturalLanguageInputError("invalid_ai_output", "summary 必须是简短非空文本。")
    request = {
        "request_id": request_id,
        "origin_airport_ids": origins,
        "return_airport_ids": returns,
        "window_start_date": payload["window_start_date"],
        "window_end_date": payload["window_end_date"],
        "reference_timezone": "Asia/Shanghai",
        "min_trip_days": _integer(payload["min_trip_days"], "min_trip_days", 7, 21),
        "max_trip_days": _integer(payload["max_trip_days"], "max_trip_days", 7, 21),
        "flight_budget_minor": _integer(payload["flight_budget_cny"], "flight_budget_cny", 1, 50000) * 100,
        "currency": "CNY",
        "passenger_count": 1,
        "fare_profile_id": data.manifest["fare_profile_id"],
        "required_destination_ids": required,
        "preferred_destination_ids": preferred,
        "preference_weights": validated_weights,
        "max_optional_destinations": _integer(
            payload["max_optional_destinations"], "max_optional_destinations", 0, 4
        ),
        "max_connections_per_offer": _integer(
            payload["max_connections_per_offer"], "max_connections_per_offer", 0, 2
        ),
        "allow_self_transfer": False,
    }
    try:
        validate_request(request, data)
    except ValidationError as exc:
        raise NaturalLanguageInputError(
            "invalid_ai_output", f"提取结果未通过旅行条件校验：{exc}"
        ) from exc
    return ExtractedTravelRequest(
        request=deepcopy(request), summary=summary.strip(), assumptions=tuple(assumptions),
        unresolved_mentions=tuple(unresolved), model=model,
    )


class OpenAITravelRequestExtractor:
    """Responses API adapter using strict Structured Outputs and no tools."""

    def __init__(self, *, api_key: str, model: str, client: Any | None = None):
        if not api_key.strip():
            raise NaturalLanguageInputError("ai_not_configured", "未配置 OPENAI_API_KEY。")
        if not model.strip():
            raise NaturalLanguageInputError("ai_not_configured", "未配置 OPENAI_MODEL。")
        if client is None:
            try:
                from openai import OpenAI
            except (ImportError, AttributeError) as exc:
                raise NaturalLanguageInputError(
                    "ai_not_configured", "OpenAI SDK 未安装或版本过旧。"
                ) from exc
            client = OpenAI(api_key=api_key)
        self.client = client
        self.model = model.strip()

    def extract(self, text: str, data: Dataset, descriptions: dict[str, JsonObject],
                *, request_id: str = "ai-discovery") -> ExtractedTravelRequest:
        user_text = text.strip()
        if len(user_text) < 10:
            raise NaturalLanguageInputError("invalid_natural_language", "请至少描述出发地、时间或旅行偏好。")
        if len(user_text) > 2000:
            raise NaturalLanguageInputError("invalid_natural_language", "旅行描述不能超过 2000 个字符。")
        try:
            response = self.client.responses.create(
                model=self.model,
                instructions=catalog_prompt(data, descriptions),
                input=user_text,
                text={"format": {
                    "type": "json_schema", "name": "travel_request_extraction",
                    "strict": True, "schema": extraction_schema(data),
                }},
                store=False,
            )
            output = response.output_text
        except NaturalLanguageInputError:
            raise
        except Exception as exc:
            raise NaturalLanguageInputError(
                "ai_service_error", "AI 条件提取暂时不可用，请改用手动规划。"
            ) from exc
        if not isinstance(output, str) or not output.strip():
            raise NaturalLanguageInputError("invalid_ai_output", "AI 没有返回可用的结构化结果。")
        try:
            payload = json.loads(output)
        except json.JSONDecodeError as exc:
            raise NaturalLanguageInputError("invalid_ai_output", "AI 返回的内容不是有效 JSON。") from exc
        return validate_extraction(payload, data, request_id=request_id, model=self.model)
