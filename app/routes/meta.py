"""健康检查与元信息路由。"""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import config, db, taxonomy
from ..models import TaxonomyOut
from ..schema import SCHEMA_VERSION
from ..services.llm.client import LLMError, model_ready
from ..services.llm.desktop import bridge_status, save_settings

router = APIRouter(tags=["meta"])


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
    bridge = bridge_status()
    return {
        "status": "healthy",
        "schema_version": SCHEMA_VERSION,
        "db_path": str(db.DB_PATH),
        "item_count": items,
        "pending_tagging": pending,
        "llm_ready": model_ready(),
        "vision_ready": model_ready(vision=True),
        "llm_model": bridge["model"] if bridge["configured"] else config.LLM_MODEL or None,
        "vision_model": bridge["model"] if bridge["configured"] else config.VISION_MODEL or None,
        "desktop_bridge": bridge,
        "flatlay_configured": bool(bridge["configured"] and not bridge.get("invalid")),
    }


class BridgeSettings(BaseModel):
    url: str = Field(max_length=200)
    token: str = Field(max_length=128, repr=False)


@router.put("/settings/desktop-bridge")
def configure_bridge(body: BridgeSettings, request: Request):
    # 本地服务没有跨站授权；拒绝非 JSON / 外站页面修改连接配置。
    origin = request.headers.get("origin")
    expected = str(request.base_url).rstrip("/")
    if urlsplit(expected).hostname not in ("127.0.0.1", "localhost", "::1") or (origin and origin != expected):
        raise HTTPException(403, "请在应用内设置电脑连接")
    try:
        return save_settings(body.url, body.token)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except LLMError as error:
        raise HTTPException(503, error.detail()) from error
    except OSError:
        raise HTTPException(503, "无法保存电脑连接，请稍后重试")


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
