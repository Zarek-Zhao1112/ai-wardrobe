"""§19：手机的电脑中转配置与客户端。配置生效后绝不回退直付接口。"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import tempfile
from threading import RLock
import time
from urllib.parse import urlsplit

import httpx

from ... import config
from .client import Capabilities, LLMError, OpenAICompatClient

MODEL = "deepseek-v4.1-flash"
#: 手机能选的两种上游。缺省 account —— 老配置没有这个字段时按它处理。
ACCOUNT = "account"
API = "api"
ENGINES = (ACCOUNT, API)
_settings_lock = RLock()


def settings_path():
    return config.DATA_DIR / "desktop-bridge.json"


def load_settings():
    path = settings_path()
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return validate_settings(
            value.get("url"), value.get("token"),
            value.get("engine", ACCOUNT), value.get("api_model", ""),
        )
    except (OSError, ValueError, AttributeError):
        # 损坏也阻止工厂偷偷走直付接口。
        raise LLMError("电脑连接配置损坏，请重新设置。", code="bridge_config", retryable=False)


def validate_settings(url, token, engine=ACCOUNT, api_model=""):
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
    if engine not in ENGINES:
        raise ValueError("识别引擎不正确")
    return {
        "url": f"http://{ip}:{port}",
        "token": token.strip(),
        "engine": engine,
        "api_model": api_model.strip() if isinstance(api_model, str) else "",
    }


def bridge_request(settings, path, *, payload=None):
    try:
        # §19.1：连接旧IP时尽快报连接问题，保留原有的推理等待上限。
        read_timeout = 90 if payload is not None else (20 if path.startswith(("/flatlay/jobs/", "/reference/jobs/", "/style/jobs/")) else 6)
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
        if path.startswith('/style/jobs') and response.status_code in (404, 503) and payload is not None:
            raise LLMError('电脑中转尚未启用款式生图，请更新电脑中转。', code='reference_not_configured', retryable=False)
        if path.startswith('/style/jobs/') and response.status_code == 404 and payload is None:
            return {'model': MODEL, 'job_id': path.rsplit('/', 1)[-1], 'status': 'missing'}
        if path.startswith('/style/jobs') and response.status_code == 503:
            raise LLMError('电脑款式生图服务尚未就绪。', code='reference_not_configured', retryable=False)
        if path.startswith('/reference/jobs') and response.status_code == 409:
            raise LLMError('实物搜图已暂停，请重新分析生成AI款式示意。', code='reference_paused', retryable=False)
        if path.startswith('/reference/jobs') and response.status_code == 503:
            raise LLMError('电脑中转尚未启用实物搜图，请更新电脑中转。', code='reference_not_configured', retryable=False)
        if path.startswith('/reference/jobs/') and response.status_code == 404 and payload is None:
            return {'model': MODEL, 'job_id': path.rsplit('/', 1)[-1], 'status': 'missing'}
        code, text, retryable = errors.get(response.status_code, ("bridge_unavailable", "电脑中转本次调用失败，请检查个人CLI状态后重试。", True))
        raise LLMError(text, code=code, retryable=retryable)
    try:
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError()
        # 模型名不再等于固定常量 —— 直付那条会用电脑侧配置的模型名。
        # 这里只要求"是个非空名字"，另外 engine 必须是认识的，防止连到别的服务上。
        model = body.get("model")
        if not isinstance(model, str) or not model.strip():
            raise ValueError()
        if body.get("engine", ACCOUNT) not in ENGINES:
            raise ValueError()
        return body
    except (ValueError, TypeError):
        raise LLMError("电脑中转响应格式不正确，请检查连接地址。", code="bridge_unavailable")


def _write(settings, *, expected=None):
    with _settings_lock:
        if expected is not None and load_settings() != expected:
            raise LLMError("电脑连接设置已变化，请重新检查。", code="bridge_config_changed")
        return _write_locked(settings)


def _write_locked(settings):
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
    return settings


def replace_bridge_url(settings, url):
    updated = validate_settings(url, settings["token"], settings["engine"], settings["api_model"])
    return _write(updated, expected=settings)


def status_of(settings):
    """给 /health 和设置接口用的一份描述。model 跟着当前引擎走。"""
    api_model = settings.get("api_model") or ""
    engine = settings.get("engine") or ACCOUNT
    return {
        "configured": True,
        "url": settings["url"],
        "engine": engine,
        "model": api_model if engine == API and api_model else MODEL,
        "engines": {ACCOUNT: MODEL, API: api_model or None},
    }


def _probe_engines(settings):
    """连一次中转，确认版本，并把它能提供的直付模型名带回来。

    中转版本比手机新时才有 `engines`；老中转没有这个字段 → 直付不可用，
    这样手机不会挂一个点了必定失败的选项。
    """
    check = bridge_request(settings, "/health")
    if check.get("protocol") != 1 or check.get("status") != "ready":
        raise ValueError("电脑中转版本不兼容")
    engines = check.get("engines")
    return str(engines.get(API) or "") if isinstance(engines, dict) else ""


def save_settings(url, token, engine=None):
    current = None
    try:
        current = load_settings()
    except LLMError:
        current = None
    wanted = engine or (current or {}).get("engine") or ACCOUNT
    settings = validate_settings(url, token, wanted)
    settings["api_model"] = _probe_engines(settings)
    if settings["engine"] == API and not settings["api_model"]:
        # 换了台没有直付配置的电脑：别把「API 直连」这个选了也跑不起来的选项留下。
        settings["engine"] = ACCOUNT
    return status_of(_write(settings))


def set_engine(engine):
    if engine not in ENGINES:
        raise ValueError("识别引擎不正确")
    current = load_settings()
    if not current:
        raise ValueError("还没有配置电脑连接，请先填写电脑地址和配对码")
    settings = validate_settings(current["url"], current["token"], engine)
    settings["api_model"] = _probe_engines(settings)
    if engine == API and not settings["api_model"]:
        raise ValueError("电脑上没有可用的模型密钥，请先在电脑配置")
    return status_of(_write(settings, expected=current))


#: 补探直付模型名的节流。电脑关着时 /health 不能被连接超时拖住，
#: 失败后一分钟内不再试，成功则落盘、以后不必再探。
_probe_gate = {"at": 0.0}


def bridge_status(*, probe=True):
    try:
        settings = load_settings()
    except LLMError:
        return {"configured": True, "url": "", "model": MODEL, "invalid": True, "engine": "", "engines": {}}
    if not settings:
        return {"configured": False, "url": "", "model": MODEL, "engine": "", "engines": {}}
    if probe and not settings.get("api_model"):
        # 升级前保存的配置里没有这个字段。不补探的话「API 直连」永远不出现，
        # 而用户没有任何理由重新进设置页配对一次 —— 等于升级后功能凭空消失。
        now = time.monotonic()
        if now - _probe_gate["at"] >= 60:
            _probe_gate["at"] = now
            try:
                api_model = _probe_engines(settings)
                if api_model:
                    settings = _write({**settings, "api_model": api_model}, expected=settings)
            except (LLMError, ValueError):
                pass
    return status_of(settings)


class DesktopClient:
    def __init__(self, settings):
        self.settings = settings
        # 能力探测和展示都用"当前这条上游真正会跑的模型名"。
        self.model = (settings.get("api_model") if settings.get("engine") == API else None) or MODEL

    def capabilities(self):
        # CLI 用提示词约束，不伪装成服务端支持强制 schema，也不发付费探测。
        return Capabilities(model=self.model)

    def _chat(self, messages, schema, json_output):
        body = bridge_request(self.settings, "/chat", payload={
            "model": MODEL,
            "messages": messages,
            "schema": schema,
            "json_output": json_output,
            "engine": self.settings.get("engine", ACCOUNT),
        })
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            raise LLMError("电脑识别没有返回有效内容。", code="bridge_unavailable")
        return text

    def chat_text(self, messages, *, timeout=60.0, temperature=0.2):
        return self._chat(messages, None, False)

    def chat_json(self, messages, *, schema=None, timeout=60.0, temperature=0.0):
        return OpenAICompatClient._parse_json_text(self._chat(messages, schema, True))
