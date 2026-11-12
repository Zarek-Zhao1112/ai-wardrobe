"""§19：个人 CLI 中转；只接收内嵌图片，不开放工具或任意命令。"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hmac
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import tempfile
import threading
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.bridge_discovery import DISCOVERY_PATH, discovery_proof
from scripts.bridge.flatlay_bridge import FlatlayJobs, IMAGE_MODEL
from scripts.bridge.reference_bridge import ReferenceJobs
from scripts.bridge.style_image_bridge import StyleJobs, IMAGE_MODEL as STYLE_IMAGE_MODEL
from scripts.bridge.tty_runner import account_source_ok, run_cli

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

MODEL = "deepseek-v4.1-flash"
#: 手机可以点名的两种上游。缺省仍是 account —— 老 APK 不带这个字段。
ACCOUNT_ENGINE = "account"
API_ENGINE = "api"
MAX_BODY = 60 * 1024 * 1024
MAX_IMAGE = 20 * 1024 * 1024
UPLOAD_TIMEOUT = 10
BUILD = hashlib.sha256(b''.join(Path(__file__).with_name(name).read_bytes() for name in ('desktop_bridge.py', 'flatlay_bridge.py', 'flatlay_guard.cjs', 'reference_bridge.py', 'reference_net.py', 'reference_guard.cjs', 'style_image_bridge.py', 'style_image_guard.cjs'))
                       + (Path(__file__).resolve().parents[2] / 'app/bridge_discovery.py').read_bytes()).hexdigest()


def convert_messages(messages):
    """OpenAI 消息转官方 CLI 的单次输入，图片只准 base64，不取远程 URL。"""
    if not isinstance(messages, list) or not 1 <= len(messages) <= 12:
        raise HTTPException(400, "消息格式不正确")
    system, content, images, chars = [], [], 0, 0
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in ("system", "user"):
            raise HTTPException(400, "只接受 system/user 消息")
        parts = message.get("content")
        if isinstance(parts, str):
            parts = [{"type": "text", "text": parts}]
        if not isinstance(parts, list):
            raise HTTPException(400, "消息内容不正确")
        for part in parts:
            if not isinstance(part, dict):
                raise HTTPException(400, "消息内容不正确")
            if part.get("type") == "text" and isinstance(part.get("text"), str):
                chars += len(part["text"])
                if chars > 100000:
                    raise HTTPException(413, "文字过长")
                if message["role"] == "system":
                    system.append(part["text"])
                else:
                    content.append({"type": "text", "text": part["text"]})
            elif part.get("type") == "image_url" and message["role"] == "user":
                value = part.get("image_url")
                url = value.get("url") if isinstance(value, dict) else None
                match = re.fullmatch(r"data:(image/(?:png|jpeg|webp|gif));base64,([A-Za-z0-9+/=]+)", url or "")
                if not match or len(match[2]) > (MAX_IMAGE + 2) // 3 * 4:
                    raise HTTPException(400, "图片须为内嵌的 JPG/PNG/WebP/GIF")
                try:
                    data = base64.b64decode(match[2], validate=True)
                except ValueError:
                    raise HTTPException(400, "图片编码不正确")
                images += 1
                if images > 2 or not data or len(data) > MAX_IMAGE:
                    raise HTTPException(413, "一次最多两张图片，每张不超过20MB")
                content.append({"type": "image", "source": {"type": "base64", "media_type": match[1], "data": match[2]}})
            else:
                raise HTTPException(400, "不支持此消息内容")
    if not content:
        raise HTTPException(400, "缺少输入内容")
    return "\n".join(system), content


def cli_environment():
    env = os.environ.copy()
    # 认证交给官方个人登录；不继承项目的直付密钥、代理入口或 Node 注入。
    # ★ ACC_PRODUCT_CONFIG* 必须剥掉（§19.4 最终结论，2026-11-04 实测）：
    #   WorkBuddy 桌面端会用 ACC_PRODUCT_CONFIG_PATH 把自己的产品配置溢出到
    #   %Temp%\workbuddy-product-spill-*\acc-product-config-v3.json，其中
    #   authentication.id=workbuddy-desktop。本进程是桌面端的子进程，天然继承它；
    #   CLI 读到这份配置后只认 workbuddy-desktop 名下的 token，而那份凭据不落盘、
    #   或早已过期，于是每次都报「Authentication required」。
    #   剥掉之后 CLI 回落到包内 product.json（CodeBuddy / cli-external-link），
    #   走 /login 写盘的凭据（%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\
    #   Tencent-Cloud.coding-copilot.info），apiKeySource=copilot.tencent.com。
    # 注意：http(s)_proxy 保留，CLI 自身取配置时会用到。
    for name in list(env):
        if name.startswith(("CODEBUDDY_", "ANTHROPIC_", "LLM_", "OPENAI_", "ACC_PRODUCT_CONFIG")) or name in (
            "NODE_OPTIONS", "ELECTRON_RUN_AS_NODE", "VISION_MODEL",
        ):
            env.pop(name, None)
    env.update(CODEBUDDY_INTERNET_ENVIRONMENT="internal", CODEBUDDY_DISABLE_COMPILE_CACHE="1")
    return env


def free_port():
    """每次调用要一个空闲端口。
    §19.2：CLI 会持久化上一次用过的「稳定端口」，WorkBuddy 桌面端自己预热了一个
    CLI 实例常驻占用它，于是新起的 CLI 直接 `EADDRINUSE` 崩在登录检查之前
    （报错长得像网络问题，实际是端口撞车）。显式传 --port 绕开。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def execute_cli(executable: Path, system: str, content: list, *, json_output: bool, cli_entry: Path | None = None):
    if json_output:
        system += "\n只返回一个JSON对象，不能包含工具调用或解释。"
    args = [str(executable), *([str(cli_entry)] if cli_entry else []), "-p", "--port", str(free_port()), "--input-format", "stream-json", "--output-format", "stream-json",
            "--verbose", "--model", MODEL, "--tools", "", "--max-turns", "1",
            "--no-session-persistence", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--setting-sources", "", "--system-prompt", system or "只回答用户本次请求，不使用工具。"]
    message = {"type": "user", "message": {"role": "user", "content": content}}
    if os.name == "nt" and len(subprocess.list2cmdline(args).encode("utf-16-le")) // 2 >= 30000:
        raise HTTPException(413, "系统提示过长，请缩短后重试")
    started=time.monotonic();request_id=secrets.token_hex(8);timed_out=False
    try:
        # 每次使用空目录；无项目指令、无用户图片文件、无会话文件。
        # §19.4：CLI 必须在真 TTY 里跑才能拿到账号凭据，故走 tty_runner（mintty 隐藏终端）。
        with tempfile.TemporaryDirectory(prefix="wardrobe_cli_") as cwd:
            result = run_cli(args, cwd=cwd, env=cli_environment(),
                stdin_bytes=(json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"),
                timeout=75)
    except subprocess.TimeoutExpired as error:
        # §29.1：官方CLI可能已输出结果，仍在等待流/退出收尾；绝不重发。
        timed_out=True
        result=SimpleNamespace(returncode=0,stdout=error.stdout or b"",stderr=b"")
    except OSError:
        raise HTTPException(503, "个人CLI无法启动")
    events = []
    for line in result.stdout.decode("utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
            if isinstance(event, dict):
                events.append(event)
        except ValueError:
            pass
    final = next((event for event in reversed(events) if event.get("type") == "result"), {})
    init = next((event for event in events if event.get("type") == "system" and event.get("subtype") == "init"), {})
    if timed_out:
        value=final.get("result")
        complete=(account_source_ok(init.get("apiKeySource")) and init.get("model")==MODEL
                  and final.get("subtype")=="success" and final.get("is_error") is False
                  and not final.get("cancelled") and not final.get("is_cancelled")
                  and final.get("terminal_reason") not in ("aborted_streaming","aborted_tools")
                  and isinstance(value,str) and bool(value.strip()) and len(value)<=100000)
        phase="result_received_exit_pending" if complete else "before_init" if not init else "incomplete_result" if final else "awaiting_result"
        print(json.dumps({"event":"recognition_timeout","request_id":request_id,"elapsed":round(time.monotonic()-started,2),"phase":phase,"result_recovered":complete}),flush=True)
        if not complete:raise HTTPException(504,"电脑识别超时，尚未收到完整结果")
    if result.returncode or final.get("is_error") or final.get("cancelled") or final.get("is_cancelled") or final.get("terminal_reason") in ("aborted_streaming","aborted_tools") or final.get("subtype") != "success":
        # 原始错误可能夹带凭证或图片，只做分类，不返回、不记录。
        diagnostic = (str(final) + result.stderr.decode("utf-8", errors="replace") + result.stdout.decode("utf-8", errors="replace")).lower()
        if any(word in diagnostic for word in ("authentication", "login", "unauthorized", "需要登录", "登录过期")):
            raise HTTPException(401, "请在电脑的个人CLI重新登录")
        if any(word in diagnostic for word in ("insufficient", "balance", "credit", "积分不足", "额度不足")):
            raise HTTPException(402, "个人账号积分或额度不足")
        raise HTTPException(502, "个人CLI本次调用失败")
    if not account_source_ok(init.get("apiKeySource")) or init.get("model") != MODEL:
        raise HTTPException(502, "个人账号或模型来源未通过校验")
    text = final.get("result")
    if not isinstance(text, str) or not text.strip() or len(text) > 100000:
        raise HTTPException(502, "模型未返回有效内容")
    if not timed_out:print(json.dumps({"event":"recognition_finished","request_id":request_id,"elapsed":round(time.monotonic()-started,2),"phase":"completed"}),flush=True)
    return text


def _dotenv():
    """读电脑根目录 .env 的键值。读不到就返回空 —— 这个函数不该让中转起不来。"""
    path = Path(__file__).resolve().parents[2] / ".env"
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return {}
    values = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, sep, value = line.partition("=")
        if not sep or not key.strip():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def api_profile():
    """电脑侧直付模型的配置。

    优先 `.local/llm-profiles.json` 的 openai 段：那份**不随 .env 的模式切换而变**，
    手机点了「API 直连」就始终拿到 OpenAI，不会因为电脑切去账号额度而变成别的。
    没有这份文件时才回退 .env。
    """
    source = {}
    path = Path(__file__).resolve().parents[2] / ".local" / "llm-profiles.json"
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict) and isinstance(loaded.get("openai"), dict):
            source = loaded["openai"]
    except (OSError, ValueError):
        source = {}
    if not str(source.get("LLM_API_KEY") or "").strip():
        source = _dotenv()
    base = str(source.get("LLM_BASE_URL") or "").strip()
    key = str(source.get("LLM_API_KEY") or "").strip()
    model = str(source.get("LLM_MODEL") or "").strip()
    if not base or not key or not model:
        return None
    if not base.rstrip("/").endswith("/v1"):
        base = base.rstrip("/") + "/v1"
    return {"base_url": base, "key": key, "model": model, "proxy": str(source.get("LLM_PROXY") or "").strip() or None}


#: 这一类只认 `max_completion_tokens`，且不接受自定义 temperature。
_REASONING_PREFIX = ("gpt-5", "gpt-6", "o1", "o3", "o4")


def call_api(profile, messages, schema, json_output):
    """把手机那条消息原样转给直付模型。手机发的本来就是 OpenAI 格式，不用转。"""
    body = {"model": profile["model"], "messages": messages}
    if profile["model"].lower().startswith(_REASONING_PREFIX):
        body["max_completion_tokens"] = 4096
    else:
        body["max_tokens"] = 4096
        body["temperature"] = 0
    if json_output and isinstance(schema, dict):
        # strict=true 要求每个 object 都带 additionalProperties:false 且 required 列全字段，
        # 而项目这份 TAG_SCHEMA 不是按那个约束写的（只有 type 必填）→ 会被 400。
        # strict=false 仍然按 schema 提示字段，实测返回结构完整。
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "result", "schema": schema, "strict": False}}
    # trust_env=False：电脑上有个只放行特定域名的系统代理，走它会 502，必须显式指定。
    with httpx.Client(timeout=120, trust_env=False, proxy=profile["proxy"]) as client:
        try:
            response = client.post(profile["base_url"].rstrip("/") + "/chat/completions",
                                   headers={"Authorization": "Bearer " + profile["key"]}, json=body)
        except httpx.TimeoutException:
            raise HTTPException(504, "电脑直连模型超时")
        except httpx.HTTPError:
            raise HTTPException(502, "电脑连不上模型服务，请检查电脑代理是否开启")
    if response.status_code == 401:
        raise HTTPException(401, "电脑上的模型密钥无效，请在电脑重新配置")
    if response.status_code == 402:
        raise HTTPException(402, "模型账户额度不足")
    if response.status_code == 429:
        raise HTTPException(429, "模型限流，请稍后重试")
    if response.status_code != 200:
        raise HTTPException(502, "电脑直连模型失败")
    try:
        text = response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise HTTPException(502, "模型返回格式不正确")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(502, "模型没有返回内容")
    return text


def create_app(executable: Path, token: str, runner=execute_cli, *, cli_entry=None, flatlay_dir=None, image_runner=None, reference_dir=None, reference_runner=None, style_runner=None, search_enabled=False):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    busy = threading.BoundedSemaphore(1)
    admission = threading.Lock()
    queue_admission = threading.Lock()
    draining = False
    flatlay = None
    if cli_entry and flatlay_dir:
        options = {"runner": image_runner} if image_runner else {}
        flatlay = FlatlayJobs(flatlay_dir, executable, cli_entry, busy, cli_environment, **options)
    references = None
    styles = None
    if cli_entry and reference_dir:
        options = {"runner": reference_runner} if reference_runner else {}
        def reference_chat(system, content):
            return runner(executable, system, content, json_output=True, cli_entry=cli_entry)
        references = ReferenceJobs(reference_dir, executable, cli_entry, busy, cli_environment, reference_chat, **options)
        style_options = {'runner': style_runner} if style_runner else {}
        styles = StyleJobs(Path(reference_dir).parent/'styles', executable, cli_entry, busy, cli_environment, **style_options)

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        from fastapi.responses import JSONResponse
        # 禁止浏览器跨站调用；手机后端发请求，不需要 CORS。
        discovery = request.method == "GET" and request.url.path == DISCOVERY_PATH
        if request.headers.get("origin") or (
            not discovery and not hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + token)
        ):
            return JSONResponse({"detail": "配对校验失败"}, status_code=403)
        if request.method == "POST" and request.url.path not in {"/admin/drain", "/admin/resume"}:
            gate = queue_admission if request.url.path == '/style/jobs' else admission
            if not gate.acquire(blocking=False):
                return JSONResponse({"detail": "电脑正在处理其他请求，请稍后再试"}, status_code=429)
            try:
                if draining:
                    return JSONResponse({"detail": "电脑中转维护中，请稍后重试"}, status_code=503)
                response = await call_next(request)
            finally:
                gate.release()
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/admin/drain")
    async def drain():
        nonlocal draining
        if not admission.acquire(blocking=False):
            raise HTTPException(409, "电脑仍在接收或处理请求")
        if not queue_admission.acquire(blocking=False):
            admission.release()
            raise HTTPException(409, '电脑仍在登记款式生成任务')
        try:
            if not draining:
                if not busy.acquire(blocking=False):
                    raise HTTPException(409, "电脑仍有任务在运行")
                draining = True
            return {"draining": True}
        finally:
            queue_admission.release()
            admission.release()

    @app.post("/admin/resume")
    async def resume():
        nonlocal draining
        if not admission.acquire(blocking=False):
            raise HTTPException(409, "电脑仍在处理请求")
        if not queue_admission.acquire(blocking=False):
            admission.release()
            raise HTTPException(409, '电脑仍在登记款式生成任务')
        try:
            if draining:
                draining = False
                busy.release()
            return {"draining": False}
        finally:
            queue_admission.release()
            admission.release()

    @app.get(DISCOVERY_PATH)
    def discover(request: Request, nonce: str = Query(pattern=r"^[0-9a-f]{32}$")):
        # 签名绑定服务实际收到连接的 IP 与端口，不信任 Host 请求头。
        endpoint = request.scope.get("server")
        if not endpoint or not endpoint[0] or not endpoint[1]:
            raise HTTPException(503, "无法验证中转地址")
        host, port = endpoint
        state = "ready" if executable.is_file() and not draining else "unavailable"
        return {"state": state, "proof": discovery_proof(token, nonce, host, port, state)}

    @app.get("/health")
    def health():
        if not executable.is_file():
            raise HTTPException(503, "找不到个人CLI")
        profile = api_profile()
        return {"status": "maintenance" if draining else "ready", "model": MODEL, "protocol": 1, "build": BUILD,
                "engines": {ACCOUNT_ENGINE: MODEL, API_ENGINE: profile["model"] if profile else None},
                "image_edit": bool(flatlay), "image_model": IMAGE_MODEL if flatlay else None, "reference_search": bool(references) and search_enabled,
                "style_generation": bool(styles), "style_image_model": STYLE_IMAGE_MODEL if styles else None}

    @app.post("/reference/jobs")
    async def create_reference(request: Request):
        if not search_enabled: raise HTTPException(409, '实物搜图已暂停，请重新分析生成AI款式示意。')
        if not references: raise HTTPException(503, "电脑中转尚未启用实物搜图")
        if request.headers.get("content-type", "").split(";")[0] != "application/json": raise HTTPException(415, "需要JSON请求")
        body = bytearray()
        try:
            async with asyncio.timeout(UPLOAD_TIMEOUT):
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 5000: raise HTTPException(413, "搜图方向内容过长")
        except TimeoutError: raise HTTPException(504, "搜图请求上传超时")
        try: payload = json.loads(body)
        except (ValueError, UnicodeError): raise HTTPException(400, "搜图任务格式不正确")
        return {"model": MODEL, **await run_in_threadpool(references.start, payload)}

    @app.get("/reference/jobs/{job_id}")
    def get_reference(job_id: str):
        if not references: raise HTTPException(503, "电脑中转尚未启用实物搜图")
        return {"model": MODEL, **references.status(job_id)}

    @app.post('/style/jobs')
    async def create_style(request: Request):
        if not styles: raise HTTPException(503,'电脑中转尚未启用款式生图')
        if request.headers.get('content-type','').split(';')[0]!='application/json':raise HTTPException(415,'需要JSON请求')
        body=bytearray()
        try:
            async with asyncio.timeout(UPLOAD_TIMEOUT):
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body)>5000:raise HTTPException(413,'款式方向内容过长')
        except TimeoutError:raise HTTPException(504,'款式请求上传超时')
        try:payload=json.loads(body)
        except (ValueError,UnicodeError):raise HTTPException(400,'款式任务格式不正确')
        return {'model':MODEL,**await run_in_threadpool(styles.start,payload)}

    @app.get('/style/jobs/{job_id}')
    def get_style(job_id:str):
        if not styles:raise HTTPException(503,'电脑中转尚未启用款式生图')
        return {'model':MODEL,**styles.status(job_id)}

    @app.post("/flatlay/jobs")
    async def create_flatlay(request: Request):
        if not flatlay: raise HTTPException(503, "电脑中转尚未启用平铺图功能")
        if request.headers.get("content-type", "").split(";")[0] != "application/json": raise HTTPException(415, "需要JSON请求")
        body = bytearray()
        try:
            async with asyncio.timeout(UPLOAD_TIMEOUT):
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 15 * 1024 * 1024: raise HTTPException(413, "图片请求过大")
        except TimeoutError: raise HTTPException(504, "图片上传超时")
        try: payload = json.loads(body)
        except (ValueError, UnicodeError): raise HTTPException(400, "任务JSON无效")
        return await run_in_threadpool(flatlay.start, payload)

    @app.get("/flatlay/jobs/{job_id}")
    def get_flatlay(job_id: str):
        if not flatlay: raise HTTPException(503, "电脑中转尚未启用平铺图功能")
        return flatlay.status(job_id)

    @app.post("/chat")
    async def chat(request: Request):
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise HTTPException(415, "需要JSON请求")
        body = bytearray()
        try:
            async with asyncio.timeout(UPLOAD_TIMEOUT):
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > MAX_BODY:
                        raise HTTPException(413, "请求过大")
        except TimeoutError:
            raise HTTPException(504, "图片上传超时，请检查网络后重试")
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError):
            raise HTTPException(400, "JSON格式不正确")
        if not isinstance(payload, dict) or payload.get("model") != MODEL:
            raise HTTPException(400, "模型固定为DeepSeek V4.1 Flash")
        # 两条上游共用这套入参校验：图片只收内嵌 base64，文字和图片张数都设上限。
        system, content = convert_messages(payload.get("messages"))
        schema = payload.get("schema")
        serialized = None
        if schema is not None:
            serialized = json.dumps(schema, ensure_ascii=False)
            if not isinstance(schema, dict) or len(serialized) > 50000:
                raise HTTPException(400, "结构要求不正确")
        if payload.get("engine") == API_ENGINE:
            # 直付这条路**不占** CLI 的单通道锁 —— 它跟 CLI 是两条独立上游，
            # 手机在直付上跑打标时，电脑自己的 CLI 请求不该被一起堵住。
            profile = api_profile()
            if profile is None:
                raise HTTPException(503, "电脑上没有可用的模型密钥，请先在电脑配置")
            text = await run_in_threadpool(call_api, profile, payload.get("messages"), schema, payload.get("json_output") is True)
            return {"model": profile["model"], "text": text, "engine": API_ENGINE}
        if serialized is not None:
            system += "\n输出JSON须遵循以下字段约定：\n" + serialized
        if not busy.acquire(blocking=False):
            raise HTTPException(429, "电脑正在识别，请稍后再试")
        try:
            options = {"cli_entry": cli_entry} if cli_entry else {}
            text = await run_in_threadpool(runner, executable, system, content, json_output=payload.get("json_output") is True, **options)
            return {"model": MODEL, "text": text, "engine": ACCOUNT_ENGINE}
        finally:
            busy.release()

    return app


def main():
    parser = argparse.ArgumentParser(description="衣橱个人CLI电脑中转")
    parser.add_argument("--cli", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True, help="仅本机保存配对码的运行目录")
    parser.add_argument("--cli-entry", type=Path, help="官方npm个人CLI的bin/codebuddy入口，--cli为node.exe")
    parser.add_argument("--port", type=int, default=8891)
    args = parser.parse_args()
    executable = args.cli.resolve(strict=True)
    cli_entry = args.cli_entry.resolve(strict=True) if args.cli_entry else None
    args.state.mkdir(parents=True, exist_ok=True)
    path = args.state / "pairing.json"
    if path.exists():
        token = json.loads(path.read_text(encoding="utf-8"))["token"]
    else:
        token = secrets.token_urlsafe(32)
        path.write_text(json.dumps({"token": token}), encoding="utf-8")
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
        raise SystemExit("配对配置损坏")
    import uvicorn
    print(f"电脑中转端口：{args.port}；配对码保存在运行目录 pairing.json。", flush=True)
    uvicorn.run(create_app(executable, token, cli_entry=cli_entry, flatlay_dir=args.state / "flatlay" if cli_entry else None, reference_dir=args.state / "references" if cli_entry else None), host="0.0.0.0", port=args.port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
