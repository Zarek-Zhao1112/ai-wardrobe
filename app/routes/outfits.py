"""搭配路由：推荐 / 历史 / 反馈。

⚠️ 路由顺序有讲究：`/recommend` 和 `/feedback` 是**具体路径**，
必须声明在 `/{outfit_id}` 之前 —— 否则 `GET /outfits/recommend` 会被
通配路由吃掉，然后报"搭配 recommend 不存在"（一个很难看的 404）。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from .. import db, taxonomy
from ..models import FeedbackIn, OutfitOut
from ..services import feedback as feedback_svc
from ..services import recommend as recommend_svc
from ..services import aesthetics

router = APIRouter(prefix="/outfits", tags=["outfits"])


def _uid() -> str:
    return db.get_default_user_id()


def _check_season(season: str | None) -> str | None:
    """校验 `season` 参数。非法值直接 400，**不要静默返回空组合**。

    `/wardrobe/*` 早就有这个校验，`/outfits/recommend` 之前漏了 ——
    传任何字符串进去都会静默给出 0 套搭配，排查时完全看不出是参数写错了。
    （W4 审查发现。）
    """
    if season is None:
        return None
    if season not in taxonomy.SEASON_LABELS:
        options = "/".join(taxonomy.SEASON_LABELS)
        raise HTTPException(400, f"未知的季节：{season}（可选：{options}）")
    return season


@router.post("/recommend")
def recommend_outfits(
    scene: str | None = Query(None, description="兼容旧版参数，新搭配链路已忽略"),
    season: str | None = Query(None, description="不传则取当前季节"),
    limit: int = Query(3, ge=1, le=10),
    use_llm: bool = Query(True, description="传 false 则只用本地排序，不调模型"),
    aesthetic: str = Query(aesthetics.DEFAULT, description="完整审美方案id"),
    anchor_item_id: str | None = Query(None, min_length=1, max_length=128, description="每套搭配都必须包含的衣物"),
) -> dict:
    """推荐几套搭配。

    **没配模型也不会失败**：自动退回本地排序，响应里 `degraded=true`、`reason` 为空。
    这样未配 key 也能把整条链路跑通。
    """
    try:
        aesthetics.get_profile(aesthetic)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    try:
        return recommend_svc.recommend(
            _uid(), aesthetic=aesthetic, season=_check_season(season),
            limit=limit, source="api", use_llm=use_llm, anchor_item_id=anchor_item_id,
        )
    except recommend_svc.AnchorUnavailable as error:
        raise HTTPException(400, str(error)) from error


@router.get("/aesthetics")
def list_aesthetics() -> dict:
    return {"aesthetics": aesthetics.list_profiles(), "default": aesthetics.DEFAULT}


@router.post("/feedback")
def submit_feedback(payload: FeedbackIn) -> dict:
    """对某套搭配反馈。

    这个接口会**顺带更新偏好记忆** —— 闭环真正闭合的地方就在这里。
    只写 `feedback` 表而不动记忆，AI 就永远学不会。
    """
    try:
        return feedback_svc.submit(
            _uid(),
            feedback_type=payload.feedback_type,
            outfit_id=payload.outfit_id,
            rating=payload.rating,
            comfort_rating=payload.comfort_rating,
            style_rating=payload.style_rating,
            comment=payload.comment,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("")
def list_outfits(limit: int = Query(20, ge=1, le=100)) -> dict:
    """最近生成过的搭配（含反馈后的状态：proposed / accepted / rejected / swapped）。"""
    return {"outfits": recommend_svc.list_outfits(_uid(), limit=limit)}


@router.get("/{outfit_id}", response_model=OutfitOut)
def get_outfit(outfit_id: str) -> dict:
    got = recommend_svc.get_outfit(_uid(), outfit_id)
    if not got:
        raise HTTPException(404, "搭配不存在")
    return got
