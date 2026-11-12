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
    reference_id: str | None = Field(default=None, min_length=1, max_length=64)


class PurchaseReferenceIn(BaseModel):
    plan_id: str = Field(min_length=1, max_length=64)
    direction_id: str = Field(min_length=1, max_length=64)
    job_id: UUID
    retry: bool = False


class PurchaseReferenceEnsureIn(BaseModel):
    plan_id: str = Field(min_length=1, max_length=64)


class PurchaseAnalysisIn(BaseModel):
    model_config = {"extra": "forbid"}
    request_id: UUID
    aesthetic: str = Field(min_length=1, max_length=32)
    ready_references: bool = False
    preference: str = Field('', max_length=160)
    source_draft_id: UUID | None = None
    direction_ids: list[str] = Field(default_factory=list, max_length=3)


class PurchasePrepareIn(BaseModel):
    model_config = {"extra": "forbid"}
    request_id: UUID
    draft_id: UUID
    direction_ids: list[str] = Field(min_length=1, max_length=3)
    preference: str = Field("", max_length=160)


class PurchaseBuilderIn(BaseModel):
    model_config = {"extra": "forbid"}
    request_id: UUID
    aesthetic: str = Field(min_length=1, max_length=32)
    mode: Literal["single", "multi", "outfit"]
    item_ids: list[str] = Field(min_length=1, max_length=12)


class PurchaseBuilderSaveIn(BaseModel):
    model_config = {"extra": "forbid"}
    reference_ids: dict[str, str] = Field(min_length=1, max_length=3)


@router.get('/purchase/builder')
def purchase_builder_state(inventory: bool = False) -> dict:
    from ..services import purchase_builder
    return purchase_builder.snapshot(_uid(), inventory=inventory)


@router.post('/purchase/builder')
def purchase_builder_start(payload: PurchaseBuilderIn) -> dict:
    from ..services import purchase_builder
    try:
        return purchase_builder.start(_uid(), str(payload.request_id), payload.aesthetic, payload.mode, payload.item_ids)
    except purchase.PurchaseError as error:
        raise HTTPException(error.status, str(error)) from error


@router.get('/purchase/builder/{request_id}')
def purchase_builder_request(request_id: UUID) -> dict:
    from ..services import purchase_builder
    try:
        return purchase_builder.request_state(_uid(), str(request_id))
    except purchase.PurchaseError as error:
        raise HTTPException(error.status, str(error)) from error


@router.post('/purchase/builder/{request_id}/query')
def purchase_builder_query(request_id: UUID) -> dict:
    from ..services import purchase_builder
    try:
        return purchase_builder.query(_uid(), str(request_id))
    except purchase.PurchaseError as error:
        raise HTTPException(error.status, str(error)) from error


@router.put('/purchase/builder/{request_id}/wanted')
def purchase_builder_save(request_id: UUID, payload: PurchaseBuilderSaveIn) -> dict:
    from ..services import purchase_builder
    try:
        return purchase_builder.save(_uid(), str(request_id), payload.reference_ids)
    except purchase.PurchaseError as error:
        raise HTTPException(error.status, str(error)) from error


@router.get('/purchase/intent')
def purchase_intent_state() -> dict:
    from ..services import purchase_intent
    return purchase_intent.state(_uid())


@router.post('/purchase/intent/analyze')
def purchase_intent_analyze(payload: PurchaseAnalysisIn) -> dict:
    from ..services import purchase_intent
    try:
        if payload.ready_references:
            from ..services import purchase_analysis
            return purchase_analysis.start(_uid(), str(payload.request_id), payload.aesthetic, preference=payload.preference,
                source_draft_id=str(payload.source_draft_id) if payload.source_draft_id else None, direction_ids=payload.direction_ids)
        if payload.preference or payload.source_draft_id or payload.direction_ids:
            raise purchase.PurchaseError('请通过配图分析调整偏好。', 422)
        return purchase_intent.analyze(_uid(), str(payload.request_id), payload.aesthetic)
    except purchase.PurchaseError as error:
        raise HTTPException(error.status, str(error)) from error


@router.post('/purchase/intent/analyze/{request_id}/query')
def purchase_analysis_query(request_id: UUID) -> dict:
    from ..services import purchase_analysis
    try:
        return purchase_analysis.query(_uid(), str(request_id))
    except purchase.PurchaseError as error:
        raise HTTPException(error.status, str(error)) from error


@router.get('/purchase/intent/request/{request_id}')
def purchase_intent_request(request_id: UUID) -> dict:
    from ..services import purchase_intent
    try:
        return purchase_intent.request_state(_uid(), str(request_id))
    except purchase.PurchaseError as error:
        raise HTTPException(error.status, str(error)) from error


@router.get('/purchase/intent/analysis/{draft_id}')
def purchase_intent_analysis(draft_id: UUID) -> dict:
    from ..services import purchase_intent
    try:
        return purchase_intent.analysis(_uid(), str(draft_id))
    except purchase.PurchaseError as error:
        raise HTTPException(error.status, str(error)) from error


@router.post('/purchase/intent/prepare')
def purchase_intent_prepare(payload: PurchasePrepareIn) -> dict:
    from ..services import purchase_intent
    try:
        return purchase_intent.prepare(_uid(), str(payload.request_id), str(payload.draft_id), payload.direction_ids, payload.preference)
    except purchase.PurchaseError as error:
        raise HTTPException(error.status, str(error)) from error


@router.post('/purchase/intent/prepare/{request_id}/query')
def purchase_intent_query(request_id: UUID) -> dict:
    from ..services import purchase_intent
    try:
        return purchase_intent.query(_uid(), str(request_id))
    except purchase.PurchaseError as error:
        raise HTTPException(error.status, str(error)) from error


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
        return purchase.choice(_uid(), payload.plan_id, payload.direction_id, payload.action, reference_id=payload.reference_id)
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
