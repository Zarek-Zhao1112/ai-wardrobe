"""健康检查与元信息路由。"""

from __future__ import annotations

from urllib.parse import urlsplit
import hashlib
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, SecretStr
from starlette.concurrency import run_in_threadpool

from .. import config, db, taxonomy
from ..models import TaxonomyOut
from ..schema import SCHEMA_VERSION
from ..services.llm.client import LLMError, model_ready, reset_client_cache
from ..services.llm import channel
from ..services.llm.bridge_repair import repair_bridge
from ..services.llm.desktop import bridge_status, save_settings, set_engine

router = APIRouter(tags=["meta"])


@router.get("/sw.js", include_in_schema=False)
def service_worker_script() -> Response:
    # APK静态文件时间固定，默认mtime+大小ETag会把同长度的新worker误判成旧版。
    # 用同一份字节生成响应和内容摘要，不沿用StaticFiles的条件304判断。
    try:
        content = (config.WEB_DIR / "sw.js").read_bytes()
    except FileNotFoundError as error:
        raise HTTPException(404, "离线脚本不存在") from error
    return Response(content, media_type="application/javascript", headers={
        "Cache-Control": "no-store",
        "ETag": '"' + hashlib.sha256(content).hexdigest() + '"',
    })


@router.get("/health")
def health() -> dict:
    """健康检查 + 能力开关。

    `llm_ready` / `vision_ready` 让**前端能直接告诉用户"模型没配"**，
    而不是等他点了打标、拿到 503 才知道。
    """
    with db.conn() as c:
        items = c.execute("SELECT COUNT(*) AS n FROM clothing_items").fetchone()["n"]
        pending = c.execute(
            "SELECT COUNT(*) AS n FROM clothing_items WHERE tagging_status='pending'"
        ).fetchone()["n"]
    state = channel.load()
    bridge = bridge_status(probe=not state.get("mode"))
    ai_channel = channel.public_status(state, bridge)
    direct = ai_channel["mode"] == "direct"
    api_profile = channel.profile(state)
    direct_model = api_profile.get("model", config.LLM_MODEL) or None
    direct_vision = api_profile.get("vision_model", config.VISION_MODEL) or None
    return {
        "status": "healthy",
        "schema_version": SCHEMA_VERSION,
        "db_path": str(db.DB_PATH),
        "item_count": items,
        "pending_tagging": pending,
        "llm_ready": model_ready(),
        "vision_ready": model_ready(vision=True),
        "llm_model": direct_model if direct else bridge["model"],
        "vision_model": direct_vision if direct else bridge["model"],
        "engine": bridge.get("engine") or "",
        "ai_channel": ai_channel,
        "desktop_bridge": bridge,
        "flatlay_configured": bool(not direct and bridge["configured"] and not bridge.get("invalid")),
    }


class AIChannelSettings(BaseModel):
    mode: Literal["direct", "account"]
    provider: Literal["openai", "deepseek"] | None = None
    key: SecretStr = SecretStr("")
    model: str = Field(default="", max_length=150)
    vision_model: str = Field(default="", max_length=150)


@router.put("/settings/ai-channel")
async def configure_ai_channel(request: Request):
    origin = request.headers.get("origin")
    expected = str(request.base_url).rstrip("/")
    loopback = {"127.0.0.1", "localhost", "::1"}
    if (urlsplit(expected).hostname not in loopback or not request.client
            or request.client.host not in loopback or (origin and origin != expected)):
        raise HTTPException(403, "请在本机 App 内配置 AI 通道。")
    if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
        raise HTTPException(415, "请在 App 内保存 AI 通道。")
    try:
        body = AIChannelSettings.model_validate(await request.json())
    except (ValueError, TypeError):
        # 校验失败的响应里也不能带用户输入的 Key。
        raise HTTPException(400, "AI 通道配置格式不正确。") from None
    return await run_in_threadpool(_save_ai_channel, body)


def _save_ai_channel(body: AIChannelSettings):
    if body.mode == "account":
        bridge = bridge_status(probe=False)
        if not bridge["configured"] or bridge.get("invalid"):
            raise HTTPException(409, "请先在电脑识别设置中连接并配对电脑。")
        if body.provider is not None:
            raise HTTPException(400, "请在 API 直连通道保存 API 服务商。")
    try:
        channel.save(body.mode, provider=body.provider, key=body.key.get_secret_value(),
                     model=body.model, vision_model=body.vision_model)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except OSError:
        raise HTTPException(503, "无法保存本机 AI 配置，请稍后重试。") from None
    reset_client_cache()
    return health()


class BridgeSettings(BaseModel):
    url: str = Field(max_length=200)
    token: str = Field(max_length=128, repr=False)
    keep_direct: bool = False


@router.put("/settings/desktop-bridge")
def configure_bridge(body: BridgeSettings, request: Request):
    # 本地服务没有跨站授权；拒绝非 JSON / 外站页面修改连接配置。
    origin = request.headers.get("origin")
    expected = str(request.base_url).rstrip("/")
    if urlsplit(expected).hostname not in ("127.0.0.1", "localhost", "::1") or (origin and origin != expected):
        raise HTTPException(403, "请在应用内设置电脑连接")
    try:
        if body.keep_direct and not bridge_status(probe=False)["configured"]:
            channel.initialize_direct()
        return save_settings(body.url, body.token)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except LLMError as error:
        raise HTTPException(503, error.detail()) from error
    except OSError:
        raise HTTPException(503, "无法保存电脑连接，请稍后重试")


class EngineSettings(BaseModel):
    engine: str = Field(pattern="^(account|api)$")


class BridgeRepair(BaseModel):
    candidate: str | None = Field(default=None, max_length=15)


@router.post("/settings/desktop-bridge/repair")
async def repair_desktop_bridge(body: BridgeRepair, request: Request):
    origin = request.headers.get("origin")
    expected = str(request.base_url).rstrip("/")
    if urlsplit(expected).hostname not in ("127.0.0.1", "localhost", "::1") or (origin and origin != expected):
        raise HTTPException(403, "请在应用内修复电脑连接")
    try:
        return await repair_bridge(body.candidate)
    except LLMError as error:
        raise HTTPException(503, error.detail()) from error
    except OSError as error:
        raise HTTPException(503, "无法保存电脑连接，请稍后重试") from error


@router.put("/settings/engine")
def configure_engine(body: EngineSettings, request: Request):
    """只切「走哪条上游」，不动电脑地址和配对码。

    成功时直接回一份新的 health —— 前端拿到就能整块重画，不用再请求一次。
    """
    origin = request.headers.get("origin")
    expected = str(request.base_url).rstrip("/")
    if urlsplit(expected).hostname not in ("127.0.0.1", "localhost", "::1") or (origin and origin != expected):
        raise HTTPException(403, "请在应用内设置识别引擎")
    try:
        set_engine(body.engine)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except LLMError as error:
        raise HTTPException(503, error.detail()) from error
    except OSError:
        raise HTTPException(503, "无法保存识别引擎，请稍后重试")
    # 客户端是进程级单例，引擎变了必须让它重建，否则下一次调用还带着旧的上游。
    reset_client_cache()
    return health()


@router.get("/taxonomy", response_model=TaxonomyOut)
def get_taxonomy() -> dict:
    """返回受控词表，供前端下拉框和 AI 提示词使用。"""
    return {
        "slots": taxonomy.SLOTS,
        "types": {
            k: {"label": v[0], "slot": v[1]} for k, v in taxonomy.CLOTHING_TYPES.items()
        },
        "vocabularies": taxonomy.VOCABULARIES,
    }
