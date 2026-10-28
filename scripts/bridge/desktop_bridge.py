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
import subprocess
import tempfile
import threading
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.bridge.flatlay_bridge import FlatlayJobs, IMAGE_MODEL
from scripts.bridge.reference_bridge import ReferenceJobs

from fastapi import FastAPI, HTTPException, Request
from starlette.concurrency import run_in_threadpool

MODEL = "deepseek-v4.1-flash"
MAX_BODY = 60 * 1024 * 1024
MAX_IMAGE = 20 * 1024 * 1024
UPLOAD_TIMEOUT = 10
BUILD = hashlib.sha256(b''.join(Path(__file__).with_name(name).read_bytes() for name in ('desktop_bridge.py', 'flatlay_bridge.py', 'flatlay_guard.cjs', 'reference_bridge.py', 'reference_net.py', 'reference_guard.cjs'))).hexdigest()


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
    for name in list(env):
        if name.startswith(("CODEBUDDY_", "ANTHROPIC_", "LLM_", "OPENAI_")) or name in (
            "NODE_OPTIONS", "ELECTRON_RUN_AS_NODE", "VISION_MODEL",
        ):
            env.pop(name, None)
    env.update(CODEBUDDY_INTERNET_ENVIRONMENT="internal", CODEBUDDY_DISABLE_COMPILE_CACHE="1")
    return env


def execute_cli(executable: Path, system: str, content: list, *, json_output: bool, cli_entry: Path | None = None):
    if json_output:
        system += "\n只返回一个JSON对象，不能包含工具调用或解释。"
    args = [str(executable), *([str(cli_entry)] if cli_entry else []), "-p", "--input-format", "stream-json", "--output-format", "stream-json",
            "--verbose", "--model", MODEL, "--tools", "", "--max-turns", "1",
            "--no-session-persistence", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--setting-sources", "", "--system-prompt", system or "只回答用户本次请求，不使用工具。"]
    message = {"type": "user", "message": {"role": "user", "content": content}}
    if os.name == "nt" and len(subprocess.list2cmdline(args).encode("utf-16-le")) // 2 >= 30000:
        raise HTTPException(413, "系统提示过长，请缩短后重试")
    started=time.monotonic();request_id=secrets.token_hex(8);timed_out=False
    try:
        # 每次使用空目录；无项目指令、无用户图片文件、无会话文件。
        with tempfile.TemporaryDirectory(prefix="wardrobe_cli_") as cwd:
            result = subprocess.run(args, cwd=cwd, env=cli_environment(),
                input=(json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"),
                capture_output=True, timeout=75,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
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
        complete=(init.get("apiKeySource")=="copilot.tencent.com" and init.get("model")==MODEL
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
    if init.get("apiKeySource") != "copilot.tencent.com" or init.get("model") != MODEL:
        raise HTTPException(502, "个人账号或模型来源未通过校验")
    text = final.get("result")
    if not isinstance(text, str) or not text.strip() or len(text) > 100000:
        raise HTTPException(502, "模型未返回有效内容")
    if not timed_out:print(json.dumps({"event":"recognition_finished","request_id":request_id,"elapsed":round(time.monotonic()-started,2),"phase":"completed"}),flush=True)
    return text


def create_app(executable: Path, token: str, runner=execute_cli, *, cli_entry=None, flatlay_dir=None, image_runner=None, reference_dir=None, reference_runner=None):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    busy = threading.BoundedSemaphore(1)
    admission = threading.Lock()
    draining = False
    flatlay = None
    if cli_entry and flatlay_dir:
        options = {"runner": image_runner} if image_runner else {}
        flatlay = FlatlayJobs(flatlay_dir, executable, cli_entry, busy, cli_environment, **options)
    references = None
    if cli_entry and reference_dir:
        options = {"runner": reference_runner} if reference_runner else {}
        def reference_chat(system, content):
            return runner(executable, system, content, json_output=True, cli_entry=cli_entry)
        references = ReferenceJobs(reference_dir, executable, cli_entry, busy, cli_environment, reference_chat, **options)

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        from fastapi.responses import JSONResponse
        # 禁止浏览器跨站调用；手机后端发请求，不需要 CORS。
        if request.headers.get("origin") or not hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + token):
            return JSONResponse({"detail": "配对校验失败"}, status_code=403)
        if request.method == "POST" and request.url.path not in {"/admin/drain", "/admin/resume"}:
            if not admission.acquire(blocking=False):
                return JSONResponse({"detail": "电脑正在处理其他请求，请稍后再试"}, status_code=429)
            try:
                if draining:
                    return JSONResponse({"detail": "电脑中转维护中，请稍后重试"}, status_code=503)
                response = await call_next(request)
            finally:
                admission.release()
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/admin/drain")
    async def drain():
        nonlocal draining
        if not admission.acquire(blocking=False):
            raise HTTPException(409, "电脑仍在接收或处理请求")
        try:
            if not draining:
                if not busy.acquire(blocking=False):
                    raise HTTPException(409, "电脑仍有任务在运行")
                draining = True
            return {"draining": True}
        finally:
            admission.release()

    @app.post("/admin/resume")
    async def resume():
        nonlocal draining
        if not admission.acquire(blocking=False):
            raise HTTPException(409, "电脑仍在处理请求")
        try:
            if draining:
                draining = False
                busy.release()
            return {"draining": False}
        finally:
            admission.release()

    @app.get("/health")
    def health():
        if not executable.is_file():
            raise HTTPException(503, "找不到个人CLI")
        return {"status": "maintenance" if draining else "ready", "model": MODEL, "protocol": 1, "build": BUILD,
                "image_edit": bool(flatlay), "image_model": IMAGE_MODEL if flatlay else None, "reference_search": bool(references)}

    @app.post("/reference/jobs")
    async def create_reference(request: Request):
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
        if not busy.acquire(blocking=False):
            raise HTTPException(429, "电脑正在识别，请稍后再试")
        try:
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
            system, content = convert_messages(payload.get("messages"))
            schema = payload.get("schema")
            if schema is not None:
                serialized = json.dumps(schema, ensure_ascii=False)
                if not isinstance(schema, dict) or len(serialized) > 50000:
                    raise HTTPException(400, "结构要求不正确")
                system += "\n输出JSON须遵循以下字段约定：\n" + serialized
            options = {"cli_entry": cli_entry} if cli_entry else {}
            text = await run_in_threadpool(runner, executable, system, content, json_output=payload.get("json_output") is True, **options)
            return {"model": MODEL, "text": text}
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
