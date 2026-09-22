# M9 意图草稿契约

版本：`m9-intent-v2`。状态：产品演示边界，尚未通过独立可靠性验收。它与冻结的 M7/M8 `travel_ui/natural_language.py` 分离，M8 的 C 结论继续有效，不能用本轮组件测试声称模型准确率提高。

## 从想法到确认

`travel_product.intent.extract_intent(text, data, descriptions, *, api_key=None, model=None, client=None)` 返回 JSON 可序列化的草稿。它只接收机场/目的地名称和偏好标签目录，不发送 `FlightOffer`、行程、报价或优化结果。模块不调用搜索。

返回字段为 `contract_version`、`fields`、`summary`、`assumptions`、`unresolved_mentions`、`missing_fields`、`model`。`missing_fields` 由服务端根据空值计算，不相信模型自行声称信息完整。确认页必须让用户修正并明确确认后，才能构造完整 `TripRequest`，执行现有 `validate_request` 和确定性搜索。草稿不是 `TripRequest`。

| fields 字段 | 值与缺失语义 |
| --- | --- |
| origin_airport_ids / return_airport_ids | PVG/HGH/NKG 子集；未说明或机场歧义时 null |
| window_start_date / window_end_date | 合法 YYYY-MM-DD 或 null；没有年份不能自动改为2027年 |
| min_trip_days / max_trip_days | 7–21 整数或 null；“12天左右”不能伪装成明确上下限 |
| flight_budget_cny | 正整数或 null；只表示单人机票 CNY 预算 |
| required_destination_ids / preferred_destination_ids | 目录 ID 数组；不重叠；未说明为 [] |
| preference_weights | 当前全部标签的0–100整数；未说明时0并披露假设 |
| max_optional_destinations | 0–4；未说明时2并披露假设 |
| max_connections_per_offer | 0–2；未说明时1并披露假设 |
| allow_self_transfer | 布尔；未说明时false并披露假设；明确true原样保留并提示当前不支持 |

服务端不填补出发/返程机场、日期、天数或预算。目录外地名应出现在 `unresolved_mentions`；若模型仍生成未知 ID，整份草稿以 `unsupported_location` 拒绝，不能悄悄删除地点后搜索。日期超出模拟数据覆盖时原样保留并提示，不能迁移用户年份以获得有解结果。

偏好的定性到数值映射只是可编辑的演示假设：强偏好80、普通偏好50、未提0；明确权重原样提取。LLM 必须披露这一量化。字段范围校验不证明它正确理解了用户，所以自然语言解释、假设和所有字段仍需人工确认。描述性目的地元数据不进入评分。

## 服务端配置与失败行为

服务端读取 `DEEPSEEK_API_KEY`；`DEEPSEEK_MODEL` 默认为 `deepseek-flash`，当前允许 `deepseek-flash` 和 `deepseek-v4-pro`。SDK 连接固定地址 `https://api.deepseek.com`，调用 Responses API 的严格 `json_schema`，关闭思考模式、temperature=0、输出上限1800 tokens、SDK 请求超时25秒、自动重试0次、`store=False`。25秒是传输超时配置，不是所有网络环境下的硬性端到端 SLA。支持注入假客户端，便于离线测试。提供的客户端由调用方管理；模块自行创建的客户端调用后关闭。

输入限制1–2000字符；响应限制18000字符。JSON 重复键、额外字段、未知/重复ID、必去/偏好交集、错误类型、非法日期和相互颠倒的日期/天数范围均拒绝。布尔值不能当整数。完整性状态异常或输出被截断时拒绝。对外只返回 `IntentInputError.code` 和安全中文 `message`，不透传 API 原始响应、HTTP 内容、密钥或异常链。

密钥不进入模型输入、草稿或浏览器；此模块不持久化输入、响应或日志。部署层应只从服务端密钥配置传入；不得让浏览器提交 API key。对外展示模型文本时应使用安全文本渲染，不允许模型输出 HTML。

接口依据：[DeepSeek Responses API](https://api-docs.deepseek.com/api/create-response/)、[DeepSeek Responses 使用说明](https://api-docs.deepseek.com/guides/responses_api/)。SDK 依赖复用项目已锁定版本，不添加代理、工具调用或航班接口。

## 验证与边界

离线验证命令：`py -3.12 -m unittest tests.test_intent_m9 -v`。测试覆盖空值保留、默认假设、地名白名单、类型/范围、交叉冲突、目录信息隔离、密钥不回传、服务异常、重复JSON键和截断响应。

这些测试验证本地边界和请求配置；模型语义是否正确、是否漏报不确定性仍需后续独立评估。自然语言输出并不保证重复运行一致；确认后的同一 `TripRequest` 才交给原确定性优化器。M8 原始语料、prompt/schema、历史评估结果保持冻结；本契约设计参考既有可靠性风险，不将旧语料上的表现当作独立验收。此次组件测试未读取或消耗 holdout，也未发起付费模型调用。
