"""衣橱单品路由。"""

from __future__ import annotations

import re
import sqlite3

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from .. import db, taxonomy
from ..models import (
    BulkItemsIn,
    BulkItemsOut,
    ItemCreate,
    ItemCreateOut,
    ItemListOut,
    ItemOut,
    ItemParseIn,
    ItemUpdate,
    ItemsParseOut,
    OkOut,
    WearEventOut,
    WearLogIn,
)
from ..services import images as image_svc
from ..services import ingest as ingest_svc
from ..services import items as svc
from ..services import tagging as tagging_svc
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


@router.post("/parse", response_model=ItemsParseOut)
def parse_pasted_items(payload: ItemParseIn) -> dict:
    """解析粘贴来的商品文案。**只解析，不落库、不写会话状态**（§11.3）。

    待确认清单由**前端**持有：用户在页面上勾选/改完，一次性提交 `/items/bulk`。
    好处是**不用动 `conversation_state`、也不用动 `schema.py`**（两者都在
    Do-Not-Modify 清单里）。代价是刷新页面清单会丢，可以接受。

    ⚠️ 声明在 `/{item_id}` **之前**：否则 `POST /items/parse` 会被
    `/{item_id}` 当成品单 id 吃掉（跟 `/bulk` 同一个理由）。
    """
    uid = _uid()
    candidates = ingest_svc.build_candidates(payload.text)
    if not candidates:
        # ⚠️ 失败提示要**说清为什么、下一步做什么**（2026-10-20 用户反馈：
        #    粘了个商品链接，只看到"没读出商品名"，不知道是坏了还是自己用错）。
        #
        # 两种最常见的情况分开说：
        #   ① 只粘了链接 → 用户以为"粘链接就能录"（这是很自然的预期）。
        #      必须讲明**不抓网页**是刻意的（淘宝无登录态读不到，实测 6 条入口全被挡），
        #      否则他会反复试、以为是 bug。
        #   ② 文字里没标题 → 给个能直接照抄的例子。
        text = (payload.text or "").strip()
        has_url = "http://" in text or "https://" in text
        has_cjk = bool(re.search(r"[\u4e00-\u9fff]", text))
        example = "例如：宽松直筒牛仔裤 深灰"
        if has_url and not has_cjk:
            message = (
                "这是个商品链接，但我读不到链接里的内容 —— 我不会去打开那个网页"
                "（淘宝等平台在没登录时读不到）。把商品标题一起粘进来就行，" + example
            )
        elif has_url:
            message = "这段里有链接，但没读出能当标题的文字 —— 补一句商品标题就行，" + example
        else:
            message = "这段我没读出商品名 —— 直接粘商品标题就行，" + example
        return {"total": 0, "items": [], "message": message}

    # 疑似重复只看"归一化标题完全一致"。**只提示，不阻止** ——
    # 用户确实可能买了两件一样的（§11.3 失败语义）。
    seen = {
        ingest_svc.normalize_title(it.get("name") or "")
        for it in svc.iter_all_items(uid)
    }
    seen.discard("")

    items = []
    for idx, c in enumerate(candidates):
        key = str(c.fields.get("type") or "other")
        items.append(
            {
                "index": idx,
                "title": c.title,
                "url": c.url or None,
                "code": c.code or None,
                "platform": c.platform or None,
                "fields": c.fields,
                "unresolved": c.unresolved,
                "type_key": key,
                "type_label": taxonomy.label_of(key),
                "needs_type": c.needs_type,
                "duplicate_hint": (
                    "你已经有一件同名的了" if ingest_svc.normalize_title(c.title) in seen else None
                ),
                "used_llm": c.pick.used_llm,
                "llm_error": c.pick.error or None,
            }
        )
    return {"total": len(items), "items": items, "message": None}


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

    # 存图成功后顺手跑一次打标（上传即识别，认完用户能改）。
    # ⚠️ 打标**失败绝不能让上传变成 4xx/5xx** —— 照片已经存好了，
    #    单品留在 `pending` 就行，属性用户可以手填。所以走"只回报状态"的那个入口。
    #
    # ⚠️ 外面再包一层 `Exception` 兜底：`tag_and_apply` 自己已经承诺"不抛"，
    #    但那是**约定**不是**保证** —— 万一它内部漏接了某个异常类型
    #    （比如数据库 `OperationalError`），让上传变成 500 是最坏结果：
    #    照片已经换成新的了，用户却收到"上传失败"，重试还会再撞一次
    #    `image_hash` 唯一索引。这里兜住，把结果如实告诉前端。
    try:
        tagging = tagging_svc.tag_and_apply(uid, item_id)
    except Exception as e:  # noqa: BLE001 —— 兜底就是这里的目的
        tagging = {
            "status": "failed",
            "tags": {},
            "unresolved": {},
            "ai_confidence": None,
            "message": f"识别失败（照片已保存，可手动填属性）：{type(e).__name__}",
            "item": None,
        }

    return {
        **saved,
        "item": tagging.get("item") or svc.get(item_id, uid),
        "tagging": tagging,
    }
