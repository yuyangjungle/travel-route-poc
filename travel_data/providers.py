"""带来源的数据适配器。展示信息不进入可行性、评分或 Pareto 计算。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol, runtime_checkable

from benchmark_tools.validation import Dataset, ValidationError, instant, read_json, zone


DEFAULT_PROFILES = Path(__file__).resolve().parents[1] / "data" / "m9" / "destination_profiles.json"


class ProviderError(ValueError):
    """可供调用层展示的稳定错误码，不把覆盖缺口解释为没有真实航班。"""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class DataProvenance:
    source: str
    source_ref: str
    version: str
    updated_at: str
    confidence: str
    is_simulated: bool


@dataclass(frozen=True)
class FlightQuery:
    origin_airport_id: str
    destination_airport_id: str
    departure_date: date


@dataclass(frozen=True)
class FlightSegment:
    id: str
    origin_airport_id: str
    destination_airport_id: str
    departure_at: str
    arrival_at: str


@dataclass(frozen=True)
class FlightQuote:
    id: str
    origin_airport_id: str
    destination_airport_id: str
    departure_at: str
    arrival_at: str
    segments: tuple[FlightSegment, ...]
    price_minor: int
    currency: str
    fare_profile_id: str
    connection_count: int
    self_transfer: bool
    protected_connection: bool
    source_type: str
    source_ref: str
    observed_at: str
    provenance: DataProvenance

    def to_core_offer(self) -> dict[str, Any]:
        """返回独立副本，维持既有 FlightOffer 交换格式与整数金额。"""
        result = asdict(self)
        result.pop("provenance")
        result["segments"] = [asdict(segment) for segment in self.segments]
        return result

    def to_dict(self) -> dict[str, Any]:
        return {**self.to_core_offer(), "provenance": asdict(self.provenance)}


@dataclass(frozen=True)
class FlightSearchResult:
    query: FlightQuery
    status: Literal["ok", "no_sample", "out_of_coverage"]
    offers: tuple[FlightQuote, ...]
    provenance: DataProvenance
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": {"origin_airport_id": self.query.origin_airport_id,
                      "destination_airport_id": self.query.destination_airport_id,
                      "departure_date": self.query.departure_date.isoformat()},
            "status": self.status,
            "offers": [offer.to_dict() for offer in self.offers],
            "provenance": asdict(self.provenance),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class DestinationDescription:
    display_name_zh: str
    destination_type: str
    suitable_activities: tuple[str, ...]
    best_season_months: tuple[int, ...]
    seasonal_note: str
    transport_difficulty: Literal["low", "moderate", "high"]
    transport_note: str
    provenance: DataProvenance


@dataclass(frozen=True)
class DestinationOptimizationData:
    min_stay_nights: int
    recommended_stay_nights: int
    max_stay_nights: int
    tag_scores: tuple[tuple[str, int], ...]
    season_scores_by_month: tuple[tuple[str, int], ...]
    provenance: DataProvenance


@dataclass(frozen=True)
class DestinationProfile:
    id: str
    name: str
    airport_ids: tuple[str, ...]
    timezone: str
    descriptive: DestinationDescription
    optimization: DestinationOptimizationData

    def to_dict(self) -> dict[str, Any]:
        description = asdict(self.descriptive)
        description["suitable_activities"] = list(self.descriptive.suitable_activities)
        description["best_season_months"] = list(self.descriptive.best_season_months)
        optimization = asdict(self.optimization)
        optimization["tag_scores"] = dict(self.optimization.tag_scores)
        optimization["season_scores_by_month"] = dict(self.optimization.season_scores_by_month)
        return {"id": self.id, "name": self.name, "airport_ids": list(self.airport_ids),
                "timezone": self.timezone, "descriptive": description,
                "optimization": optimization}


@dataclass(frozen=True)
class PreferenceMatch:
    tag_id: str
    preference_weight: int
    destination_tag_score: int


@runtime_checkable
class FlightProvider(Protocol):
    def search(self, query: FlightQuery) -> FlightSearchResult: ...

    def get_offer(self, offer_id: str) -> FlightQuote: ...


@runtime_checkable
class DestinationProvider(Protocol):
    def list_destinations(self) -> tuple[DestinationProfile, ...]: ...

    def get_destination(self, destination_id: str) -> DestinationProfile: ...

    def match_preferences(self, destination_id: str,
                          weights: Mapping[str, int]) -> tuple[PreferenceMatch, ...]: ...


def _dataset_provenance(data: Dataset) -> DataProvenance:
    if data.manifest["coverage_kind"] != "synthetic":
        raise ProviderError("invalid_dataset", "模拟提供者只能加载 synthetic 数据。")
    return DataProvenance(
        source="synthetic_fixture", source_ref=data.manifest["dataset_id"],
        version=data.manifest["dataset_version"], updated_at=data.manifest["created_at"],
        confidence="simulated_unverified", is_simulated=True,
    )


class SyntheticFlightProvider:
    """按出发机场当地日历日期查询已加载的固定快照，不生成缺失报价。"""

    def __init__(self, data: Dataset):
        self.provenance = _dataset_provenance(data)
        self._airports = deepcopy(data.airports)
        self._start = date.fromisoformat(data.manifest["coverage_start_date"])
        self._end = date.fromisoformat(data.manifest["coverage_end_date"])
        self._offers: dict[str, FlightQuote] = {}
        index: dict[tuple[str, str, date], list[FlightQuote]] = {}
        for offer in data.offers.values():
            if offer["source_type"] != "mock" or type(offer["price_minor"]) is not int:
                raise ProviderError("invalid_dataset", "模拟报价须标记 mock 且价格为整数分。")
            provenance = DataProvenance(
                source="synthetic_fixture", source_ref=offer["source_ref"],
                version=self.provenance.version, updated_at=offer["observed_at"],
                confidence="simulated_unverified", is_simulated=True,
            )
            quote = FlightQuote(**{key: value for key, value in offer.items() if key != "segments"},
                                segments=tuple(FlightSegment(**row) for row in offer["segments"]),
                                provenance=provenance)
            self._offers[quote.id] = quote
            departure_date = instant(quote.departure_at).astimezone(
                zone(self._airports[quote.origin_airport_id]["timezone"])).date()
            index.setdefault((quote.origin_airport_id, quote.destination_airport_id, departure_date), []).append(quote)
        self._index = {key: tuple(sorted(offers, key=lambda offer: (offer.price_minor, offer.departure_at, offer.id)))
                       for key, offers in index.items()}

    def get_offer(self, offer_id: str) -> FlightQuote:
        try:
            return self._offers[offer_id]
        except KeyError as exc:
            raise ProviderError("unknown_offer", f"报价不在当前快照中：{offer_id}") from exc

    def search(self, query: FlightQuery) -> FlightSearchResult:
        if not isinstance(query, FlightQuery) or type(query.departure_date) is not date:
            raise ProviderError("invalid_query", "departure_date 必须是出发机场当地日期 date。")
        for airport_id in (query.origin_airport_id, query.destination_airport_id):
            if not isinstance(airport_id, str) or airport_id not in self._airports:
                raise ProviderError("unknown_airport", "机场不在当前数据覆盖内。")
        if query.origin_airport_id == query.destination_airport_id:
            raise ProviderError("invalid_query", "出发机场和到达机场不能相同。")
        local_zone = zone(self._airports[query.origin_airport_id]["timezone"])
        start = datetime.combine(self._start, time.min, zone("Asia/Shanghai")).astimezone(local_zone).date()
        end = (datetime.combine(self._end + timedelta(days=1), time.min, zone("Asia/Shanghai"))
               - timedelta(microseconds=1)).astimezone(local_zone).date()
        offers = self._index.get((query.origin_airport_id, query.destination_airport_id, query.departure_date), ())
        if query.departure_date < start or query.departure_date > end:
            status = "out_of_coverage"
            warning = "日期超出模拟快照覆盖范围；尚未查询真实库存。"
        elif not offers:
            status = "no_sample"
            warning = "当前模拟快照缺少该路线当日报价，不代表现实中没有航班。"
        else:
            status = "ok"
            warning = "所有报价均为固定模拟数据，不可购买；采样时间不代表仍可售。"
        return FlightSearchResult(query, status, offers, self.provenance, (warning,))


class SyntheticDestinationProvider:
    """描述目录与现有优化参数分别返回，不把描述字段写入 Dataset。"""

    def __init__(self, data: Dataset, profiles_path: Path | None = None):
        optimization_provenance = _dataset_provenance(data)
        self._tags = frozenset(data.manifest["preference_tag_ids"])
        try:
            payload = read_json(profiles_path or DEFAULT_PROFILES)
        except ValidationError as exc:
            raise ProviderError("invalid_profiles", "目的地描述目录不可读取或 JSON 不合法。") from exc
        if (not isinstance(payload, dict)
                or payload.get("schema_version") != "m9-destination-profiles-v1"
                or payload.get("is_simulated") is not True
                or payload.get("confidence") != "simulated_unverified"
                or not isinstance(payload.get("destinations"), list)):
            raise ProviderError("invalid_profiles", "需要版本化的模拟目的地描述目录。")
        for field in ("source", "version", "updated_at"):
            if not isinstance(payload.get(field), str) or not payload[field]:
                raise ProviderError("invalid_profiles", f"缺少目录字段：{field}")
        try:
            instant(payload["updated_at"], "destination_profiles.updated_at")
        except ValidationError as exc:
            raise ProviderError("invalid_profiles", "目的地描述更新时间必须含时区。") from exc
        rows: dict[str, dict[str, Any]] = {}
        required_fields = {"id", "display_name_zh", "destination_type", "suitable_activities",
                           "best_season_months", "seasonal_note", "transport_difficulty", "transport_note"}
        for row in payload["destinations"]:
            if (not isinstance(row, dict) or set(row) != required_fields
                    or not isinstance(row["id"], str) or row["id"] in rows):
                raise ProviderError("invalid_profiles", "目的地描述字段、ID 或唯一性不合法。")
            for field in ("id", "display_name_zh", "destination_type", "seasonal_note", "transport_note"):
                if not isinstance(row[field], str) or not row[field].strip():
                    raise ProviderError("invalid_profiles", f"目的地描述缺少文本：{field}")
            if (not isinstance(row["suitable_activities"], list) or not row["suitable_activities"]
                    or any(not isinstance(value, str) or not value.strip() for value in row["suitable_activities"])):
                raise ProviderError("invalid_profiles", "活动必须是非空文本列表。")
            months = row["best_season_months"]
            if (not isinstance(months, list) or not months
                    or any(type(month) is not int or not 1 <= month <= 12 for month in months)
                    or len(set(months)) != len(months)
                    or row["transport_difficulty"] not in {"low", "moderate", "high"}):
                raise ProviderError("invalid_profiles", "月份或交通难度不合法。")
            rows[row["id"]] = row
        if set(rows) != set(data.destinations):
            raise ProviderError("invalid_profiles", "描述目录必须恰好覆盖当前目的地目录。")
        self._profiles: dict[str, DestinationProfile] = {}
        for destination_id, destination in sorted(data.destinations.items()):
            if destination["is_mock"] is not True:
                raise ProviderError("invalid_dataset", "模拟目的地提供者只接受 is_mock=true 的目的地。")
            row = rows[destination_id]
            description_provenance = DataProvenance(
                source=payload["source"], source_ref=f"m9-destination-profiles:{destination_id}",
                version=payload["version"], updated_at=payload["updated_at"],
                confidence=payload["confidence"], is_simulated=True,
            )
            description = DestinationDescription(
                **{key: row[key] for key in ("display_name_zh", "destination_type", "seasonal_note",
                                             "transport_difficulty", "transport_note")},
                suitable_activities=tuple(row["suitable_activities"]),
                best_season_months=tuple(sorted(row["best_season_months"])),
                provenance=description_provenance,
            )
            optimization = DestinationOptimizationData(
                min_stay_nights=destination["min_stay_nights"],
                recommended_stay_nights=destination["recommended_stay_nights"],
                max_stay_nights=destination["max_stay_nights"],
                tag_scores=tuple(sorted(destination["tag_scores"].items())),
                season_scores_by_month=tuple(sorted(destination["season_scores_by_month"].items(), key=lambda item: int(item[0]))),
                provenance=optimization_provenance,
            )
            self._profiles[destination_id] = DestinationProfile(
                id=destination_id, name=destination["name"], timezone=destination["timezone"],
                airport_ids=tuple(sorted({access["airport_id"] for access in data.accesses.values()
                                          if access["destination_id"] == destination_id})),
                descriptive=description, optimization=optimization,
            )

    def list_destinations(self) -> tuple[DestinationProfile, ...]:
        return tuple(self._profiles.values())

    def get_destination(self, destination_id: str) -> DestinationProfile:
        try:
            return self._profiles[destination_id]
        except KeyError as exc:
            raise ProviderError("unknown_destination", f"目的地不在当前目录中：{destination_id}") from exc

    def match_preferences(self, destination_id: str,
                          weights: Mapping[str, int]) -> tuple[PreferenceMatch, ...]:
        """仅列出用户关注标签及既有匹配值；不产生新的路线推荐分数。"""
        profile = self.get_destination(destination_id)
        if not isinstance(weights, Mapping) or any(
            tag not in self._tags or type(value) is not int or not 0 <= value <= 100
            for tag, value in weights.items()
        ):
            raise ProviderError("invalid_preferences", "偏好只接受目录标签及 0–100 整数权重。")
        scores = dict(profile.optimization.tag_scores)
        return tuple(PreferenceMatch(tag, value, scores[tag])
                     for tag, value in sorted(weights.items(), key=lambda item: (-item[1], item[0]))
                     if value > 0)
