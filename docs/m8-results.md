# M8 AI 输入可靠性结果

状态：**M8-002 已完成；DeepSeek `deepseek-flash` 在冻结 v1 边界上判定为 C，尚未准备进入 Pilot。**  
日期：2026-09-17

## 测量范围

`m8-golden-v1` 共 144 条合成 case：evaluation 120、holdout 24。此次只运行 evaluation，每条重复 3 次，共 360 次真实调用；holdout 未消费。冻结的 corpus、prompt、Structured Output schema、parser/local validation、标签、优化器、评分、Pareto 和模拟数据均未改。

完整配置、错误样例、下游分析和产物索引见 [M8-002 真实模型评估](m8-002-real-model-evaluation.md)。机器结果见 [results.json](../evals/m8/runs/m8-002-20260917T063521Z-deepseek-deepseek-flash/results.json)。

## 结果摘要

| 指标 | 结果 |
| --- | ---: |
| API 成功 | 360 / 360（100.000%） |
| schema-valid | 190 / 360（52.778%） |
| critical-field error | 106 / 1,218（8.703%） |
| unresolved precision / recall | 58.696% / 19.565% |
| invalid-value rejection | 12 / 12（100.000%） |
| manual correction required | 221 / 360（61.389%） |
| raw exact 三次一致 | 0 / 120（0.000%） |
| 有效 TripRequest 三次一致 | 8 / 120（6.667%） |
| dangerous semantic error | 34 次 |
| latency median / p95 | 4.696 s / 11.775 s |
| 估计成本 | USD 0.6596988 total；USD 0.0018325/call |

## 产品影响

43 条 case 产生了多个不同且合法的 TripRequest，因此进入下游确定性优化器比较：31 条无实质行程影响，4 条导致无可行解，8 条实质违背用户意图。模型调用成功率和成本表现良好，但自然语言边界的语义可靠性不足。

手动确认仍然必要。当前输出不能在未经逐字段确认的情况下直接运行优化器，也不能作为真实用户 Pilot 的默认入口。

## 主要风险

- unresolved 漏报 111 项，尤其集中在缺失日期、冲突约束、目录外目的地和城市/机场歧义。
- 模糊日期存在自行扩张或移动范围的问题。
- 必去和偏好目的地存在遗漏；自然语言偏好权重准确率为 0%。
- 三次重复运行的一致性很低，导致 4 个 case 无解、8 个 case 实质偏离意图。
- 语料为团队合成，仍不能代表真实用户分布；当前结果只衡量冻结 v1 契约。

## Readiness

预设门槛中，仅全部调用完成、非法值拒绝、成本和 feasible-set-only 变化通过；schema、critical error、dangerous error、unresolved、人工修正、一致性、下游意图影响和 p95 时延均未通过。

结论：**C. Not ready; prompt/schema iteration required**。

## 下一里程碑建议

M8-003 应进行版本化 prompt/schema 迭代：保留 v1 历史，使用独立开发集形成 v2，再用未查看的 holdout 或新增独立语料做一次性验收。当前不进入真实用户 Pilot，不接入真实航班数据，不新增功能。
