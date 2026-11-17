"""本机选择的 AI 通道。旧版电脑配对继续使用原有配置。"""
from __future__ import annotations

import json
import os
import re
import tempfile
from threading import RLock

from ... import config

PROVIDERS = {"openai": "https://api.openai.com/v1", "deepseek": "https://api.deepseek.com"}
DIRECT_MODELS = {"openai": "gpt-6-luna", "deepseek": "deepseek-flash"}
_lock = RLock()


def _path():
    return config.DATA_DIR / "ai-channel.json"


def load():
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("mode") not in ("direct", "account"):
            raise ValueError()
        return data
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        # 损坏时停在本机直连，避免偷偷切到电脑账号。
        return {"mode": "direct", "invalid": True}


def profile(state):
    value = state.get("profile")
    if not isinstance(value, dict) or value.get("provider") not in PROVIDERS:
        return {}
    if not all(isinstance(value.get(key), str) for key in ("key", "model", "vision_model")):
        return {}
    if not (value["key"] and value["model"]):
        return {}
    # 一个模型管两用：两家服务商都只有一档高能力模型，文字分析和照片识别共用它。
    # 界面已经不再分别选，老配置里 vision_model 为空（曾经可以「暂不启用图片识别」）的
    # 也按同一个模型算 —— 否则升级后打标会报「请选择图片识别模型」。
    return {**value, "vision_model": value["vision_model"] or value["model"]}


def initialize_direct():
    with _lock:
        if not load():
            _write({"mode": "direct", "profile": {}})


def save(mode, *, provider=None, key="", model="", vision_model=""):
    with _lock:
        state = load()
        current = profile(state)
        if provider is not None:
            key, model, vision_model = key.strip(), model.strip(), vision_model.strip()
            if provider not in DIRECT_MODELS or model != DIRECT_MODELS[provider]:
                raise ValueError("请从该服务商的模型列表中选择模型。")
            if vision_model and vision_model != model:
                raise ValueError("图片识别和文字分析必须用同一个模型。")
            if not key and current.get("provider") == provider:
                key = current.get("key", "")
            if not re.fullmatch(r"[!-~]{1,512}", key):
                raise ValueError("请填写该服务商的有效 API Key。")
            current = {"provider": provider, "key": key, "model": model, "vision_model": vision_model}
        if mode == "direct" and not current and not config.LLM_READY:
            raise ValueError("请先配置本机 API Key。")
        _write({"mode": mode, "profile": current})


def _write(value):
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as output:
            temporary = output.name
            json.dump(value, output)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def public_status(state, bridge):
    active = state.get("mode") or ("legacy" if bridge["configured"] else "direct")
    current = profile(state)
    return {
        "mode": active,
        "provider": current.get("provider"),
        "model": current.get("model", ""),
        "vision_model": current.get("vision_model", ""),
        "configured": bool(current),
        "direct_ready": bool(current or (config.LLM_READY and not state.get("invalid"))),
    }
