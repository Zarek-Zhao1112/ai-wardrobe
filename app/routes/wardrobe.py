"""衣橱结构分析路由。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Literal
from uuid import UUID

from .. import db, taxonomy
from ..models import GapAnalysisOut
from ..services import wardrobe as svc
from ..services import purchase

router = APIRouter(prefix="/wardrobe", tags=["wardrobe"])


def _uid() -> str:
    return db.get_default_user_id()


class PurchaseIn(BaseModel):
    request_id: UUID
    aesthetic: str = Field("auto", max_length=32)


class PurchaseChoiceIn(BaseModel):
    plan_id: str = Field(min_length=1, max_length=64)
    direction_id: str = Field(min_length=1, max_length=64)
    action: Literal["save", "unsave", "dismiss"]


class PurchaseReferenceIn(BaseModel):
    plan_id: str = Field(min_length=1, max_length=64)
    direction_id: str = Field(min_length=1, max_length=64)
    job_id: UUID
    retry: bool = False


class PurchaseReferenceEnsureIn(BaseModel):
    plan_id: str = Field(min_length=1, max_length=64)


@router.post('/purchase/references/ensure')
def purchase_reference_ensure(payload: PurchaseReferenceEnsureIn) -> dict:
    from ..services import purchase_reference
    try: return purchase_reference.ensure_for_plan(_uid(), payload.plan_id)
    except purchase.PurchaseError as error: raise HTTPException(error.status, str(error))


@router.post('/purchase/references')
def purchase_reference_start(payload: PurchaseReferenceIn) -> dict:
    from ..services import purchase_reference
    try: return purchase_reference.start(_uid(), payload.plan_id, payload.direction_id, str(payload.job_id), retry=payload.retry)
    except purchase.PurchaseError as error: raise HTTPException(error.status, str(error))


@router.get('/purchase/references/{job_id}')
def purchase_reference_status(job_id: UUID) -> dict:
    from ..services import purchase_reference
    try: return purchase_reference.status(_uid(), str(job_id))
    except purchase.PurchaseError as error: raise HTTPException(error.status, str(error))


@router.get("/purchase")
def purchase_state(request_id: UUID | None = None) -> dict:
    return purchase.get_state(_uid(), str(request_id) if request_id else None)


@router.post("/purchase")
def purchase_generate(payload: PurchaseIn) -> dict:
    try:
        return purchase.generate(_uid(), str(payload.request_id), payload.aesthetic)
    except purchase.PurchaseError as error:
        raise HTTPException(error.status, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, "未知审美方向，请重新选择。") from error


@router.put("/purchase/choice")
def purchase_choice(payload: PurchaseChoiceIn) -> dict:
    try:
        return purchase.choice(_uid(), payload.plan_id, payload.direction_id, payload.action)
    except purchase.PurchaseError as error:
        raise HTTPException(error.status, str(error)) from error


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
