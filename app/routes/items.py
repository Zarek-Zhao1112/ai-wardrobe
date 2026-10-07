"""衣橱单品路由。"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from .. import db
from ..models import (
    BulkItemsIn,
    BulkItemsOut,
    ItemCreate,
    ItemCreateOut,
    ItemListOut,
    ItemOut,
    ItemUpdate,
    OkOut,
    WearEventOut,
    WearLogIn,
)
from ..services import images as image_svc
from ..services import items as svc
from ..services import wardrobe
from ..services import wear as wear_svc

router = APIRouter(prefix="/items", tags=["items"])


def _uid() -> str:
    return db.get_default_user_id()


@router.get("", response_model=ItemListOut)
def list_items(
    slot: str | None = Query(None, description="top/bottom/full/outer/shoes/accessory"),
    type: str | None = Query(None, description="受控 type_key 或中文名"),
    tagging_status: str | None = Query(None, description="pending|tagged"),
    favorite: bool | None = None,
    include_archived: bool = False,
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    return svc.list_items(
        _uid(),
        slot=slot,
        type_key=type,
        tagging_status=tagging_status,
        favorite=favorite,
        include_archived=include_archived,
        search=search,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=ItemCreateOut)
def create_item(payload: ItemCreate) -> dict:
    """建一件单品，并立即返回"这次录入带来了多少新搭配"。

    设计文档 §9.1 / §9.2：录入这个体力活本身必须有可感知的回报，
    否则用户没有动机把整个衣橱录进来。所以响应里带上
    组合数的前后变化 + 用现有衣橱搭出来的现成样例。
    """
    uid = _uid()
    before = wardrobe.total_combinations(uid)["total_combinations"]
    try:
        item = svc.create(uid, payload)
    except sqlite3.IntegrityError as e:
        # image_hash 唯一索引命中：同一张图已经录过了
        raise HTTPException(400, f"这件衣服可能已经录过了（图片指纹重复）：{e}") from e
    after = wardrobe.total_combinations(uid)["total_combinations"]
    previews = wardrobe.sample_outfits_with(uid, item["id"], limit=3)

    delta = after - before
    if delta > 0:
        message = f"已入库。你的可选搭配从 {before} 套变成 {after} 套。"
    elif after > 0:
        message = f"已入库。当前这季共 {after} 套组合——再补一件基础单品就能明显变多。"
    else:
        message = "已入库。现在还没有能搭出的整套，再补上缺的基础单品就能成型。"

    return {
        "item": item,
        "combination_delta": {"before": before, "after": after},
        "sample_outfits": previews,
        "message": message,
    }


@router.post("/bulk", response_model=BulkItemsOut)
def create_items_bulk(payload: BulkItemsIn) -> dict:
    """批量导入。

    ⚠️ **声明在 `/{item_id}` 之前**是刻意的：现在还没有 `POST /items/{item_id}`，
    所以不存在冲突，但把具体路径放在通配路径前面是防御性的 ——
    将来谁加了一个 `POST /{item_id}`，这条不会被静默吞掉。

    失败语义（2026-10-03 拍板）：**跳过并回报清单**，不是整体回滚。
    """
    uid = _uid()
    before = wardrobe.total_combinations(uid)["total_combinations"]
    result = svc.create_many(uid, payload.items)
    after = wardrobe.total_combinations(uid)["total_combinations"]

    parts = [f"共 {result['total']} 件，成功 {result['created']}"]
    if result["skipped"]:
        parts.append(f"跳过 {result['skipped']}")
    if result["failed"]:
        parts.append(f"失败 {result['failed']}")
    message = "，".join(parts) + "。"

    delta = after - before
    if delta > 0:
        message += f"可选搭配从 {before} 套变成 {after} 套。"
    elif after > 0:
        message += f"当前这季共 {after} 套组合。"

    return {
        **result,
        "combination_delta": {"before": before, "after": after},
        "message": message,
    }


@router.get("/{item_id}", response_model=ItemOut)
def get_item(item_id: str) -> dict:
    got = svc.get(item_id, _uid())
    if not got:
        raise HTTPException(404, "单品不存在")
    return got


@router.patch("/{item_id}", response_model=ItemOut)
def update_item(item_id: str, payload: ItemUpdate) -> dict:
    got = svc.update(item_id, payload, _uid())
    if not got:
        raise HTTPException(404, "单品不存在")
    return got


@router.delete("/{item_id}", response_model=OkOut)
def delete_item(item_id: str) -> dict:
    if not svc.delete(item_id, _uid()):
        raise HTTPException(404, "单品不存在")
    return {"ok": True}


@router.post("/wear", response_model=WearEventOut)
def log_wear(payload: WearLogIn) -> dict:
    try:
        return wear_svc.log_wear(_uid(), payload)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except sqlite3.IntegrityError as e:
        # 兜底：外键 / 唯一约束冲突一律归成 400，不要漏成 500
        raise HTTPException(400, f"数据约束冲突：{e}") from e


@router.get("/wear/events", response_model=list[WearEventOut])
def list_wear_events(limit: int = 50) -> list[dict]:
    return wear_svc.list_wear_events(_uid(), limit=limit)


@router.post("/{item_id}/image")
def upload_item_image(item_id: str, file: UploadFile = File(...)) -> dict:
    """给单品传一张照片。**会覆盖旧图**（一个单品只保留一张）。

    类型靠**读文件头**判断，不信 `content_type` —— 微信转发的图片
    有时报 `application/octet-stream`，信它会把正常图片拒之门外。
    """
    uid = _uid()
    data = file.file.read()
    try:
        saved = image_svc.save_image(uid, item_id, data)
    except image_svc.ImageError as e:
        raise HTTPException(400, str(e)) from e
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except sqlite3.IntegrityError as e:
        raise HTTPException(400, f"这张图已经录给别的单品了（图片指纹重复）：{e}") from e
    return {**saved, "item": svc.get(item_id, uid)}
