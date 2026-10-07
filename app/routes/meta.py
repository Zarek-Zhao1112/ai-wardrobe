"""健康检查与元信息路由。"""

from __future__ import annotations

from fastapi import APIRouter

from .. import config, db, taxonomy
from ..models import TaxonomyOut
from ..schema import SCHEMA_VERSION

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
    return {
        "status": "healthy",
        "schema_version": SCHEMA_VERSION,
        "db_path": str(db.DB_PATH),
        "item_count": items,
        "pending_tagging": pending,
        "llm_ready": config.LLM_READY,
        "vision_ready": config.VISION_READY,
        "llm_model": config.LLM_MODEL or None,
        "vision_model": config.VISION_MODEL or None,
    }


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
