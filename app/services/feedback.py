"""反馈闭环：写反馈 → 更新搭配状态 → **把反馈变成偏好证据**。

**为什么最后那步是重点**
如果反馈只写进 `feedback` 表，它就只是历史记录 —— AI 不会因为你说过
"不喜欢这条裤子"而改变下次推荐。这个模块的职责是把一次反馈翻译成
若干条**带极性的偏好证据**，交给 `preference.record()` 去更新记忆。
闭环要真正闭合，靠的就是这一步。

极性映射（`k` = 等效样本量，取值见 `preference` 模块）：

| 反馈 | 极性 r | k | 理由 |
|---|---|---|---|
| `like` | +1.0 | 2.0 | 用户明确表态，最强信号 |
| `dislike` | -1.0 | 2.0 | 同上 |
| `swap`（换一套） | -0.5 | 1.0 | 只说明「这套不合适」，不等价于「不喜欢里面每一件」 —— 所以不给满格负分 |

**已知未利用的信号**：`rating` / `comfort_rating` / `style_rating`（1..5）。
它们比 like/dislike 更细，但当前只用 `feedback_type` 定极性，星级只入库。
更细的利用（用星级调节极性幅度）**留给 P4** —— 现在没有真实数据来判断
"4 星算多正面"，硬猜一个系数不如先不做。这是有意为之，不是遗漏。

⚠️ **连接纪律**：`preference.record()` 自己会开连接，所以它**必须**在
本模块的 `with db.conn()` 块**之外**调用 —— SQLite 写事务不重入，
嵌套开连接会撞 `database is locked`。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Optional

from .. import db
from . import preference

#: `feedback_type` → `(极性, 等效样本量)`
POLARITY: dict[str, tuple[float, float]] = {
    "like": (1.0, preference.K_EXPLICIT),
    "dislike": (-1.0, preference.K_EXPLICIT),
    "swap": (-0.5, preference.K_IMPLICIT),
}

#: 反馈导致的搭配状态流转
STATUS_AFTER: dict[str, str] = {
    "like": "accepted",
    "dislike": "rejected",
    "swap": "swapped",
}


def _as_date(when: Any) -> date:
    if isinstance(when, datetime):
        return when.date()
    if isinstance(when, str):
        try:
            return datetime.fromisoformat(when).date()
        except ValueError:
            pass
    return date.today()


def _load_items(c, user_id: str, item_ids: list[str]) -> list[dict]:
    """按 id 取单品。**带 user_id 过滤**（归属校验，同 `log_wear` 的口径）。

    ⚠️ 必须过一遍 `to_out()`。原始行里的 `colors` / `style` / `season` / `scene`
    是 **JSON 字符串**，而 `preference.tags_of_item` 期望的是 list。
    直接把原始行喂进去，`for color in '["white"]'` 会**逐字符**迭代，
    往记忆里写进 `color:"["`、`color:"w"` 这种垃圾 —— **而接口照样返回 200**。

    （这个 bug 单测抓不到：单测手上是 `items.create()` 的返回值，已经 `to_out` 过了。
    只有"从数据库读原始行"的这条路径才会踩到，所以是集成时才暴露的。）
    """
    if not item_ids:
        return []
    from .items import to_out

    placeholders = ",".join("?" * len(item_ids))
    rows = c.execute(
        f"SELECT * FROM clothing_items WHERE user_id=? AND id IN ({placeholders})",
        [user_id, *item_ids],
    ).fetchall()
    return [to_out(row) for row in rows]


def resolve_items(
    user_id: str,
    *,
    outfit_id: Optional[str] = None,
    wear_event_id: Optional[str] = None,
    item_ids: Optional[list[str]] = None,
) -> tuple[list[dict], Optional[str]]:
    """确定「这次反馈针对的是哪几件单品」，顺带取回场合。

    三个来源优先级：`outfit_id` → `wear_event_id` → 直接给的 `item_ids`。
    返回 `(单品行列表, 场合)`；场合取自 AI 方案或穿着事件，用于决定偏好存进哪个桶。
    """
    scene: Optional[str] = None
    resolved: list[str] = []

    with db.conn() as c:
        if outfit_id:
            row = c.execute(
                "SELECT item_ids, scene FROM outfits WHERE id=? AND user_id=?",
                (outfit_id, user_id),
            ).fetchone()
            if row:
                resolved = _json_ids(row["item_ids"])
                scene = row["scene"]
        elif wear_event_id:
            row = c.execute(
                "SELECT item_ids, occasion FROM wear_events WHERE id=? AND user_id=?",
                (wear_event_id, user_id),
            ).fetchone()
            if row:
                resolved = _json_ids(row["item_ids"])
                scene = row["occasion"]

        if not resolved and item_ids:
            resolved = [str(i) for i in item_ids]

        items = _load_items(c, user_id, resolved)

    return items, scene


def _json_ids(raw: Any) -> list[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v]
    return []


def submit(
    user_id: str,
    *,
    feedback_type: str,
    outfit_id: Optional[str] = None,
    wear_event_id: Optional[str] = None,
    item_ids: Optional[list[str]] = None,
    rating: Optional[int] = None,
    comfort_rating: Optional[int] = None,
    style_rating: Optional[int] = None,
    comment: Optional[str] = None,
    when: Any = None,
) -> dict:
    """记一次反馈，并更新偏好记忆。

    返回：
        `{"feedback": {...}, "updated_tags": [...], "recorded": N, "duplicate": bool}`

    幂等性：**同一「搭配 + 反馈类型」重复提交会被识别为重复**，直接返回既有结果、
    不重复累加证据（防网络重试污染）。但用户改主意（先 `like` 后 `dislike`）
    是不同类型，会正常生效。
    """
    if feedback_type not in POLARITY:
        raise ValueError(f"未知的 feedback_type：{feedback_type}（只接受 {sorted(POLARITY)}）")

    items, scene = resolve_items(
        user_id, outfit_id=outfit_id, wear_event_id=wear_event_id, item_ids=item_ids
    )
    if not items:
        raise ValueError("这次反馈没有关联到任何真实单品，无法学习偏好")

    now = db.now_iso()
    polarity, k = POLARITY[feedback_type]
    resolved_ids = [i["id"] for i in items]

    # ---- 写反馈 + 更新搭配状态（一个连接，别把 preference.record 塞进来）----
    with db.conn() as c:
        if outfit_id:
            dup = c.execute(
                "SELECT * FROM feedback WHERE user_id=? AND outfit_id=? AND feedback_type=?",
                (user_id, outfit_id, feedback_type),
            ).fetchone()
            if dup:
                return {
                    "feedback": dict(dup),
                    "updated_tags": [],
                    "recorded": 0,
                    "duplicate": True,
                }

        fid = db.new_id("fb")
        c.execute(
            """
            INSERT INTO feedback
                (id,user_id,outfit_id,wear_event_id,item_ids,feedback_type,
                 rating,comfort_rating,style_rating,comment,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                fid, user_id, outfit_id, wear_event_id,
                json.dumps(resolved_ids, ensure_ascii=False), feedback_type,
                rating, comfort_rating, style_rating, comment, now,
            ),
        )
        if outfit_id:
            c.execute(
                "UPDATE outfits SET status=? WHERE id=? AND user_id=?",
                (STATUS_AFTER[feedback_type], outfit_id, user_id),
            )
        fb_row = c.execute("SELECT * FROM feedback WHERE id=?", (fid,)).fetchone()

    # ---- 翻译成偏好证据（**必须在上面那个 with 之外**）----
    context = preference.context_key(preference.season_of_day(_as_date(when)), scene)
    updated: list[dict] = []
    for item in items:
        for tag_type, tag_value in preference.tags_of_item(item):
            updated.append(
                preference.record(
                    user_id, tag_type, tag_value, polarity,
                    k=k, context=context, when=when,
                )
            )

    return {
        "feedback": dict(fb_row),
        "updated_tags": sorted(
            updated,
            key=lambda d: (d["confidence"], abs(d["decayed_score"])),
            reverse=True,
        ),
        "recorded": len(updated),
        "duplicate": False,
        "context_key": context,
    }
