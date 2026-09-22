# M7 本地运行与部署准备

状态：Streamlit 单体已准备；没有生产基础设施或公开环境承诺。  
最后更新：2026-09-17

## 本地运行

安装固定依赖：

```powershell
py -3.12 -m pip install -r requirements.txt
```

只使用手动模式时无需任何密钥：

```powershell
py -3.12 -m streamlit run app.py
```

启用可选 AI 输入：

```powershell
$env:OPENAI_API_KEY = "your-project-api-key"
$env:OPENAI_MODEL = "gpt-5-mini"
py -3.12 -m streamlit run app.py
```

`.env.example` 只列变量名；应用不会自动读取 `.env` 文件。不要把真实密钥写入仓库。

## Streamlit Community Cloud

1. 推送项目到私有或受控 GitHub 仓库。
2. 创建 Streamlit app，入口设为 `app.py`，Python 使用 3.12。
3. 在应用 Secrets 中设置：

```toml
OPENAI_API_KEY = "your-project-api-key"
OPENAI_MODEL = "gpt-5-mini"
```

4. 部署后依次检查：发现模式输入、结构化结果确认、多个方案比较、手动模式、无解场景。
5. 不在日志、截图或错误页面打印 Secrets。

没有 Secrets 时应用仍能启动，发现模式会显示配置提示，手动模式继续工作。

## 依赖

`requirements.txt` 固定：

- Python 时区数据；
- Streamlit；
- OpenAI Python SDK；
- 与 SDK 传输层兼容的 aiohttp。

AI 请求使用 Responses API Structured Outputs，`store=False`。当前实现没有流式响应、工具调用、对话状态或服务端数据库。

## 部署验收

```powershell
py -3.12 -m unittest discover -s tests -v
py -3.12 -m benchmark_tools.run
py -3.12 -m benchmark_tools.run_m2
```

另外用真实浏览器检查：

- 页面可以在没有 API key 时正常加载；
- 两种模式可以切换；
- AI 结果必须经过用户确认后才运行优化器；
- 页面持续显示模拟价格和数据边界；
- 浏览器控制台无错误。

## 运维边界

本阶段没有账户、配额管理、审计数据库或多租户隔离。公开部署前应限制参与者范围，并使用单独项目密钥和供应商侧预算限制。部署准备不等于生产就绪。
