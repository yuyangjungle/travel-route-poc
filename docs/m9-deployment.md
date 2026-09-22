# M9 运行与部署

更新：2026-09-22。公开演示：[随心航线](https://travel-route-poc.vercel.app)。这是公开产品原型，报价全部模拟。

## 干净安装

需要 Python 3.12。Windows：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn server:app --host 127.0.0.1 --port 8000
# 另一个终端可运行完整本地 Streamlit 演示
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Linux/macOS 使用 `python3.12` 和 `.venv/bin/python`。打开 http://127.0.0.1:8000。没有模型密钥时，示例和手动模式照常工作。

`requirements.txt` 固定直接依赖，含 Streamlit 和测试库。Vercel 从 `pyproject.toml` 安装较小的 Web 运行依赖，不打包 Streamlit；当前 Python Function 约 22 MB。间接依赖未全部锁死，所以兼容性以 CI 和干净环境验证为准，不宣称安装产物逐字节相同。

```powershell
py -3.12 scripts/verify_clean_environment.py
py -3.12 -m unittest discover -s tests -v
py -3.12 -m benchmark_tools.run_m2
py -3.12 -m data.m5.generate --check
node --check web/assets/app.js
# 服务启动后，已安装 agent-browser 时可运行浏览器状态回归（不调用模型）
py -3.12 scripts/verify_m9_browser.py --url http://127.0.0.1:8000
```

干净环境验证器在临时虚拟环境内安装并运行全部测试。`.gitattributes` 禁止换行转换，保持冻结语料、核心代码和数据的字节哈希。

## 模型与密钥

- `DEEPSEEK_API_KEY`：仅服务端持有。
- `DEEPSEEK_MODEL`：`deepseek-flash`；可选 `deepseek-v4-pro` 需单独评估。
- `AI_ENABLED=false`：关闭 Web AI 入口；示例、确认和优化继续可用。
- `.env.example` 只作配置说明，Uvicorn 不自动加载它。请用终端环境或托管平台 Secret 配置；不能把密钥放进 HTML、JS、URL 或 Git。

Vercel Production/Preview 已配置敏感环境变量。代码不记录自然语言正文、模型原始响应或 Authorization header；错误消息不会透传供应商异常。浏览器仅访问同源 `/api/intent`，从不接收 API key。浏览器可查看已确认条件和路线，也可主动下载本轮结果；应用不建立用户数据库。

## Vercel 发布

项目采用单个 FastAPI 入口 `server:app`，同源提供静态页面与 API。`vercel.json` 指定 FastAPI、香港区域和 60 秒函数上限。

```powershell
vercel link
# 交互输入敏感值，不将密钥写在命令参数里
vercel env add DEEPSEEK_API_KEY production --sensitive
vercel env add DEEPSEEK_MODEL production --sensitive
vercel deploy --yes
# 预览部署受保护时，用 vercel curl 验证，不关闭保护
vercel inspect <deployment-url>
vercel deploy --prod --yes
```

首次部署可能直接成为 Production。Preview 需单独配置同名变量。GitHub CI 执行测试；当前发布由 Vercel CLI 完成，**没有配置自动 Git 推送部署**。后续更新代码后要显式重新部署，不能把 CI 绿灯视为线上代码已更新。

发布后匿名检查首页、`/api/health`、`/api/bootstrap`、AI 草稿确认、固定场景优化、无解、移动端和浏览器控制台。使用合成描述验证；不要上传真实旅客个人资料。

## 运维边界

无账户、数据库、监控基础设施或全球限流。AI 接口仅有每个进程 12 次/分钟的演示保护，跨实例请求不能共享额度；供应商费用仍需由项目所有者关注。需要暂停 AI 时设置 `AI_ENABLED=false` 并重新部署；需要回退时将已验证的上一部署重新提升为 Production。当前版本不作为无人值守商业服务。

实际验证结果见 [交付报告](m9-results.md)，产品讲解见 [演示流程](m9-demo-flow.md)。
