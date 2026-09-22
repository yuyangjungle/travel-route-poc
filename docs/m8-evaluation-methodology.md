# M8 AI 输入可靠性评估方法

状态：方法与准入门槛已在真实模型结果之前冻结。  
版本：`m8-evaluator-v1` / `m8-golden-v1`  
日期：2026-09-17

## 评估边界

M8 只评估自然语言到既有 `TripRequest` 的转换。模型使用与应用相同的 `catalog_prompt()` 和 `extraction_schema()`；模型不会看到 `FlightOffer`、候选行程、评分或 Pareto 结果。模型的结构化结果仍须通过本地 `validate_extraction()` 与 `validate_request()`。通过确认的相同请求继续由未修改的确定性优化器处理。

语料全部为人工编写的合成请求，不含真实用户文本或个人信息。运行结果只重复保存 case ID、模型元数据、用量、时延及复现实验所需的原始结构化 JSON；不重复保存输入正文。API 调用明确使用 `store=False`。

## 冻结语料

`evals/m8/corpus-v1.json` 共 144 条：120 条 `evaluation`、24 条 `holdout`。默认执行器只运行 evaluation；holdout 只有显式传入 `--include-holdout` 才会运行。内容锁保存在 `evals/m8/manifest.json`，加载时校验 SHA-256。

| 维度 | 数量或覆盖 |
| --- | --- |
| 中文 / 英文 / 混合 | 66 / 26 / 52 |
| 预期合法 / 带未决项 / 应拒绝 | 96 / 44 / 4 |
| 字段严重度标注 | critical 485；major 132 |
| 日期 | 明确日期、整月、上中下旬及合法多解 |
| 语义 | 时长、近似预算、必去、优选、自然权重、转机容忍 |
| 风险 | 信息缺失、互相冲突、目录外地点、城市机场歧义、恶意或畸形输入 |

每个 case 包含输入、逐字段期望及错误严重度、合法替代解释、应识别的未决提及、预期是否需要人工修正。合法歧义通过 `acceptable_alternatives` 显式列出，不强行设成唯一答案。保留集用于提示词或 schema 版本变化后的独立检查；若针对 v1 逐例调参，必须发布新提示契约并使用未看过的保留集或新语料，不能把调参后的同一集合称为无偏评估。

## 错误分级

| 等级 | 定义 | 示例 |
| --- | --- | --- |
| harmless variation | 不改变已标注语义或只影响说明性文字 | summary 措辞不同；命中已登记的合法替代解释 |
| correctable error | 用户确认页可明显发现且不静默改变关键约束 | 次要偏好权重偏差、额外 unresolved 误报 |
| blocking error | 无法形成合法 TripRequest 或缺少继续所需信息 | schema 缺字段、本地校验失败 |
| dangerous semantic error | 结构合法但静默改变关键意图 | 错预算、日期、必去地点、出发机场或转机上限；漏报目录外必去地 |

错误分级和字段准确率同时保留；schema 合法不代表语义正确。

## 指标

1. **schema-valid response rate**：原始 JSON 能否通过应用的完整本地提取校验。
2. **field-level extraction accuracy**：只对 case 已标注字段逐字段统计，并显示分子、分母；合法 alternative 计正确。
3. **critical-field error rate**：critical 标注中的错误比例，另报 dangerous semantic 数量。
4. **unresolved/out-of-catalog detection**：按归一化包含匹配计算 precision、recall、TP、FP、FN。
5. **invalid-value rejection rate**：预期拒绝样本中被本地边界拒绝的比例。
6. **manual-correction-required rate**：出现 correctable、blocking 或 dangerous 错误的运行比例。
7. **repeated-run consistency**：分别报告原始结构化 JSON 完全一致率和最终 `TripRequest` 一致率。
8. **downstream itinerary stability**：只对同一 case 的不同合法 TripRequest 运行固定 `m5-small-offer-subset-v1`，分类为无实质影响、仅排序变化、可行集变化、导致无解、实质违背意图。
9. **latency**：单次 Responses API 调用的 mean、p50、p95；不包含优化器时间。
10. **estimated API cost**：基于响应 token usage 和运行时显式提供的每百万 token 单价。未提供价格时保持 null，不采用可能过期的内置价格。

不生成单一综合准确率，也不把不同严重度相互抵消。应拒绝样本只有 4 条，其比例必须连同小样本分母解释。

## 真实模型运行

```powershell
# 无密钥也可运行；会生成明确的 blocked 结果，不会伪造数据
py -3.12 -m evals.m8.run --model $env:OPENAI_MODEL --case M8-001 --repeats 3

# 主评估。价格必须取运行时确认的候选模型价格
py -3.12 -m evals.m8.run --model $env:OPENAI_MODEL --repeats 3 `
  --input-cost-per-million 0 --output-cost-per-million 0

# 开发子集
py -3.12 -m evals.m8.run --model $env:OPENAI_MODEL --category transfer_tolerance --limit 10
```

上例中的 `0` 仅是命令占位，正式测量必须换成记录日期时的真实单价。结果写入 `evals/m8/results/<timestamp>-<model>.json`，并更新 `latest.json`。结果记录请求模型名、服务返回的模型版本、UTC 时间、语料哈希、选择范围、重复次数、原始结构化结果、token、时延和成本。单次服务失败保留稳定错误类型，不保存异常正文。

比较第二个模型时，两个文件必须具有相同语料哈希、case 顺序和重复次数：

```powershell
py -3.12 -m evals.m8.compare result-a.json result-b.json --output tradeoff.json
```

比较文件并列显示质量、关键错误、一致性、时延、成本和下游稳定性，`decision` 固定为空，不自动选胜者。

## 下游影响口径

原始输出先经应用校验，只有两个以上不同且合法的 `TripRequest` 才进入比较。执行器删除仅用于追踪的 `request_id` 后去重，使用未修改的 `travel_core.search()` 和固定 M5 small 报价子集。行程 ID 集用于比较完整可行集，Pareto 与代表结果的有序 ID 用于判断排序变化。任何一侧已经出现 dangerous semantic error 时，差异优先归类为“实质违背用户意图”，避免用偶然相同的路线掩盖错误请求。

这项分析只说明模拟数据与离散模型内的产品影响，不证明真实机票或用户价值。

## 预先冻结的 Pilot 准入门槛

候选模型只有同时满足以下条件，才可进入**仍带逐字段人工确认**的 3–5 人 Pilot：

- evaluation 120 条、每条至少 3 次，运行无系统性服务失败；
- schema-valid ≥ 99%；
- critical-field error ≤ 1%，dangerous semantic error = 0；
- unresolved detection precision ≥ 95% 且 recall ≥ 95%；
- invalid-value rejection = 100%（同时报告 4 条的小分母）；
- manual-correction-required ≤ 10%；
- 最终 TripRequest 重复一致率 ≥ 98%；
- 不出现“实质违背意图”的下游差异；导致无解的重复差异 = 0，可行集变化 ≤ 2%；
- API latency p95 ≤ 10 秒；
- 估算成本 ≤ USD 0.02/请求，且价格假设已记录。

任何一项未满足都不能通过其他指标平均补偿。即使全部满足，首次 Pilot 仍保留确认页；移除确认属于以后独立的风险决策。

## 验证与变更规则

`tests/test_m8.py` 检查语料数量、覆盖、哈希、严重度、字段与 schema 分离、非法值拒绝、下游分类、无密钥阻断以及无自动赢家的模型比较。全套回归仍必须运行 M1 内容锁、M2 oracle 和 M6 场景测试。提示词或 schema 变更须提升 `prompt_contract_version`，冻结新结果，并使用未参与修改的 holdout；不得覆盖旧结果。
