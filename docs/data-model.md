# 核心数据模型

状态：M2 核心结果模型已实现；M5 优化目录沿用 schema 1.0.0；M6/M7 展示与 AI 输入均不改变核心模型。示例均为模拟。  
最后更新：2026-09-17

## 术语与统计口径

| 术语 | 定义 |
| --- | --- |
| 目的地 Destination | 旅客实际访问的旅行地点，可与一个或多个机场关联 |
| 机场 Airport | 航空交通节点；机场中转不算访问目的地 |
| 单程报价 FlightOffer | 可独立计价的一个单程出行选项，包含一个或多个航段 |
| 航段 FlightSegment | 一次起飞到落地；只携带时刻，不重复计价 |
| 访问 Visit | 经接驳到达目的地并满足最低停留要求的一次停留 |
| 总行程天数 trip_days | 末段落地日期 − 首段起飞日期 + 1，统一 Asia/Shanghai |
| 停留夜数 stay_nights | 目的地 departure_at 与 arrival_at 的本地日期差；不等于酒店账单 |
| 有效游玩分钟 usable_minutes | 访问区间与每天当地 08:00–20:00 的重叠分钟，初始近似规则；不代表活动安排 |
| 交通总时长 transit_minutes | 各单程报价首段起飞到末段落地的分钟数 + 目的地两端接驳分钟；含报价内中转等待，不含提前到机场缓冲 |
| 飞行日数 flight_day_count | 所有航段占用的 Asia/Shanghai 日期集合大小；按半开区间 [起飞, 落地) 计数，避免午夜落地多记一天 |
| 夜间飞行分钟 night_flight_minutes | 航段与 Asia/Shanghai 每天 00:00–06:00 重叠分钟之和，固定比较口径 |

全局时间比较统一转 UTC；保留原偏移和机场 IANA 时区。金额用 CNY 整数分。不存在的报价不生成隐式边。

## 实体与字段

### TripRequest

- `request_id: string`
- `origin_airport_ids: string[]`、`return_airport_ids: string[]`：非空；返程默认复制出发集合。
- `window_start_date: YYYY-MM-DD`、`window_end_date: YYYY-MM-DD`：包含边界，按 `reference_timezone`。
- `reference_timezone: string`：MVP 固定 `Asia/Shanghai`。
- `min_trip_days: int`、`max_trip_days: int`：7 ≤ min ≤ max ≤ 21。
- `flight_budget_minor: int`：正整数分；`currency: "CNY"`；`passenger_count: 1`。
- `fare_profile_id: string`：必须与数据集和报价一致。
- `required_destination_ids: string[]`、`preferred_destination_ids: string[]`：各自唯一且两集合不交叉；可以为空。
- `preference_weights: object<string, int>`：固定标签字典上的 0–100 权重，可全为零。
- `max_optional_destinations: int`：0–4；优选目的地仍属于可选地点，未选择不导致不可行。
- `max_connections_per_offer: int`：非负，指每个报价内航段数减一。
- `allow_self_transfer: bool`：首期只支持 false，true 返回 `unsupported_request`。

必去集合为空时仍须至少访问一个目的地；此时 optional 上限为 0 会产生数据模型下的无可行解。未覆盖 ID 为覆盖错误，不自动删除。

### Airport

`id: string`（IATA）、`name: string`、`timezone: string`（IANA）。

### Destination

- `id: string`、`name: string`、`timezone: string`。
- `tag_scores: object<string, int>`：各标签 0–100；必须包含数据集声明的全部标签，未知请求标签拒绝，不能自行猜测。
- `season_scores_by_month: object<string, int>`：月份 1–12，对应 0–100；按访问开始的当地月份取值。
- `min_stay_nights: int`、`recommended_stay_nights: int`、`max_stay_nights: int`：1 ≤ min ≤ recommended ≤ max。
- `min_usable_minutes: int`：最低有效游玩分钟，非负。
- `metadata_source: string`、`is_mock: bool`：人工规则来源与模拟标记。

### DestinationAccess

`id`、`destination_id`、`airport_id`、`to_destination_minutes`、`to_airport_minutes`、`to_destination_cost_minor`、`to_airport_cost_minor`、`source_note`、`is_mock`。

分钟和费用为非负整数估计。第一版一次访问通过同一条 access 进出；接驳假定固定耗时且任意时刻可用，这是模拟近似，不能推断实际接驳班次。真实样本须复核这一假设。

### FlightSegment

`id`、`origin_airport_id`、`destination_airport_id`、`departure_at`、`arrival_at`。

时间为带偏移的 ISO 8601 字符串。`arrival_at > departure_at`。单程报价内相邻航段机场相接，时间递增；不接受机场间换乘。

### FlightOffer

- `id`、`origin_airport_id`、`destination_airport_id`。
- `departure_at`、`arrival_at`：分别等于首航段起飞和末航段落地。
- `segments: FlightSegment[]`：至少一个。
- `price_minor: int`、`currency: "CNY"`、`fare_profile_id`。
- `connection_count: int`：等于 segments 长度减一。
- `self_transfer: bool`：首期必须 false；`protected_connection: bool`：多个航段时必须 true，表示作为完整联程报价导入。
- `source_type: "mock" | "manual"`、`source_ref: string`、`observed_at: datetime`。

受保护联程不由搜索拼接；数据提供者负责确认其内部衔接有效。报价含税且为指定单人口径。报价年龄只展示，不推断仍可购买。

### DatasetManifest

`dataset_id`、`dataset_version`、`schema_version`、`created_at`、`currency`、`fare_profile_id`、`fare_profile_description`、`preference_tag_ids`、`covered_airport_ids`、`covered_destination_ids`、`coverage_start_date`、`coverage_end_date`、`coverage_kind: "synthetic" | "manual_sample" | "mixed"`、`coverage_notes`。

覆盖日期按 Asia/Shanghai 的起飞日记载。清单只是声明采样范围，不保证机场之间逐日全覆盖。手工整理的缺口和范围限制写入 coverage_notes。

### ScoringConfig 与 SearchConfig

- `ScoringConfig`：`scoring_version`、`airport_buffer_minutes`、`usable_day_start_local`、`usable_day_end_local`、`night_start_reference`、`night_end_reference`、`preferred_bonus_points`、`burden_coefficients`、`normalization_scales`、`ranking_weights`、`score_rounding_digits`。初始值见算法文档。
- `SearchConfig`：`search_version`、`mode: "exact" | "bounded"`、`max_expanded_states: int | null`、`tie_break_rule`。bounded 是预留模式，M3 决策前不实现；exact 不设状态截断。

首期不按墙钟超时截断确定性运行。若以后引入墙钟截止，应单独声明不保证候选集完全可复现。

### Visit

`destination_id`、`access_id`、`inbound_offer_id`、`outbound_offer_id`、`arrival_at`、`departure_at`、`stay_nights`、`usable_minutes`、`experience_points`。

arrival_at = 入境报价落地 + 到目的地接驳；departure_at = 下一报价起飞 − 机场缓冲 − 返机场接驳。机场缓冲来自版本化 ScoringConfig，虽位于同一配置中，它是可行性参数，不是软评分权重。

### Itinerary

`id`、`origin_airport_id`、`return_airport_id`、`offer_ids`（有序）、`visits`（有序）、`first_departure_at`、`last_arrival_at`、`trip_days`、`flight_total_minor`、`ground_total_minor`、`transit_minutes`、`flight_dates`（去重有序）、`flight_day_count`、`connection_count`、`night_flight_minutes`、`burden_points`、`experience_points`、`preference_match_points`、`recommendation_score`、`recommendation_labels`、`tradeoffs`。

所有累计指标由引用数据复算；同一报价只计价一次。preference_match_points 等于体验模型中匹配与停留收益之和，首期与 experience_points 相同，不另建第四目标。id 由规范化的报价序列、访问序列及版本生成稳定标识。

### SearchResult

- `status: "ok" | "invalid_request" | "unsupported_request" | "invalid_dataset" | "insufficient_coverage" | "no_feasible_in_dataset" | "search_incomplete"`。
- `request`、`dataset_id`、`dataset_version`、`schema_version`、`scoring_version`、`search_config`。
- `search_complete: bool`、`optimality_scope: "dataset_and_discrete_model" | "found_candidates_only" | "none"`。
- `itineraries: Itinerary[]`、`coverage_warnings: string[]`、`diagnostics: object`。
- `run_summary`：`expanded_states`、`feasible_count`、`pareto_count`、`elapsed_ms`；耗时不属于可复现结果比较字段。

未覆盖请求机场、目的地或超出声明日期范围返回 insufficient_coverage。范围内稀疏缺边仅警告。完整搜索且无解返回 no_feasible_in_dataset；截断运行无论是否找到解都返回 search_incomplete。已拒绝的请求 optimality_scope 为 none；精确无解也只说明数据模型内无解。

校验失败而尚无法构造类型化请求或识别版本时，相应 request/版本字段允许为 null，diagnostics 必须给出字段级原因；itineraries 为空、search_complete=false、run_summary 中计数为 0。成功加载并搜索时版本字段不得缺省。

## 关系

### M2 结果扩展与精度

SearchResult 已在核心实现。除原有字段外，feasible_itineraries 保存全部可行行程，pareto_itineraries 保存完整非支配集，itineraries 保存至多 5 个代表结果。前两者用于正确性审计，不是未来界面必须展示的全部内容。

allowed_offer_ids 记录实际搜索子集，data_fingerprint 记录该子集及目录/参数的内容哈希。None 表示全部报价，空集合表示没有报价，不能混淆。错误请求尚无法归一化时 request 为 null。

M2 不测墙钟性能，run_summary.elapsed_ms 为 null；状态数和结果数为确定性整数。bounded/search_incomplete 仍只保留在规格中，当前没有截断模式。

评分字段及 Visit.experience_points 使用六位十进制字符串。总体验由未舍入分项相加后舍入，可能与展示分项之和存在最末位差异。ID 标识同一版本下的报价/访问路径，不编码用户偏好；跨请求比较还必须核对外层 request、数据指纹和评分版本。

tradeoffs 是相对本次 best_overall 的结构化记录，包含基准行程 ID、机票差额、交通分钟差和飞行日差，不声称比较旅行总开销。航班通过 offer_ids 回查原始 FlightOffer。

### 实体关系

- Destination 与 Airport 通过 DestinationAccess 多对多关联。
- FlightOffer 连接两个 Airport，包含一个或多个 FlightSegment。
- TripRequest 引用 Airport、Destination 与统一的 fare_profile_id。
- Itinerary 引用一个有序 FlightOffer 序列及 Visit 序列。
- Visit 引用 DestinationAccess、入境和出境报价；中间报价可以同时是前一 Visit 的出境与后一 Visit 的入境。
- SearchResult 绑定一个请求、数据快照、参数版本与搜索配置。

## 示例模式

以下是完整请求示例与报价示例；DEMO_ISLAND 是虚构目的地 ID，需由模拟数据集定义，时刻和费用均不可用于购票。

```json
{
  "request_id": "demo-001",
  "origin_airport_ids": ["PVG", "HGH"],
  "return_airport_ids": ["PVG", "HGH"],
  "window_start_date": "2026-10-01",
  "window_end_date": "2026-10-31",
  "reference_timezone": "Asia/Shanghai",
  "min_trip_days": 12,
  "max_trip_days": 18,
  "flight_budget_minor": 500000,
  "currency": "CNY",
  "passenger_count": 1,
  "fare_profile_id": "demo-adult-tax-no-checked-bag-v1",
  "required_destination_ids": ["DEMO_ISLAND"],
  "preferred_destination_ids": [],
  "preference_weights": {"island": 100, "diving": 80, "food": 60},
  "max_optional_destinations": 4,
  "max_connections_per_offer": 1,
  "allow_self_transfer": false
}
```

```json
{
  "id": "mock-offer-001",
  "origin_airport_id": "PVG",
  "destination_airport_id": "BKI",
  "departure_at": "2026-10-03T09:00:00+08:00",
  "arrival_at": "2026-10-03T13:30:00+08:00",
  "segments": [{
    "id": "mock-segment-001",
    "origin_airport_id": "PVG",
    "destination_airport_id": "BKI",
    "departure_at": "2026-10-03T09:00:00+08:00",
    "arrival_at": "2026-10-03T13:30:00+08:00"
  }],
  "price_minor": 95000,
  "currency": "CNY",
  "fare_profile_id": "demo-adult-tax-no-checked-bag-v1",
  "connection_count": 0,
  "self_transfer": false,
  "protected_connection": false,
  "source_type": "mock",
  "source_ref": "synthetic-fixture-001",
  "observed_at": "2026-09-15T12:00:00+08:00"
}
```

## 校验与未决项

加载时校验 ID 唯一、引用存在、时间偏移与时区一致、价格非负、配置范围合理及报价内连接完整。不同来源可以并存，但不可混用不同币种或行李口径。上面的示例为说明性模式；实际 M1 数据见 [样本清单](../data/m1/manifest.json)。

### M1 基准交换格式

M1 请求为完全展开的字段，不实现表单默认值填充。报价时刻采用分钟精度，时区规则固定为 tzdata==2024.2。实际模拟报价统一使用 manifest 中的 `adult-cny-tax-cabin7kg-no-checked-v1`，与上方说明性示例不同。

见证行程仅含 `id`、有序 `offer_ids`、有序 `access_ids` 以及手工 `expected`。每两个报价之间对应一次访问；工具独立推导访问时间和指标，不信任预填累计值。该格式不等同于产品 Itinerary，也不要求预填推荐标签。

场景包含 `id`、`title`、`hypothesis`、`requirement_ids`、`assumption_ids`、`offer_ids`、`request`、`expected_request_status`、`expected_search_status`、`witnesses`、`comparisons`，以及可选 `infeasibility_certificate`。其报价集合是共享数据集的显式子集。

运行报告 scope=`prepared_witness_validation_only`、optimizer_executed=false，不伪装为 SearchResult。评分值输出为六位小数的十进制字符串以避免 JSON 浮点误差；其他时间/费用指标为整数。拒绝输入时不执行见证校验。详见 [基准说明](benchmarks.md)。

接入清单/季节与停留参数见 A-02，费用口径见 A-04，夜数近似见 A-07，覆盖见 A-08；均统一登记在 [PRD](product/prd.md#未决假设登记)。

### M5 扩展目录

M5 没有增加实体或字段。`data/m5` 使用相同 schema 1.0.0，标签字典扩展为七项，并通过现有 `preference_tag_ids` 声明；请求只能使用当前数据集声明的标签。四个季节报价快照使用独立 FlightOffer 表达，连接难度继续由报价航段、`connection_count` 和在途时间表达。目录规模、生成规则和模拟边界见 [M5 数据扩展](m5-data-expansion.md)。

### M6 展示描述目录

M6 不修改优化数据模型。`data/m6/destination_descriptions.json` 使用独立的 `m6-destination-description-v1` 展示模式，以 Destination ID 关联显示名、目的地类型、适合活动、季节说明、交通难度和交通说明。该目录只由 `travel_ui` 读取，不是 DatasetManifest 的组成部分，也不进入搜索、约束、评分或 Pareto 计算。

推荐停留夜数、月份季节系数和机场接驳分钟仍只来自优化目录。展示描述不得重复维护这些数值，以保持单一来源。场景结构见 [M6 场景库](m6-scenarios.md)。

### M7 AI 提取信封

LLM 返回的是进入 TripRequest 前的临时提取信封，包括可映射字段、`summary`、`assumptions` 和 `unresolved_mentions`。它不是优化数据实体，不会写入 Dataset、SearchState 或 SearchResult。

本地边界把预算元转换为 `flight_budget_minor`，补入固定币种、乘客、时区、fare profile 和 `allow_self_transfer=false`，然后调用同一个 `validate_request`。`summary`、假设、模型名和原始文本不会进入优化器。
