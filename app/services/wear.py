"""穿着记录的业务逻辑（原在 services/items.py，按职责拆出）。

与 items.py 的分工：
    items.py  ——  衣橱里"有什么"（单品的增删改查）
    wear.py   ——  "哪天穿了什么"（穿着事件 + 单品穿着历史）

穿着记录的真相源是 `wear_events`（outfit 级），`item_history` 只是它的展开，
这是为了补上同类实现的一个短板：它们的穿着记录只有 item 级、整套标识可空，
导致「某天穿了哪一整套」重建不出来。
"""

from __future__ import annotations

import json
from typing import Any

from .. import db
from ..models import WearLogIn


def _as_list(raw: Any) -> list:
    """DB 里的 JSON 文本 -> list（容错：任何异常都降级成空列表）。

    刻意在这里自带一份，而不是 from .items import _loads_list：
    那是 items.py 的私有函数，跨模块引私有名会造出隐式耦合 ——
    将来 items.py 改了它，本模块会**静默**坏掉。
    代价是逻辑重复 8 行，可接受。
    """
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else []
    except Exception:
        return []


def log_wear(user_id: str, payload: WearLogIn) -> dict:
    """记录一次穿着：写一条 wear_event（整套真相）+ 每个单品一条 item_history。

    三处必经校验（缺一个就会写出脏数据）：
    - item_ids 必须都属于当前用户（防自造 id）
    - outfit_id 若给了，必须真实存在且属于当前用户 —— 否则外键会抛
      IntegrityError，未捕获就是 500
    """
    worn_at = (payload.worn_at or db.now_iso()[:10])[:10]
    we_id = db.new_id("we")
    now = db.now_iso()
    # 去重保序：同一条记录里重复填同一件衣服（`[a, a]`）原本会插两条
    # `item_history`、把 `wear_count` 加两次 —— 用户手滑或前端重复提交都可能造成。
    item_ids = list(dict.fromkeys(payload.item_ids))
    source = payload.source

    with db.conn() as c:
        # 1) 校验单品归属
        ph = ",".join("?" * len(item_ids))
        rows = c.execute(
            f"SELECT id FROM clothing_items WHERE user_id=? AND id IN ({ph})",
            [user_id, *item_ids],
        ).fetchall()
        valid = {r["id"] for r in rows}
        missing = [i for i in item_ids if i not in valid]
        if missing:
            raise ValueError(f"这些单品不属于当前用户或不存在：{missing}")

        # 2) 校验 outfit_id 归属（不做这步 → 外键 IntegrityError → 500）
        outfit_id = payload.outfit_id
        if outfit_id:
            hit = c.execute(
                "SELECT id FROM outfits WHERE id=? AND user_id=?", (outfit_id, user_id)
            ).fetchone()
            if not hit:
                raise ValueError(f"搭配方案不存在或不属于当前用户：{outfit_id}")

        c.execute(
            """
            INSERT INTO wear_events
                (id,user_id,worn_at,outfit_id,item_ids,occasion,weather,source,notes,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                we_id, user_id, worn_at, outfit_id,
                json.dumps(item_ids, ensure_ascii=False),
                payload.occasion, payload.weather, source,
                payload.notes, now,
            ),
        )
        for iid in item_ids:
            c.execute(
                """
                INSERT INTO item_history
                    (id,item_id,wear_event_id,outfit_id,worn_at,occasion,notes,created_at)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (db.new_id("ih"), iid, we_id, outfit_id, worn_at,
                 payload.occasion, payload.notes, now),
            )
            # 采纳计数：只有"采纳了 AI 给的方案"才记，
            # 否则采纳率（设计文档 §7 的一级指标）算不出来
            if outfit_id:
                c.execute(
                    """
                    UPDATE clothing_items
                       SET wear_count = wear_count + 1,
                           acceptance_count = acceptance_count + 1,
                           last_worn_at = CASE
                             WHEN last_worn_at IS NULL OR last_worn_at < ? THEN ?
                             ELSE last_worn_at END,
                           updated_at = ?
                     WHERE id = ?
                    """,
                    (worn_at, worn_at, now, iid),
                )
            else:
                c.execute(
                    """
                    UPDATE clothing_items
                       SET wear_count = wear_count + 1,
                           last_worn_at = CASE
                             WHEN last_worn_at IS NULL OR last_worn_at < ? THEN ?
                             ELSE last_worn_at END,
                           updated_at = ?
                     WHERE id = ?
                    """,
                    (worn_at, worn_at, now, iid),
                )

    return {
        "id": we_id,
        "worn_at": worn_at,
        "item_ids": item_ids,
        "outfit_id": outfit_id,
        "occasion": payload.occasion,
        "weather": payload.weather,
        "source": source,
    }


def list_wear_events(user_id: str, limit: int = 50) -> list[dict]:
    with db.conn() as c:
        rows = c.execute(
            "SELECT * FROM wear_events WHERE user_id=? ORDER BY worn_at DESC LIMIT ?",
            (user_id, max(1, min(500, limit))),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["item_ids"] = _as_list(d.get("item_ids"))
        out.append(d)
    return out
