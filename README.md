# 随心航线 · AI 多目的地旅行决策原型

状态：M9-001 产品开发与交付验收进行中；Web 与 Streamlit 共用确定性优化器。[打开公开演示](https://travel-route-poc.vercel.app) · [GitHub仓库](https://github.com/yuyangjungle/travel-route-poc)。  
最后更新：2026-09-22

当前定位：**AI-assisted multi-objective travel decision prototype**。可选 LLM 层把自然语言转成经过验证的结构化条件；确定性搜索仍负责目的地、顺序、日期、停留和返程机场，并返回可解释的多方案对比。LLM 不生成路线或航班数据。

面向尚未决定全部目的地的旅客：说出旅行想法 → 检查并补全条件 → 确认搜索 → 比较最低价、少折腾和体验方案 → 查看理由与目的地资料。全部报价和目的地资料为模拟；不提供预订。面向 OTA 交流或产品面试，可从 [产品案例研究](docs/m9-case-study.md) 和 [三分钟演示流程](docs/m9-demo-flow.md) 开始。

M8 冻结 v1 的真实模型评估结论仍是 C。M9 使用独立版本 `m9-intent-v2`：缺失的机场、日期、天数和预算保持空白，用户补全并确认后才搜索。**v2 尚未经过独立可靠性评估，真实用户验证仍未开展。**

![随心航线首页](docs/screenshots/m9-landing.png)

[交付与测试报告](docs/m9-results.md) · [安装部署说明](docs/m9-deployment.md)

## 当前里程碑与授权范围

- M0：文档基础已完成。
- M1：固定样本、产品假设基准、验证工具与预期结果已完成。
- M2：确定性完整搜索、可行性检查、评分、Pareto 筛选与独立穷举对照已完成。
- M3-001：本地 Streamlit 表单、推荐卡片、4 个演示场景与浏览器验证已完成。
- M3-002：公开部署文件、Pilot UX、匿名下载记录和首轮用户测试包已完成。
- M4-001：验证计划、四项标准任务、匿名结果模板和分析指南已完成；尚无真实参与者数据。
- M5-001：40 个模拟目的地、43 个机场、3,935 条报价及 small/medium/large 性能基准已完成；优化器逻辑未改。
- M6-001：4 个真实决策场景、相对差值解释、目的地信息分层和方案对比已完成；优化器逻辑未改。
- M7-001：自然语言结构化提取、发现/手动双模式、确认式搜索流程和 AI 部署配置已完成；真实用户验证仍未执行。
- M8-001：冻结 144 条合成自然语言语料，建立分项质量、重复一致性、下游影响、时延与成本评估。
- M8-002：以 DeepSeek `deepseek-flash` 对 120 条 evaluation case 各运行 3 次；360/360 调用成功，24 条 holdout 未消费。schema-valid 52.778%、critical-field error 8.703%、manual correction 61.389%，结论为 C，不能直接进入 Pilot。
- M9-001：产品开发已获授权，新增同源 Web 演示、可编辑意图确认、结果比较和版本化数据提供者，保留本地 Streamlit；GitHub 发布与 Vercel 公开演示属于本轮交付范围，部署状态以上方实测状态为准。
- 当前 large 精确基准三次中位数约 7.0 秒；保留 exact DFS 为正确性基线，后续优化方案尚未实施。
- 当前仅接入可选的服务端模型 API；没有外部航班 API、真实报价、认证、数据库、抓取、支付或预订。公开演示不代表生产可用性或用户价值已得到验证。

## 文档导航

| 文档 | 职责 |
| --- | --- |
| [问题简报](docs/product/problem-brief.md) | 用户、问题、假设与验证方法 |
| [产品需求](docs/product/prd.md) | 范围、需求、指标、里程碑与未决假设的权威来源 |
| [架构](docs/architecture.md) | 模块、数据流、技术选择和需求映射 |
| [数据模型](docs/data-model.md) | 实体、关系、字段、时间与价格口径 |
| [算法](docs/algorithm.md) | 状态、搜索、约束、评分和正确性验证 |
| [决策记录](docs/decisions.md) | ADR 模板与已接受决策 |
| [项目规则](AGENTS.md) | Codex 与其他协作者的工作约定 |
| [M1 基准说明](docs/benchmarks.md) | 场景假设、手工预期、验证边界与维护方法 |
| [M2 结果与限制](docs/m2-results.md) | 模块、调用方法、逐场景结果和已知限制 |
| [演示指南](docs/demo.md) | 启动方法、4 个场景和截图 |
| [M3-001 结果](docs/m3-results.md) | UI 集成、验证证据和局限 |
| [用户测试手册](docs/user-testing.md) | 参与者、标准任务、问题与观察清单 |
| [Pilot 指标](docs/pilot-metrics.md) | 记录结构、指标与预设决策门槛 |
| [M3-002 结果](docs/m3-002-results.md) | 部署准备、验证证据和声明边界 |
| [M4 验证计划](docs/m4-validation-plan.md) | 假设、方法、可测边界与误导性指标 |
| [M4 分析指南](docs/m4-analysis-guide.md) | Evidence/Opinion/Decision 分层与编码规则 |
| [M4 结果模板](pilot_results_template.json) | 匿名任务、反馈和观察记录结构 |
| [M5 数据扩展](docs/m5-data-expansion.md) | 模拟目录、生成方式、真实性维度与边界 |
| [M5 性能报告](docs/m5-performance-report.md) | 三档测量、瓶颈与未来算法评审 |
| [M6 场景库](docs/m6-scenarios.md) | 四类用户意图、结构化输入和模拟数据边界 |
| [M6 演示流程](docs/m6-demo-flow.md) | 从模糊偏好到方案选择的可重复演示 |
| [M7 AI 决策原型](docs/m7-ai-decision-prototype.md) | AI 输入边界、双模式、失败处理与验证范围 |
| [M7 部署准备](docs/m7-deployment.md) | 本地配置、Community Cloud Secrets 和验收步骤 |
| [M8 评估方法](docs/m8-evaluation-methodology.md) | 冻结语料、错误分级、十项指标、下游比较与预设准入门槛 |
| [M8 结果](docs/m8-results.md) | 冻结真实模型指标、下游影响、风险与 readiness |
| [M8-002 真实模型评估](docs/m8-002-real-model-evaluation.md) | 冻结配置、真实调用结果、错误分类、下游影响与重算方法 |
| [M9 产品案例研究](docs/m9-case-study.md) | 问题、洞察、方案、架构、证据和后续方向 |
| [M9 演示流程](docs/m9-demo-flow.md) | 自然语言确认、固定场景、比较、无解和截图验收 |
| [M9 意图契约](docs/m9-intent-contract.md) | v2 空值语义、服务端配置、错误处理和可靠性边界 |
| [M9 数据提供者](docs/m9-providers.md) | FlightProvider、DestinationProvider、40 份资料和未来接入方法 |

## 运行 M8 AI 输入评估

```powershell
# 无密钥会写出明确 blocked 结果，且不会伪造指标
py -3.12 -m evals.m8.run --model $env:OPENAI_MODEL --case M8-001 --repeats 3

# 检查冻结语料与评估基础设施
py -3.12 -m unittest tests.test_m8 -v
```

主评估默认只运行 120 条 evaluation case；24 条 holdout 需要显式 `--include-holdout`。方法、价格参数和结果解释见 [M8 评估方法](docs/m8-evaluation-methodology.md)。

M8-002 已用 `--provider deepseek --model deepseek-flash` 完成冻结 evaluation。runner 仍支持 OpenAI 作为另一候选供应商；不同 provider 的结果独立记录。两者复用冻结 prompt/schema，密钥只从 `OPENAI_API_KEY` 或 `DEEPSEEK_API_KEY` 环境变量读取，不写入运行产物。真实结果与复算方法见 [M8-002 报告](docs/m8-002-real-model-evaluation.md)。

建议阅读顺序：问题简报 → PRD → 数据模型 → 算法 → 架构 → ADR。开始任何项目任务前先读 AGENTS.md。

## 启动产品演示

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn server:app --host 127.0.0.1 --port 8000
```

浏览器访问 `http://127.0.0.1:8000`。无需模型密钥即可载入四个场景或手动填写，再确认、搜索、比较。Web 搜索使用 **18 个目的地、264 条固定模拟报价**，日期集中于 **2027-10-01 至 2027-10-22**；完整描述目录有 40 个目的地，资料覆盖不等于航班报价覆盖。

启用自然语言时，在启动进程前通过服务端环境变量提供 `DEEPSEEK_API_KEY`，可选 `DEEPSEEK_MODEL=deepseek-flash`。配置项见 [.env.example](.env.example)；应用不会自动加载 `.env`。密钥不放入浏览器代码、提交、下载结果或截图。`AI_ENABLED=false` 可关闭 Web 模型入口，手动与示例模式仍可使用。

保留本地 Streamlit，用于历史演示与匿名反馈下载：

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

默认地址 `http://localhost:8501`。DeepSeek 配置走 M9 v2；未配置 DeepSeek 而仅配置 OpenAI 时保留历史 v1 兼容入口，其 M8 可靠性结论没有改变。旧阶段运行说明保留于 [M7 部署准备](docs/m7-deployment.md)。

## GitHub 与 Vercel 公开演示

本轮目标是发布可访问的产品演示。仓库根目录 [server.py](server.py) 是 FastAPI 入口，[vercel.json](vercel.json) 配置同一个应用托管静态页面和服务端接口；无需将 Streamlit WebSocket 服务迁移到 Vercel。

1. 将代码推送至 GitHub，排除 `.env`、`.streamlit/secrets.toml`、本地环境和任何凭据。
2. 将仓库连接到 Vercel，使用当前 FastAPI 入口与锁定依赖；服务端配置 `DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL` 和可选的 `AI_ENABLED`。
3. 发布后验证首页、`/api/health`、模型草稿、人工确认、固定场景、无解提示、桌面/移动端和浏览器控制台，再记录实际 URL 与版本。

部署成功必须由可访问页面和完整流程证明。单实例模型调用限流不能替代全局配额；当前没有持久化业务数据或生产容量承诺。公开展示与真实参与者研究是不同事项，M4 用户测试仍未执行。

## 运行 M2

~~~powershell
py -3.12 -m benchmark_tools.run_m2
py -3.12 -m benchmark_tools.run_m2 --scenario B01 --json
py -3.12 -m unittest discover -s tests -v
~~~

运行自己的完整 TripRequest JSON 文件：

~~~powershell
py -3.12 -m travel_core --request D:/path/to/request.json
~~~

该文件必须是请求对象本身，不能直接使用包含 request/witnesses 的基准场景文件。CLI 默认读取项目 data/m1；可通过 --data 指定其他符合数据模型的本地目录。结果包括全部可行路线、完整 Pareto 集和代表推荐。完整搜索无解时退出码仍为 0，并返回 no_feasible_in_dataset；输入/数据错误为非零。

## 保留的 M1 校验

要求 Python 3.12 和 `tzdata==2024.2`。本机已验证 Python 3.12.1；Windows 默认 `python` 当前指向 3.8，因此使用显式的 `py -3.12`。

```powershell
py -3.12 -m benchmark_tools.run
py -3.12 -m unittest discover -s tests -v
```

首次在其他环境运行时，可建立隔离环境并安装依赖：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m benchmark_tools.run
```

单独运行增加目的地场景或输出可复现 JSON：

```powershell
py -3.12 -m benchmark_tools.run --scenario B01
py -3.12 -m benchmark_tools.run --json
```

Linux/macOS 使用 Python 3.12 环境下的 `python` 执行相同模块。工具从自身位置解析项目路径，不需要网络访问。成功退出码 0，数据/预期不一致或未知场景退出码 1。运行器只读数据，不重写预期或生成行程。

发布前可在一次性 Python 3.12 虚拟环境中重新安装依赖并运行全部测试：

```powershell
py -3.12 scripts/verify_clean_environment.py
```

## 当前数据与验证结果

- M1 正确性 fixture 保持 9 个机场、6 个目的地、15 条固定单程报价、20 个场景。
- M5 独立目录包含 40 个目的地、43 个机场和 3,935 条确定性模拟报价；四季价格、停留建议、连接难度和七项偏好标签均有变化。
- 每个场景记录产品假设、FR/A 编号、报价子集、完整请求和手工预期。
- 20 个场景的真实搜索通过，完整可行集、指标与 Pareto 集均匹配独立穷举。
- M8-002 历史基线为 107 项自动化测试通过；M9 新增测试覆盖提供者、意图草稿、确认流程和 Web 边界，最终结果以本轮完整回归记录为准。
- [样本清单](data/m1/manifest.json) 与 [内容锁](benchmarks/fixtures.lock.json) 固定数据和场景版本；模拟票价不能用于购票。
- B01：增加吉隆坡一站，机票 ¥3,600 → ¥2,800，交通时间 14 → 18 小时，飞行日 2 → 3。
- M2 由数据自行发现上述路线；最优性仅限已加载数据、报价子集和离散模型。参见 [机器结果报告](benchmarks/m2-results.json)。
- M5 small/medium/large 分别扩展 71 / 1,026 / 12,177 个状态；large 在记录环境三次中位数约 7.0 秒，完整数字见 [性能报告](docs/m5-performance-report.md)。

```text
data/m1/                     固定目录、报价、评分参数与覆盖清单
data/m5/                     40 目的地模拟目录、生成器与季节快照
benchmarks/scenarios/         B01–B20：请求、假设、见证行程与预期
benchmarks/m5/                M5 规模定义与最近一次机器结果
benchmarks/fixtures.lock.json 规范化 JSON 的 SHA-256 内容锁
benchmark_tools/             数据/请求/见证行程校验及只读运行器
travel_core/                 状态、约束、评分、完整搜索、Pareto 与 CLI
travel_ui/                   演示加载、请求映射和推荐卡片视图模型
travel_ui/natural_language.py 严格 JSON Schema、LLM 适配与 TripRequest 校验边界
travel_product/              M9 v2 意图草稿、确认校验与 Web 服务编排
travel_data/                 带来源/版本/时间的模拟数据提供者
demos/                       4 个版本化交互演示配置
scenarios/m6/                4 个真实决策场景与固定 M5 报价集合
data/m6/                     仅供展示的模拟目的地描述信息
data/m9/                     40 个目的地的结构化模拟描述档案
app.py                       Streamlit 浏览器界面
server.py                    FastAPI 同源 Web 演示入口
web/                         产品页面、样式和浏览器交互
vercel.json                  Vercel Web 演示部署配置
scripts/                     一次性干净环境验证工具
tests/                       校验器的反例与可复现性测试
docs/benchmarks.md           M1 手工推导与基准边界
docs/m2-results.md           M2 验收结果和已知限制
docs/demo.md                 演示运行和截图
docs/m3-results.md            M3-001 验收结果
docs/user-testing.md          首轮用户测试协议
docs/pilot-metrics.md         Pilot 数据与决策规则
docs/m3-002-results.md        M3-002 验收结果
docs/m4-validation-plan.md    M4-001 假设、方法和决策规则
docs/m4-analysis-guide.md     M4-001 分析与解释规则
docs/m5-data-expansion.md     M5 数据构成、生成和模拟边界
docs/m5-performance-report.md M5 性能结果、瓶颈和未来算法评审
docs/m6-scenarios.md          M6 场景定义、格式和边界
docs/m6-demo-flow.md          M6 可重复产品演示流程
docs/m7-ai-decision-prototype.md M7 AI 输入与产品模式设计
docs/m7-deployment.md         M7 本地与 Streamlit 部署准备
pilot_results_template.json   匿名参与者任务与观察模板
```

## 核心边界

- 首个算法验证集约 8–10 个机场；MVP 原目标约 20–30 个亚洲机场。M5 使用 43 个机场作为超出原目标的压力评估，不代表生产覆盖。
- 出发机场 PVG、HGH，NKG 为可选扩展；旅行约 7–21 天，必去目的地加 0–4 个可选目的地。
- 单人、CNY、单程报价相加；预算指机票预算，不是旅行总预算。
- 不让 LLM 生成路线或判定可行性。模拟数据不能证明真实节省金额。
- 第一版采用预先准备的直飞或受保护联程报价；不自动拼接自行转机。

## 文档迭代规则

PRD 管“要做什么”，数据模型管字段和统计口径，算法管计算规则，架构管职责，ADR 管决策理由。修改一份文档时，检查受影响的其他文档；不在多处维护独立的假设列表。

未决假设统一见 [PRD 假设登记](docs/product/prd.md#未决假设登记)。本轮审查结果见 [PRD 一致性审查](docs/product/prd.md#consistency-review)。


