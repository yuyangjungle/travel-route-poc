"""Single Python web entrypoint for the public M9 demo on Vercel/local Uvicorn."""
from __future__ import annotations

from collections import deque
import json
import os
from pathlib import Path
import threading
import time

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from benchmark_tools.validation import ValidationError
from travel_product import service
from travel_product.intent import extract_intent, IntentInputError

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="随心航线 · Decision Demo", docs_url=None, redoc_url=None, openapi_url=None)
_ai_calls: deque[float] = deque()
_rate_lock = threading.Lock()


@app.middleware("http")
async def response_boundary(request: Request, call_next):
    if request.method == "POST":
        # Read with a cap even for chunked bodies; don't log natural language or payloads.
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 16384:
                return JSONResponse({"error": "请求过长，请缩短旅行描述。"}, status_code=413)
        request._body = bytes(body)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def failure(message: str, status: int = 422):
    return JSONResponse({"error": message}, status_code=status)


@app.get("/api/health")
def health():
    return {"status": "ok", "version": service.PRODUCT_VERSION, "data_status": "simulated"}


@app.get("/api/bootstrap")
def bootstrap():
    return service.bootstrap()


async def object_body(request):
    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("JSON 包含重复字段，请重新提交。")
            value[key] = item
        return value

    try:
        body = json.loads(await request.body(), object_pairs_hook=unique_object,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError("无效数值。")))
    except (ValueError, UnicodeDecodeError):
        raise ValueError("请求不是有效 JSON。") from None
    if not isinstance(body, dict):
        raise ValueError("请求必须包含完整的旅行条件。")
    return body


@app.post("/api/intent")
async def intent(request: Request):
    try:
        body = await object_body(request)
        if set(body) != {"text"} or not isinstance(body["text"], str) or not 10 <= len(body["text"].strip()) <= 2000:
            return failure("请用 10–2000 个字符描述出发地、时间和旅行偏好。")
        if not service.bootstrap()["ai_available"]:
            return failure("AI 暂时不可用，可以使用示例或手动填写继续规划。", 503)
        now = time.monotonic()
        with _rate_lock:
            while _ai_calls and now - _ai_calls[0] > 60:
                _ai_calls.popleft()
            # Per-instance guard, not a global quota. No identities or IPs stored.
            if len(_ai_calls) >= 12:
                return failure("演示请求较多，请稍后重试，或使用手动填写。", 429)
            _ai_calls.append(now)
        data, descriptions, _ = service.resources()
        return await run_in_threadpool(extract_intent, body["text"], data, descriptions)
    except IntentInputError as exc:
        return failure(str(exc), 502)
    except ValueError:
        return failure("请求无法处理，请检查文字或使用手动填写。")
    except Exception:
        return failure("AI 暂时不可用，请使用手动填写继续。", 503)


@app.post("/api/optimize")
async def optimize(request: Request):
    try:
        body = await object_body(request)
        if set(body) != {"fields", "confirmed"}:
            return failure("请先检查并确认条件。")
        return await run_in_threadpool(service.optimize, body["fields"], confirmed=body["confirmed"])
    except ValidationError as exc:
        return failure(f"旅行条件未通过校验（{exc.path}）。请检查日期、机场、目的地、预算和中转限制。")
    except (ValueError, TypeError, KeyError):
        return failure("旅行条件不完整或不受支持。请检查必填项；自行转机暂不支持。")
    except Exception:
        return failure("本次搜索未能完成，请稍后重试。", 503)


@app.get("/")
def index():
    return FileResponse(ROOT / "web/index.html")


app.mount("/assets", StaticFiles(directory=ROOT / "web/assets"), name="assets")
