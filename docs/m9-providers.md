# M9 数据提供者与目的地资料

状态：M9-001 实现。全部报价、交通参数、季节、偏好与活动描述均为模拟。最后更新：2026-09-21。

## 目的与边界

[`travel_data`](../travel_data/providers.py) 提供 Python `Protocol` 接口，使应用可以按接口取得数据和来源。现有确定性优化器继续接收已校验的 `Dataset`。提供者不生成路线、不增加目标、不调整评分或 Pareto 选择，不在搜索中发出网络请求。

`SyntheticFlightProvider` 只读现有 M5 固定报价；`SyntheticDestinationProvider` 将 M5 优化参数与独立的 [40 份目的地描述](../data/m9/destination_profiles.json) 组合为展示资料。M5 文件与生成器没有改变。

## FlightProvider

```python
from datetime import date
from pathlib import Path
from benchmark_tools.validation import load_dataset
from travel_data import FlightQuery, SyntheticFlightProvider

data = load_dataset(Path("data/m5"))
provider = SyntheticFlightProvider(data)
result = provider.search(FlightQuery("PVG", "TWU", date(2027, 10, 3)))
for quote in result.offers:
    print(quote.price_minor, quote.currency, quote.observed_at)
    core_offer = quote.to_core_offer()
```

查询日期使用**出发机场当地日历日期**，因此悉尼午夜起飞不会被误放到上海日期的前一天。固定快照覆盖日期仍遵循既有 manifest 的 Asia/Shanghai 口径，查询边界在出发机场时区转换。报价按整数价格、起飞时间、ID 排序。所有时刻保留时区，金额使用 CNY 整数分。报价完整包含受保护联程的航段、自行转机标记、票价口径和原始采样时间。

| 输出 | 含义 |
| --- | --- |
| `ok` | 找到已加载的模拟报价 |
| `no_sample` | 覆盖声明内缺少该路线当日报价；不能推断没有真实航班 |
| `out_of_coverage` | 查询日期超出快照覆盖；未查询真实库存 |
| `ProviderError` | 输入不合法或机场未覆盖；`code` 可供调用层区分 |

`get_offer(id)` 可回查行程中的原始报价。`to_core_offer()` 返回与既有 FlightOffer 完全一致的独立副本；`to_dict()` 额外返回来源元数据。来源包含 `source`、`source_ref`、`version`、`updated_at`、`confidence` 和 `is_simulated`。报价的 `updated_at` 等于既有 `observed_at`，不会因刷新页面而变化，也不是价格仍然有效的承诺。

## DestinationProvider

```python
from travel_data import SyntheticDestinationProvider

destinations = SyntheticDestinationProvider(data)
profile = destinations.get_destination("SEMPORNA")
print(profile.descriptive.display_name_zh)
print(profile.descriptive.suitable_activities)
print(profile.optimization.recommended_stay_nights)
matching = destinations.match_preferences("SEMPORNA", {"diving": 100, "food": 40})
json_ready = profile.to_dict()
```

`list_destinations()` 按稳定 ID 顺序返回完整 40 个目的地。每个 profile 明确分为：

- `descriptive`：中文名称、目的地类型、活动、模拟适合月份、季节说明、交通难度（low/moderate/high）、交通说明与描述来源。
- `optimization`：从既有 Dataset 读取的最小/建议/最大停留夜数、标签分与月份分，以及该 Dataset 的来源和版本。

推荐停留数值没有在描述 JSON 中复制。展示月份不是天气事实，也不改变优化器已有月份分。描述中每一条季节说明都标记“模拟”，整个目录的置信状态是 `simulated_unverified`，不凭空编造置信百分比。

`match_preferences` 仅返回用户权重大于零的标签、原始权重与既有目的地标签分，按用户权重降序和标签 ID 排序。它不计算新的路线分数，零偏好返回空列表；未知标签、布尔值、非整数和越界权重均拒绝。这里的标签匹配用于解释，最终路线价值仍由现有优化器计算。

## 未来替换提供者

未来实现相同的 `FlightProvider` / `DestinationProvider` 方法，通过应用组合入口注入实现即可。接入真实来源需要单独授权和验证：

1. 在优化前取回并固定版本快照，保留供应商 ID、采集时间、来源、币种、税费与行李口径，明确覆盖缺口和有效期。不能把跨供应商、跨票价口径的数据静默混算。
2. 将报价规范化为现有 FlightOffer；多航段报价必须完整、时间相接，受保护联程不能由应用自行猜测。保持整数分金额、带偏移时刻和 IANA 时区。
3. 使用现有 Dataset 校验后交给优化器；搜索期间不重新取价。以相同快照、请求与评分版本复现结果。
4. 真实目的地资料需要独立来源、版本、更新时间和清晰置信定义；描述变化不自动修改优化参数。修改数值参数需要新的数据版本与回归验证。
5. API 凭据只由服务端提供者读取；接口返回值不含密钥、HTTP Authorization 或供应商客户端对象。当前模拟实现不读取任何凭据。

提供者协议预留实现替换能力，没有商业 API、缓存服务、数据库或后台任务。本次不验证真实价格、真实季节、安全性、签证或预订可用性。

## 验证

运行 `py -3.12 -m unittest tests.test_providers -v`，12 项测试通过。测试覆盖全部 3,935 条报价无损转换、40 个目的地完整资料与来源、当地日期边界、缺样本/超覆盖区别、非法输入拒绝、确定排序、数据副本隔离、描述与优化分离以及模拟标记。历史 M1/M2/M6/M8 检查由集成时的全量回归继续验证。
