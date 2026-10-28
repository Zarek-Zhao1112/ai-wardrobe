"""§23：平铺图任务只走已配对电脑的官方个人CLI，不回退其他付费接口。"""
from __future__ import annotations

import base64
import re

from .. import taxonomy
from . import images
from .llm.client import LLMError
from .llm.desktop import bridge_request, load_settings


def _settings():
    settings = load_settings()
    if not settings:
        raise LLMError("请先在服务状态中连接电脑识别中转，再使用WorkBuddy积分生成平铺图。", code="flatlay_not_configured", retryable=False)
    return settings


def _token(value):
    if not re.fullmatch(r"[0-9a-f]{32}", value or ""):
        raise ValueError("平铺图任务标识无效")
    return value


def start(token, data, kind):
    _token(token)
    sniffed = images.sniff(data)
    if not sniffed or sniffed[0] not in ("image/jpeg", "image/png", "image/webp"):
        raise ValueError("平铺图支持JPG、PNG、WebP图片，请先裁剪这张照片")
    if not data or len(data) > images.MAX_IMAGE_BYTES: raise ValueError("源图为空或超过10MB")
    payload = {"id": token, "kind": taxonomy.resolve_type(kind or "other"), "image": "data:" + sniffed[0] + ";base64," + base64.b64encode(data).decode()}
    return bridge_request(_settings(), "/flatlay/jobs", payload=payload)


def status(token):
    return bridge_request(_settings(), "/flatlay/jobs/" + _token(token))
