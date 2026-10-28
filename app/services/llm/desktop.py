"""§19：手机的电脑中转配置与客户端。配置生效后绝不回退直付接口。"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import tempfile
from urllib.parse import urlsplit

import httpx

from ... import config
from .client import Capabilities, LLMError, OpenAICompatClient

MODEL = "deepseek-v4.1-flash"


def settings_path():
    return config.DATA_DIR / "desktop-bridge.json"


def load_settings():
    path = settings_path()
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return validate_settings(value.get("url"), value.get("token"))
    except (OSError, ValueError, AttributeError):
        # 损坏也阻止工厂偷偷走直付接口。
        raise LLMError("电脑连接配置损坏，请重新设置。", code="bridge_config", retryable=False)


def validate_settings(url, token):
    if not isinstance(url, str) or not isinstance(token, str):
        raise ValueError("请填写电脑地址和配对码")
    try:
        parsed = urlsplit(url.strip())
        ip = ipaddress.IPv4Address(parsed.hostname or "")
        private = ip.is_loopback or any(ip in ipaddress.IPv4Network(net) for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
        port = parsed.port
        if parsed.scheme != "http" or not private or not port or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError()
    except ValueError:
        raise ValueError("电脑地址应为 http://局域网IPv4:端口，例如 http://192.168.1.2:8891")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token.strip()):
        raise ValueError("配对码格式不正确，请从电脑中转复制完整配对码")
    return {"url": f"http://{ip}:{port}", "token": token.strip()}


def bridge_request(settings, path, *, payload=None):
    try:
        # §19.1：连接旧IP时尽快报连接问题，保留原有的推理等待上限。
        read_timeout = 90 if payload is not None else (20 if path.startswith(("/flatlay/jobs/", "/reference/jobs/")) else 6)
        timeout = httpx.Timeout(read_timeout, connect=4, pool=4, write=20 if payload is not None else 6)
        with httpx.Client(timeout=timeout, trust_env=False, follow_redirects=False) as client:
            response = client.request("POST" if payload is not None else "GET", settings["url"] + path,
                headers={"Authorization": "Bearer " + settings["token"]}, json=payload)
    except httpx.ConnectTimeout:
        raise LLMError("连接电脑超时，请检查电脑地址是否已变化，以及中转是否启动、手机与电脑是否同网。", code="bridge_offline")
    except httpx.TimeoutException:
        raise LLMError("电脑识别等待超时，请稍后重试。", code="bridge_timeout")
    except httpx.HTTPError:
        raise LLMError("连不上电脑，请确认电脑中转已启动，且手机与电脑在同一网络。", code="bridge_offline")
    errors = {
        401: ("bridge_login", "电脑个人CLI登录已失效，请在电脑重新登录。", False),
        403: ("bridge_pairing", "配对码不匹配，请检查电脑识别设置。", False),
        402: ("insufficient_balance", "个人账号积分或额度不足，请补充后重新检查服务。", False),
        429: ("bridge_busy", "电脑正在处理其他识别，请稍后重试。", True),
        504: ("bridge_timeout", "电脑识别等待超时，请稍后重试。", True),
    }
    if response.status_code != 200:
        if path.startswith('/reference/jobs') and response.status_code == 503:
            raise LLMError('电脑中转尚未启用实物搜图，请更新电脑中转。', code='reference_not_configured', retryable=False)
        if path.startswith('/reference/jobs/') and response.status_code == 404 and payload is None:
            return {'model': MODEL, 'job_id': path.rsplit('/', 1)[-1], 'status': 'missing'}
        code, text, retryable = errors.get(response.status_code, ("bridge_unavailable", "电脑中转本次调用失败，请检查个人CLI状态后重试。", True))
        raise LLMError(text, code=code, retryable=retryable)
    try:
        body = response.json()
        if not isinstance(body, dict) or body.get("model") != MODEL:
            raise ValueError()
        return body
    except (ValueError, TypeError):
        raise LLMError("电脑中转响应格式不正确，请检查连接地址。", code="bridge_unavailable")


def save_settings(url, token):
    settings = validate_settings(url, token)
    check = bridge_request(settings, "/health")
    if check.get("protocol") != 1 or check.get("status") != "ready":
        raise ValueError("电脑中转版本不兼容")
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix="bridge-", suffix=".json", delete=False) as file:
            tmp = file.name
            json.dump(settings, file)
        os.replace(tmp, path)
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)
    return {"configured": True, "url": settings["url"], "model": MODEL}


def bridge_status():
    try:
        settings = load_settings()
    except LLMError:
        return {"configured": True, "url": "", "model": MODEL, "invalid": True}
    return {"configured": bool(settings), "url": settings["url"] if settings else "", "model": MODEL}


class DesktopClient:
    model = MODEL

    def __init__(self, settings):
        self.settings = settings

    def capabilities(self):
        # CLI 用提示词约束，不伪装成服务端支持强制 schema，也不发付费探测。
        return Capabilities(model=MODEL)

    def _chat(self, messages, schema, json_output):
        body = bridge_request(self.settings, "/chat", payload={"model": MODEL, "messages": messages, "schema": schema, "json_output": json_output})
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            raise LLMError("电脑识别没有返回有效内容。", code="bridge_unavailable")
        return text

    def chat_text(self, messages, *, timeout=60.0, temperature=0.2):
        return self._chat(messages, None, False)

    def chat_json(self, messages, *, schema=None, timeout=60.0, temperature=0.0):
        return OpenAICompatClient._parse_json_text(self._chat(messages, schema, True))
