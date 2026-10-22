"""单品相关的业务逻辑（路由层保持薄）。

只管"衣橱里有什么"：单品的增删改查。
"哪天穿了什么"（穿着事件 + 单品穿着历史）已拆到 services/wear.py。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any, Optional

from .. import db, taxonomy
from ..models import ItemCreate, ItemUpdate
from . import images

logger = logging.getLogger(__name__)

# 存成 JSON 的字段
JSON_LIST_FIELDS = ("colors", "style", "season", "scene")
JSON_DICT_FIELDS = ("tags",)

# 允许被 PATCH 直接覆盖的列
WRITABLE_COLUMNS = (
    "type", "subtype", "name", "brand",
    "colors", "primary_color", "pattern", "material", "fit", "length",
    "style", "season", "formality", "scene", "tags",
    "purchase_date", "purchase_price", "favorite", "notes",
    "image_path", "is_archived",
)


def _loads_list(raw: Any) -> list:
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else []
    except Exception:
        return []


def _loads_dict(raw: Any) -> dict:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def to_out(row: Any) -> dict:
    """DB row -> 可序列化的 dict（供 ItemOut 使用）。"""
    d = dict(row)
    d["is_archived"] = bool(d.get("is_archived"))
    d["favorite"] = bool(d.get("favorite"))
    for f in JSON_LIST_FIELDS:
        d[f] = _loads_list(d.get(f))
    for f in JSON_DICT_FIELDS:
        d[f] = _loads_dict(d.get(f))
    d["type_label"] = taxonomy.label_of(d.get("type", "other"))
    if not d.get("slot"):
        d["slot"] = taxonomy.slot_of(d.get("type", "other"))
    return d


def create(user_id: str, payload: ItemCreate) -> dict:
    tid = db.new_id("it")
    now = db.now_iso()
    type_key = taxonomy.resolve_type(payload.type)
    slot = taxonomy.slot_of(type_key)

    # ★ 裤长只对裤装有意义。模型/接口都可能给衬衫塞一个长度
    #   （「九分袖」词面就命中长度词），所以**入库前**再过一道类型闸门。
    #   这里丢弃而不是报错：可选字段不该挡住整条录入。
    length_val = payload.length if taxonomy.supports_length(type_key) else None

    # 打标状态判定：只要用户提供了任何受控属性，就视为已打标完毕（来源 = manual）。
    # 只看 type != "other" 是不够的 —— 用户填了颜色/材质/风格但漏选品类很常见，
    # 那种记录会被永久计为 pending，把 /health 的 pending_tagging 顶上天。
    has_attrs = bool(
        type_key != "other"
        or payload.colors
        or payload.primary_color
        or payload.material
        or payload.fit
        or payload.length
        or payload.style
        or payload.season
        or payload.formality
        or payload.scene
    )
    tagging_status = "tagged" if has_attrs else "pending"
    tagged_by = "manual" if has_attrs else None
    tagged_at = now if has_attrs else None

    with db.conn() as c:
        c.execute(
            """
            INSERT INTO clothing_items (
                id, user_id, image_path, thumbnail_path, image_hash,
                type, subtype, slot,
                colors, primary_color, pattern, material, fit, length,
                style, season, formality, scene, tags,
                name, brand, purchase_date, purchase_price, favorite, notes,
                tagging_status, tagged_by, tagged_at,
                is_archived, created_at, updated_at
            ) VALUES (
                ?,?,?,?,?,
                ?,?,?,
                ?,?,?,?,?,?,
                ?,?,?,?,?,
                ?,?,?,?,?,?,
                ?,?,?,
                0,?,?
            )
            """,
            (
                tid, user_id,
                payload.image_path, None, payload.image_hash,
                type_key, payload.subtype, slot,
                json.dumps(payload.colors, ensure_ascii=False),
                payload.primary_color, payload.pattern, payload.material, payload.fit,
                length_val,
                json.dumps(payload.style, ensure_ascii=False),
                json.dumps(payload.season, ensure_ascii=False),
                payload.formality,
                json.dumps(payload.scene, ensure_ascii=False),
                json.dumps(payload.tags, ensure_ascii=False),
                payload.name, payload.brand,
                payload.purchase_date, payload.purchase_price,
                1 if payload.favorite else 0, payload.notes,
                tagging_status, tagged_by, tagged_at,
                now, now,
            ),
        )
        row = c.execute("SELECT * FROM clothing_items WHERE id=?", (tid,)).fetchone()
    return to_out(row)


def create_many(user_id: str, payloads: list[ItemCreate]) -> dict:
    """批量创建。**一件失败不影响其余** —— 跳过并回报清单。

    失败语义已定稿（2026-10-03）：**跳过并回报**，不是整体回滚。
    理由：导入是补数据的体力活，一件撞了不该让另外 19 件白干；
    但必须**逐件**回报，否则会变成「以为导完了，其实少了几件」。

    两类失败分开计，因为处理方式不同：
    - `skipped` —— 唯一约束冲突（重复的图片指纹）。**预期之内**，不用管
    - `failed`  —— 其它异常。**需要用户看一眼**，数据本身有问题
    """
    outcomes: list[dict] = []
    created = skipped = failed = 0

    for index, payload in enumerate(payloads):
        try:
            item = create(user_id, payload)
        except sqlite3.IntegrityError as exc:
            skipped += 1
            outcomes.append(
                {
                    "index": index,
                    "name": payload.name,
                    "status": "skipped",
                    "item_id": None,
                    "reason": f"图片指纹重复，这件可能已经录过：{exc}",
                }
            )
        except Exception as exc:  # noqa: BLE001 —— 逐件兜底，一条坏数据不该毁掉整批
            failed += 1
            outcomes.append(
                {
                    "index": index,
                    "name": payload.name,
                    "status": "failed",
                    "item_id": None,
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            )
        else:
            created += 1
            outcomes.append(
                {
                    "index": index,
                    "name": payload.name,
                    "status": "created",
                    "item_id": item["id"],
                    "reason": None,
                }
            )

    return {
        "total": len(payloads),
        "created": created,
        "skipped": skipped,
        "failed": failed,
        "outcomes": outcomes,
    }


def auto_name(item: dict) -> str:
    """按「颜色 + 材质 + 品类」拼一个能认出来的名字，如 `白色棉T恤`。

    **只用于用户没填名字的情况**（纯图录入最常见：只有照片，没有商品文案）。
    用户拍板选的是"自动起名但可以改"，所以这是**填空**，不是覆盖。

    拼不出足够信息就返回空串，**不要**只返回品类名 ——
    前端 `view-wardrobe.js` 本来就 `item.name || item.type_label`，
    返回一个只有品类名的名字等于制造重复值（一排"T恤"）。

    参数是 `items.get()` 的结果（过 `to_out()`，多值字段已是列表）。
    """
    type_label = str(item.get("type_label") or "").strip()
    if not type_label or type_label == "其他":
        return ""

    parts: list[str] = []

    # 颜色：优先 `primary_color`，退回第一个 `colors`。
    color_key = item.get("primary_color")
    if not color_key:
        colors = item.get("colors") or []
        color_key = colors[0] if colors else None
    if color_key:
        label = taxonomy.COLORS.get(str(color_key))
        if label:
            # 词表里是「白 / 米白 / 卡其」这类单字或双字，补个「色」更像人话
            parts.append(label if label.endswith("色") else f"{label}色")

    material = taxonomy.MATERIALS.get(str(item.get("material") or ""))
    if material:
        parts.append(str(material))

    if not parts:
        return ""
    return "".join(parts) + type_label


def get(item_id: str, user_id: Optional[str] = None) -> Optional[dict]:
    sql = "SELECT * FROM clothing_items WHERE id=?"
    args: list = [item_id]
    if user_id:
        sql += " AND user_id=?"
        args.append(user_id)
    with db.conn() as c:
        row = c.execute(sql, args).fetchone()
    return to_out(row) if row else None


def update(item_id: str, payload: ItemUpdate, user_id: Optional[str] = None) -> Optional[dict]:
    current = get(item_id, user_id)
    if current is None:
        return None

    # 不用 exclude_none：允许传 null 来**清空可空字段**（改错的标签要能复原）。
    # 但不可空列和 JSON 列要单独处理，见下。
    data = payload.model_dump(exclude_unset=True)

    # 不可空列遇 None 直接丢掉，不能写进库
    for k in ("type",):
        if k in data and data[k] is None:
            data.pop(k)
    # JSON 列传 null 视为"清空"，落成 [] / {}
    for f in JSON_LIST_FIELDS:
        if f in data and data[f] is None:
            data[f] = []
    for f in JSON_DICT_FIELDS:
        if f in data and data[f] is None:
            data[f] = {}

    if not data:
        return current

    sets: list[str] = []
    args: list[Any] = []
    touched_tag = False

    # 类型闸门要用**更新后的**品类：本次可能同时改 type 和 length。
    effective_type = data.get("type") or current.get("type")

    for key, value in data.items():
        if key not in WRITABLE_COLUMNS:
            continue
        # 裤长只对裤装有意义（见 `create` 里的同款闸门）。
        # ⚠️ 放在这里而不是交给前端：接口是公开的，模型打标也会走这条路。
        if key == "length" and value not in (None, "") and not taxonomy.supports_length(effective_type):
            continue
        if key in JSON_LIST_FIELDS or key in JSON_DICT_FIELDS:
            value = json.dumps(value, ensure_ascii=False)
            if value not in ("[]", "{}"):
                touched_tag = True
        elif key in ("type", "primary_color", "fit", "length", "material", "pattern", "formality"):
            # 只有写入了实际内容才算"打标"；清空属性不算
            if value not in (None, ""):
                touched_tag = True
        elif key in ("is_archived", "favorite"):
            value = 1 if value else 0
        sets.append(f"{key}=?")
        args.append(value)

    if "type" in data:
        # type 变了要同步 slot
        sets.append("slot=?")
        args.append(taxonomy.slot_of(data["type"]))

    # 打标状态与来源：
    # - type 落 other 时不标 tagged（用户只是清空了分类，不是"打标完成"）
    # - **来源保留原值**：用户修正 AI 打错的标时，tagged_by 必须仍然是 ai，
    #   否则就再也分不清"这条数据是 AI 打的还是人手填的"
    should_mark = touched_tag and data.get("type") != "other"
    if should_mark:
        sets.append("tagging_status=?")
        args.append("tagged")
        sets.append("tagged_by=?")
        args.append(current.get("tagged_by") or "manual")
        sets.append("tagged_at=?")
        args.append(db.now_iso())

    sets.append("updated_at=?")
    args.append(db.now_iso())
    args.append(item_id)

    where = "WHERE id=?"
    if user_id:
        where = "WHERE id=? AND user_id=?"
        args.append(user_id)

    with db.conn() as c:
        c.execute(f"UPDATE clothing_items SET {', '.join(sets)} {where}", args)
    return get(item_id, user_id)


def list_items(
    user_id: str,
    *,
    slot: Optional[str] = None,
    type_key: Optional[str] = None,
    tagging_status: Optional[str] = None,
    favorite: Optional[bool] = None,
    include_archived: bool = False,
    search: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    where = ["user_id=?"]
    args: list[Any] = [user_id]
    if not include_archived:
        where.append("is_archived=0")
    if slot:
        where.append("slot=?")
        args.append(slot)
    if type_key:
        where.append("type=?")
        args.append(taxonomy.resolve_type(type_key))
    if tagging_status:
        where.append("tagging_status=?")
        args.append(tagging_status)
    if favorite is not None:
        where.append("favorite=?")
        args.append(1 if favorite else 0)
    if search:
        # 转义 LIKE 通配符，否则搜 "%" 会命中全部、搜 "50%" 会退化成搜 "50"
        esc = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where.append(
            "(name LIKE ? ESCAPE '\\' OR brand LIKE ? ESCAPE '\\' OR notes LIKE ? ESCAPE '\\')"
        )
        like = f"%{esc}%"
        args.extend([like, like, like])

    clause = " AND ".join(where)
    page = max(1, page)
    page_size = max(1, min(200, page_size))
    offset = (page - 1) * page_size

    with db.conn() as c:
        total = c.execute(
            f"SELECT COUNT(*) AS n FROM clothing_items WHERE {clause}", args
        ).fetchone()["n"]
        rows = c.execute(
            f"SELECT * FROM clothing_items WHERE {clause} "
            "ORDER BY favorite DESC, created_at DESC LIMIT ? OFFSET ?",
            [*args, page_size, offset],
        ).fetchall()
    return {
        "items": [to_out(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def iter_all_items(user_id: str, include_archived: bool = False) -> list[dict]:
    """取全部单品，**不分页**。

    缺口分析与组合数必须看整个衣橱，绝不能用 list_items ——
    后者会把 page_size 钳到 200，衣橱超过 200 件时会计出一个静默偏小的
    组合数，而且不报任何错。
    """
    where = ["user_id=?"]
    args: list[Any] = [user_id]
    if not include_archived:
        where.append("is_archived=0")
    with db.conn() as c:
        rows = c.execute(
            f"SELECT * FROM clothing_items WHERE {' AND '.join(where)} "
            "ORDER BY created_at DESC",
            args,
        ).fetchall()
    return [to_out(r) for r in rows]


def delete(item_id: str, user_id: Optional[str] = None) -> bool:
    """删除单品：先删数据库那一行，成功后再清磁盘上的图。

    **顺序不能反**（2026-10-22）：先删文件再删行，万一 SQL 失败（越权 / 约束），
    用户会看到「删除失败」但图片已经没了 —— 数据库还指着它，整件单品变成坏图。
    所以必须等 `with db.conn()` 提交完，确认真的删掉了一行，才动文件。

    图片清理是**尽力而为**：`images.remove_item_images` 不抛异常，删不掉只记日志。
    返回 `False` 表示这一行不存在（或不属于这个用户），此时**不碰任何文件** ——
    越权删别人的单品不该顺手把别人的图也删了。
    """
    with db.conn() as c:
        if user_id:
            cur = c.execute(
                "DELETE FROM clothing_items WHERE id=? AND user_id=?", (item_id, user_id)
            )
        else:
            cur = c.execute("DELETE FROM clothing_items WHERE id=?", (item_id,))
        deleted = cur.rowcount > 0

    if deleted:
        # 这里已经在 with 之外 —— 事务提交过了，删图不会反过来影响数据库。
        # ⚠️ 兜一层 Exception：`remove_item_images` 承诺只吞 OSError，但那条
        #   契约靠的是"它碰巧只调 OSError 相关的 API"。行已经删掉了，
        #   这里再抛任何东西出去都是 500 + 用户以为没删掉，不值当。
        try:
            images.remove_item_images(item_id)
        except Exception:  # noqa: BLE001 — 见上
            logger.exception("删除单品 %s 后清图片失败（记录已删，图可能残留）", item_id)
    return deleted
