"""衣橱结构分析路由。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from .. import db, taxonomy
from ..models import GapAnalysisOut
from ..services import wardrobe as svc

router = APIRouter(prefix="/wardrobe", tags=["wardrobe"])


def _uid() -> str:
    return db.get_default_user_id()


def _check_season(season: str | None) -> str | None:
    if season is not None and season not in taxonomy.SEASON_LABELS:
        raise HTTPException(
            422, f"season 只能是 {list(taxonomy.SEASON_LABELS)} 之一，或留空取当前季节"
        )
    return season


@router.get("/gaps", response_model=GapAnalysisOut)
def gaps(
    season: str | None = Query(None, description="spring/summer/autumn/winter，留空取当前季节"),
) -> dict:
    """衣橱缺口分析：季节化组合数 + 断链槽位 + 补货建议（ΔC × 多样性折扣）+ 旧规则对照。"""
    return svc.analyze(_uid(), season=_check_season(season))


@router.get("/combinations")
def combinations(
    limit: int = Query(20, ge=1, le=200),
    season: str | None = Query(None),
) -> dict:
    """枚举可搭出的搭配样例，以及该季的组合总数。"""
    return svc.combinations_preview(_uid(), limit=limit, season=_check_season(season))
