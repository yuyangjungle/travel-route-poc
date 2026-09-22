# M8-002 冻结配置真实模型评估

状态：**已完成；结论为 C（尚未准备进入 Pilot）**  
日期：2026-09-17  
运行记录：`m8-002-20260917T063521Z-deepseek-deepseek-flash`

## 结论

冻结的 120 条 evaluation case 各运行 3 次，共 360 次 DeepSeek API 调用。全部调用成功，24 条 holdout 未消费；prompt、Structured Output schema、解析/校验边界、语料标签、优化器和模拟数据均未修改。

传输可靠性和非法值拒绝达标，但语义可靠性没有达到预先冻结的 Pilot 门槛：schema-valid 仅 52.778%，critical-field error 8.703%，manual correction required 61.389%，有效 TripRequest 三次一致率 6.667%，并出现 34 次 dangerous semantic error。结论是 **C. Not ready; prompt/schema iteration required**。自然语言输入仍必须经过逐字段人工确认，不能直接触发优化器。

## 冻结运行配置

| 项目 | 值 |
| --- | --- |
| Provider / model | DeepSeek / `deepseek-flash` |
| API | `https://api.deepseek.com` Responses API |
| Structured Output | 应用同一 JSON Schema，`strict=True`、`store=False`、无 tools |
| 主集合 | evaluation 120 条；holdout 24 条未消费 |
| 重复 | 每条 3 次，共 360 次 |
| 调用顺序 | repeat-major，再按冻结语料顺序 |
| Python / SDK | Python 3.12.1 / OpenAI SDK 3.14.1 |
| SDK 自动重试 | 0 |
| 显式重试 | 最多 2 次，仅连接、超时、408/409、429、5xx；逐次记录 |
| 计价假设 | input USD 0.30 / 1M，output USD 1.20 / 1M；按保守 peak、cache-miss 价格 |

DeepSeek 的 Responses API 和 Structured Outputs 支持依据 [DeepSeek Responses API](https://api-docs.deepseek.com/guides/responses_api/)；费用依据运行日 [DeepSeek Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)。这里报告的是按冻结单价重算的估计值，不是账单金额。

API key 仅注入运行进程的环境变量。manifest 只保存环境变量名称和 `api_key_configured=true`，不保存密钥；原始响应、报告和文档均不含密钥。

## 冻结边界

| 边界 | SHA-256 |
| --- | --- |
| corpus-v1 | `3baccdb0c19481284abf8151e2144e6e63623872574152a56cbf9093db5a8bd7` |
| prompt 内容 | `8819aa3b2d16467e3ffc34d1b7e88301edb3fa7249cade17e4bba9ae5a7e0697` |
| Structured Output schema | `42060949fcbd4b688a21eb8b7a174101b96afc24635d57bba1e35e31b0968ebc` |
| parser/AI boundary source | `1e48d0a6252f797f8ffc3dac16c5d8da01f0ad12801efb1be0af2e554d5b358c` |
| M8-001 evaluator | `d739057477c13ed22a1c3133bc49d895aa3246f38f81da3495687397f505f189` |
| optimizer bundle | `7f3b272b9e8894e2abcd63f1423c3d4b7eb35f7d5c821bf6c04da102ae4f5889` |
| M5 synthetic data bundle | `4c5a69927ca7c4bff0aa7f6e4520b74285c1c4a9ba3b6bea26f53567e214ec40` |

权威快照为 [frozen-boundary-v1.json](../evals/m8/frozen-boundary-v1.json)。runner 在任何真实调用前校验这些哈希。

## API 执行与核心指标

| 指标 | 分子 / 分母 | 结果 |
| --- | ---: | ---: |
| API 调用成功 | 360 / 360 | 100.000% |
| schema-valid response | 190 / 360 | 52.778% |
| critical-field error | 106 / 1,218 | 8.703% |
| unresolved precision | TP 27 / (TP 27 + FP 19) | 58.696% |
| unresolved recall | TP 27 / (TP 27 + FN 111) | 19.565% |
| invalid-value rejection | 12 / 12 | 100.000% |
| manual correction required | 221 / 360 | 61.389% |
| raw exact 三次一致 | 0 / 120 case | 0.000% |
| 有效 TripRequest 三次一致 | 8 / 120 case | 6.667% |

字段准确率：origin 93.537%，window start/end 各 90.000%，min/max duration 89.583% / 91.111%，budget 90.123%，required destination 90.000%，preferred destination 85.000%，connection tolerance 86.667%，preference weights 0.000%。每项使用其冻结标注分母，不能把这些比率合并为一个总准确率。

## 错误分类与关键样例

360 次结果中：139 次无 material error，158 次 blocking error，29 次 correctable error，34 次 dangerous semantic error；因此 221 次需要人工修正。

主要失败模式：

- schema/local validation 不通过导致 170 次非 schema-valid 结果。常见原因包括字段集合不完整或多余、预算/连接数/行程天数类型或范围无效、起点或返程机场为空，以及假设数量超过边界。
- 模糊日期被模型自行扩张或移动。例如 M8-037 repeat 1 把冻结期望的 2027-10-10 结束日提取为 2027-10-20；M8-039 repeat 1 把 2027-10-21 开始日提取为 2027-10-15。
- 未决信息识别严重漏报：111 个 false negative。M8-101/M8-105 漏报缺失日期；M8-109/M8-112/M8-118 漏报冲突约束。
- 目录外目的地和城市/机场歧义经常只识别其中一项。M8-123/M8-125/M8-127/M8-129 在识别目录外目的地的同时漏掉机场歧义，构成危险语义错误。
- 自然语言偏好权重的逐字段准确率为 0%，说明当前冻结契约对权重映射缺乏稳定、可复现的语义。

上述 case ID 可在冻结 corpus 和 [脱敏失败清单](../evals/m8/runs/m8-002-20260917T063521Z-deepseek-deepseek-flash/failures-redacted.json)交叉检查。失败清单省略合成输入正文，但保留字段差异、严重度和未决项 TP/FP/FN。

## 下游影响

只有同一 case 在重复运行中产生多个不同且合法的 TripRequest 时才运行确定性优化器比较；120 条中有 43 条符合该条件。

| 下游影响 | 数量 / 43 | 比例 |
| --- | ---: | ---: |
| 无实质行程影响 | 31 | 72.093% |
| 导致无可行解 | 4 | 9.302% |
| 实质违背用户意图 | 8 | 18.605% |

没有观察到只改变排序或只改变可行集的独立分类。这里的分母是 43，不是全部 120 case。31 条无实质影响说明优化器对部分解析差异具有容忍度，但 4 条无解和 8 条意图违背足以阻止直接进入 Pilot。

## 时延、token 与估计成本

| 项目 | 结果 |
| --- | ---: |
| Latency median / p90 / p95 | 4,696 / 9,894 / 11,775 ms |
| Latency min / max / mean | 2,055 / 25,263 / 5,794 ms |
| Input tokens | 543,564 total；1,510 mean/call |
| Output tokens | 413,858 total；1,150 mean/call |
| Combined tokens | 957,422 total；2,660 mean/call |
| 估计总成本 | USD 0.6596988 |
| 估计每次成功调用 | USD 0.0018325 |

成本门槛通过；p95 11.775 秒超过冻结的 10 秒门槛。

## Readiness 判定

通过：全部调用完成、非法值 100% 拒绝、feasible-set-only 变化不超过 2%、估计单次成本不超过 USD 0.02。

未通过：schema-valid ≥99%、critical error ≤1%、dangerous semantic error = 0、unresolved precision/recall ≥95%、manual correction ≤10%、TripRequest consistency ≥98%、material intent violation = 0、no-feasible change = 0、p95 latency ≤10 秒。

因此按预先冻结门槛判定为 **C. Not ready; prompt/schema iteration required**。这不是对 DeepSeek 通用能力的评价，只是该模型在当前 v1 prompt/schema/catalog/validator 组合上的测量结果。

## 可复现产物

- [不可变运行清单](../evals/m8/runs/m8-002-20260917T063521Z-deepseek-deepseek-flash/manifest.json)
- [逐调用原始记录](../evals/m8/runs/m8-002-20260917T063521Z-deepseek-deepseek-flash/calls.jsonl)
- [机器可读结果](../evals/m8/runs/m8-002-20260917T063521Z-deepseek-deepseek-flash/results.json)
- [脱敏失败清单](../evals/m8/runs/m8-002-20260917T063521Z-deepseek-deepseek-flash/failures-redacted.json)
- [完成状态](../evals/m8/runs/m8-002-20260917T063521Z-deepseek-deepseek-flash/status.json)
- [M8-001 历史 blocked 结果](../evals/m8/results/20260917-m8-001-blocked.json)

仅使用保存输出重新评分：

```powershell
py -3.12 -m evals.m8.run_real `
  --score-run evals/m8/runs/m8-002-20260917T063521Z-deepseek-deepseek-flash `
  --input-cost-per-million 0.30 --output-cost-per-million 1.20
```

复算后的 metrics、failure taxonomy 和 readiness 规范化摘要与首次结果完全一致，SHA-256 为 `6ffda0c9ff8a1d627427b0b3d2def0158ec161d92b7d9383a13631b9f4076474`。

## 下一里程碑建议

建议 M8-003 只做版本化的 prompt/schema 可靠性迭代。保留 v1 结果；在独立开发集上形成 v2，再使用尚未查看的 24 条 holdout 或新增独立语料做一次性验证。不要在本轮 v1 corpus 上调参后把同一分数当作无偏结果，也不要进入真实用户 Pilot、接入真实航班数据或新增产品功能。
