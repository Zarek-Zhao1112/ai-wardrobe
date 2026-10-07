"""偏好记忆路由：让人看见 AI 现在「怎么理解你」。

这个模块的接口不是为了功能，是为了**可信**。
设计文档 §7 的原则是「声称与测量必须成对出现」——
如果用户看不到系统学到的偏好，那「越用越懂你」就只是句营销文案。
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from .. import db
from ..models import PreferenceOut
from ..services import preference as pref_svc

router = APIRouter(prefix="/preferences", tags=["preferences"])


def _uid() -> str:
    return db.get_default_user_id()


@router.get("", response_model=list[PreferenceOut])
def list_preferences(
    season: str | None = Query(None, description="不传则取当前季节"),
    scene: str | None = Query(None, description="场合，取值见 /taxonomy 的 scene"),
    tag_type: str | None = Query(
        None, description="color|style|occasion|fit|material|pattern|slot"
    ),
) -> list[dict]:
    """按当前上下文取偏好，走分层回退（细桶不足自动退到粗桶）。

    返回按 `confidence` 降序 —— 最可信的排前面。
    """
    return pref_svc.query(_uid(), context=(season, scene), tag_type=tag_type)


@router.get("/contexts")
def list_contexts() -> dict:
    """列出记忆里**实际存在**的那些桶。

    调试用，但价值不低：一眼就能看出分层回退是不是按预期在工作
    （比如"只有 global 有数据"说明分桶太细、样本全落在回退层）。
    """
    with db.conn() as c:
        rows = c.execute(
            """
            SELECT context_key,
                   COUNT(*)                AS rows,
                   SUM(evidence_count)     AS evidence
              FROM preference_memory
             WHERE user_id=?
             GROUP BY context_key
             ORDER BY evidence DESC
            """,
            (_uid(),),
        ).fetchall()
    return {"contexts": [dict(r) for r in rows]}
