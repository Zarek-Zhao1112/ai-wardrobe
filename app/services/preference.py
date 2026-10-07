"""偏好记忆 —— 条件化分桶 + 指数衰减 + 置信度 + 分层回退 + 冷启动降级。

**为什么不能是一段散文（推翻了 v0.1 §14 的写法）**
v0.1 把记忆写成"用户喜欢：宽松版型 / 黑灰色"。散文算不了权重、衰减不了、
也给不出置信度 —— 而 v0.1 §5.3 又说"降低该颜色的权重"，两个主张自相矛盾。
这里把它变成可计算的东西。

**为什么必须条件化（本模块存在的唯一理由）**
"喜欢短袖"这条记忆，如果因为"冬天三个月没穿短袖"就被衰减掉，那系统学到的
不是偏好变化，而是**季节变化** —— 冬天一过它就失忆了。
所以记忆按 `{季节}|{场合}` 分桶存：季节漂移被分桶隔离，
衰减只负责"反映最近口味"这一件事。两者职责分开，互不打架。

---

## 分桶口径（必须与 taxonomy 保持一致，否则组合计算和偏好计算会分裂）

季节：4 个日历季 → 3 个桶，外加 `all`（无季节上下文）
    spring / autumn  → `transition`
    summer          → `summer`
    winter          → `winter`

场合：taxonomy 有 9 个 scene，设计文档只给了 5 个桶名。
    映射见 `SCENE_BUCKETS`。多出来的 date / party / travel / school 归入 `daily`
    —— 2026-10-03 定稿：跟着设计文档走，桶内样本才够；
    将来若数据表明需要拆，改这一张表即可（分层回退会兜住样本不足的情况）。

## 两个刻意的设计选择（规格里没写，这里明说）

1. **分层回退是"逐 tag"而非"逐桶"**。设计文档原话是"取首个 `evidence_count ≥ 5`
   的桶"。若按桶整体判断，某个桶够 5 条但恰好都是颜色类时，`style:casual`
   就查不到值了。改为**对每个 (tag_type, tag_value) 各自沿回退链找**，
   这样黑裤子在细桶、休闲风在 global，各取所需。
2. **链上没有够门槛的桶时，取证据最多的那个，而不是丢弃**。丢弃等于"没这个偏好"，
   而实际情况是"有一点偏好但证据不足" —— 后者应该返回，由 `confidence` 去降权。
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from typing import Any, Final, Iterable, Optional

from .. import db

# ---------------------------------------------------------------------------
# 常量 —— 全部来自设计文档 §5。改这里之前先改文档。
# ---------------------------------------------------------------------------

#: 衰减半衰期（天）。
#:
#: 需求是"90 天后反映最近口味"，取「90 天后权重降到 1/4」为设计目标：
#:     0.5 ** (90 / H) = 0.25  →  90/H = log(0.25)/log(0.5) = 2  →  H = 45（精确解）
#: 边界校验：
#:     H = 14d  → 90 天后只剩 1.2%，一周就忘光，记忆抖动
#:     H = 180d → 90 天后仍有 70.7%，等于没衰减
#: ⚠️ 季节漂移已由 `context_key` 分桶隔离，所以 H **不需要**随季节调整。
HALF_LIFE_DAYS: Final[float] = 45.0

#: 等效样本量 k：一次反馈值多少条证据。
K_EXPLICIT: Final[float] = 2.0   # 显式点"喜欢 / 不喜欢"
K_IMPLICIT: Final[float] = 1.0   # 隐式采纳（用了推荐的搭配但没明说）
K_WEAK: Final[float] = 0.3       # 弱信号（推荐了但用户直接划走）

#: 分层回退门槛：桶内证据少于此值就退到更粗的桶。
MIN_EVIDENCE: Final[int] = 5

#: 冷启动分档。
COLD_START_N: Final[int] = 5     # n < 5 → 不参与排序
WARM_N: Final[int] = 20          # n ≥ 20 → 探索率收紧
EPS_WARM: Final[float] = 0.2
EPS_COLD: Final[float] = 0.05

#: n < COLD_START_N 时置信度的封顶值。
MIN_CONF_CAP: Final[float] = 0.3

#: 季节归并表。只含 summer / winter 的是季节限定；含 spring 或 autumn 的算过渡季。
SEASON_BUCKETS: Final[dict[str, str]] = {
    "spring": "transition",
    "autumn": "transition",
    "summer": "summer",
    "winter": "winter",
}

#: 场合归并表：taxonomy 的 9 个 scene → 设计文档的 5 个桶。
SCENE_BUCKETS: Final[dict[str, str]] = {
    "daily": "daily",
    "date": "daily",
    "party": "daily",
    "travel": "daily",
    "school": "daily",
    "work": "work",
    "sport": "sport",
    "home": "home",
    "formal": "formal",
}

#: 多个场合标签落到不同桶时的取舍顺序。`daily` 是兜底桶，所以排最后。
SCENE_PRIORITY: Final[tuple[str, ...]] = ("formal", "work", "sport", "home", "daily")

#: 全部合法的桶名（供校验与测试）。
SEASON_BUCKET_NAMES: Final[frozenset] = frozenset({"summer", "winter", "transition", "all"})
SCENE_BUCKET_NAMES: Final[frozenset] = frozenset({"daily", "work", "sport", "home", "formal", "all"})


# ---------------------------------------------------------------------------
# 时间
# ---------------------------------------------------------------------------

def _now() -> datetime:
    """与 `db.now_iso()` 同源的当前时间（本地时区、带偏移）。"""
    return datetime.now(timezone.utc).astimezone()


def _parse_iso(value: Any) -> Optional[datetime]:
    """解析 ISO 时间串。解析不了返回 None，由调用方决定怎么兜底。"""
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def elapsed_days(last_iso: Any, now: Optional[datetime] = None) -> float:
    """距上次更新过了几天。

    解析失败或时间倒流（时钟被改）→ 返回 0.0，也就是**不衰减**。
    宁可少衰减也不要凭空扣分 —— 衰减过头会让好恶无声消失。
    """
    past = _parse_iso(last_iso)
    if past is None:
        return 0.0
    current = now or _now()
    # **双向**归一：哪边缺时区就补上另一边的。
    # 只处理一个方向会漏 —— `record(when="2026-10-01")` 传进来的是无时区
    # datetime，而库里 `last_updated` 是带时区的 `db.now_iso()`，
    # 直接相减抛 `TypeError: can't subtract offset-naive and offset-aware datetimes`。
    if past.tzinfo is None and current.tzinfo is not None:
        past = past.replace(tzinfo=current.tzinfo)
    elif current.tzinfo is None and past.tzinfo is not None:
        current = current.replace(tzinfo=past.tzinfo)
    delta = (current - past).total_seconds() / 86400.0
    return delta if delta > 0 else 0.0


# ---------------------------------------------------------------------------
# 分桶
# ---------------------------------------------------------------------------

def season_bucket(season: Any) -> str:
    """日历季 → 记忆桶。未知 / 缺省 → `all`（无季节上下文）。

    **幂等**：喂进来已经是桶名（如 `transition`）就原样返回。
    少了这一步，`context_key(season_of_day(d), scene)` 这种「先归并、再拼 key」
    的写法会把 `autumn → transition → all` 连打两次，
    结果**所有反馈都挤进 `global` 桶** —— 分桶彻底失效，而且不报任何错。
    """
    if not season:
        return "all"
    text = str(season).strip().lower()
    if text in SEASON_BUCKET_NAMES:
        return text
    return SEASON_BUCKETS.get(text, "all")


def scene_bucket(scenes: Any) -> str:
    """场合标签（一个或多个）→ 记忆桶。

    多个标签落到不同桶时取 `SCENE_PRIORITY` 里靠前的那个，
    理由是 `daily` 是兜底桶、其余桶更专用。
    """
    if not scenes:
        return "all"
    seq = [scenes] if isinstance(scenes, str) else list(scenes)
    buckets = set()
    for s in seq:
        if not s:
            continue
        text = str(s).strip().lower()
        # 幂等：已经是桶名就直接用（理由同 `season_bucket`）
        b = text if text in SCENE_BUCKET_NAMES else SCENE_BUCKETS.get(text)
        if b:
            buckets.add(b)
    if not buckets:
        return "all"
    for name in SCENE_PRIORITY:
        if name in buckets:
            return name
    return "all"


def context_key(season: Any = None, scene: Any = None) -> str:
    """拼出记忆桶的 key。两级都缺失时退化成 `global`。"""
    s = season_bucket(season)
    c = scene_bucket(scene)
    if s == "all" and c == "all":
        return "global"
    return f"{s}|{c}"


def split_context_key(key: str) -> tuple[str, str]:
    """`'summer|daily'` → `('summer', 'daily')`；`'global'` → `('all','all')`。"""
    if not key or key == "global":
        return "all", "all"
    s, _, c = str(key).partition("|")
    return (s or "all"), (c or "all")


def fallback_chain(key: str) -> list[str]:
    """分层回退链：细桶 → 同龄粗桶 → 同场合粗桶 → 全局。

        'summer|daily' → ['summer|daily', 'summer|all', 'all|daily', 'global']
        'summer|all'   → ['summer|all', 'global']
        'global'       → ['global']

    存在意义：细桶样本天然稀少（单用户 + 分季分场合），
    链式回退让"证据不足"自动降级，而不是当成"没有偏好"。
    """
    s, c = split_context_key(key)
    out = [key]
    for cand in (f"{s}|all", f"all|{c}", "global"):
        if cand == "all|all":
            cand = "global"
        if cand not in out:
            out.append(cand)
    return out


# ---------------------------------------------------------------------------
# 衰减与打分
# ---------------------------------------------------------------------------

def decay_weight(dt_days: float, half_life: float = HALF_LIFE_DAYS) -> float:
    """指数半衰期权重 `w(Δt) = 0.5 ** (Δt / H)`。"""
    if dt_days <= 0:
        return 1.0
    if half_life <= 0:
        return 1.0
    return 0.5 ** (dt_days / half_life)


def update_score(
    score: float,
    n: float,
    r: float,
    k: float,
    dt_days: float,
    half_life: float = HALF_LIFE_DAYS,
) -> tuple[float, float]:
    """增量式更新（O(1)，不做全量重算）：返回 `(新 score, 新 n)`。

        w_old = 0.5 ** (Δt / H)
        score = (score * w_old * n + r * k) / (n * w_old + k)
        n     = n * w_old + k

    分子分母同构，所以 `n` 直接用分母即可。
    边界：n=0 时退化成 `score = r`（第一条证据就是全部）。
    """
    w = decay_weight(dt_days, half_life)
    denom = n * w + k
    if denom <= 0:
        return 0.0, 0.0
    new_score = (score * w * n + r * k) / denom
    # 夹到 [-1, 1]：公式本身不会越界，但防浮点误差累积
    return max(-1.0, min(1.0, new_score)), denom


def project(
    score: float,
    last_updated: Any,
    now: Optional[datetime] = None,
    half_life: float = HALF_LIFE_DAYS,
) -> float:
    """读时投影：把存下来的原始分按经过的时间衰减，得到"当前有效分"。

    写时已经用衰减后的基线更新过，这里再投影一次是为了对齐
    `PreferenceOut.score`（原始）与 `PreferenceOut.decayed_score`（有效）两个字段。
    """
    return score * decay_weight(elapsed_days(last_updated, now), half_life)


def confidence(n: float, decayed: float) -> float:
    """证据量 × 分数强度 的合成置信度：

        confidence = (n / (n + 5)) * (0.5 + 0.5 * |decayed_score|)

    - 证据越多越可信（`n/(n+5)`，n=5 时到 0.5、n=20 时到 0.8）
    - 分数越极端越可信（`0.5 + 0.5*|x|`，中性分数只有 0.5 系数）
    - `n < 5` 时**封顶**到 0.3：样本太少时不许显得自信
    """
    n = max(0.0, float(n))
    base = (n / (n + 5.0)) * (0.5 + 0.5 * min(abs(float(decayed)), 1.0))
    if n < COLD_START_N:
        return min(base, MIN_CONF_CAP)
    return base


def explore_epsilon(n: float) -> Optional[float]:
    """冷启动分档的探索率。返回 `None` 表示**该桶不参与排序**。

    - n < 5   → None（只靠 global + 初始风格兜底）
    - 5 ≤ n < 20 → 0.2（多探索，用户口味还没稳）
    - n ≥ 20  → 0.05（基本照偏好推，偶尔换个花样）

    依据：arXiv 2402.11627 实测「不加探索机制的 agent 无法完成个性化」。
    """
    if n < COLD_START_N:
        return None
    if n < WARM_N:
        return EPS_WARM
    return EPS_COLD


# ---------------------------------------------------------------------------
# 读写
# ---------------------------------------------------------------------------

def _to_out(row: sqlite3.Row, now: Optional[datetime] = None) -> dict:
    """数据库行 → `PreferenceOut` 兼容的 dict。"""
    n = float(row["evidence_count"])
    ds = project(float(row["score"]), row["last_updated"], now)
    return {
        "tag_type": row["tag_type"],
        "tag_value": row["tag_value"],
        "context_key": row["context_key"],
        "score": round(float(row["score"]), 4),
        "decayed_score": round(ds, 4),
        # 必须 round 而不是 int()：`n` 是等效样本量（k 可以是 0.3 这种小数），
        # 且每次更新都会乘一个略小于 1 的衰减因子，累积出 5.9999996 这样的值。
        # 直接 int() 会截成 5 —— 用户点了 3 次"喜欢"却看到 5 条证据。
        "evidence_count": int(round(float(row["evidence_count"]))),
        "confidence": round(confidence(n, ds), 4),
        "last_updated": row["last_updated"],
    }


def record(
    user_id: str,
    tag_type: str,
    tag_value: str,
    r: float,
    *,
    k: float = K_EXPLICIT,
    context: Any = None,
    when: Any = None,
    half_life: float = HALF_LIFE_DAYS,
) -> dict:
    """记一条证据，返回更新后的行。

    参数
    ----
    `r`       极性，`[-1, 1]`。`+1` = 喜欢，`-1` = 不喜欢，中间值表示弱倾向。
    `k`       等效样本量，用 `K_EXPLICIT` / `K_IMPLICIT` / `K_WEAK`。
    `context` 记忆桶。可以是 `context_key()` 拼好的字符串，
              也可以是 `(季节, 场合)` 二元组；不传 → `global`。
    `when`    这次反馈发生的时间（ISO 串或 datetime）；不传 → 现在。

    `pos_weight` / `neg_weight` 与 `evidence_count` 按同一衰减因子维护。
    ⚠️ 当前 `confidence()` **不读**这两列 —— 它们是给将来做"一致性度量"
    （正负证据之比）和界面展示（"你给过 12 次正面反馈"）预留的。
    """
    key = context if isinstance(context, str) and context else context_key(*(context or (None, None)))
    now_dt = _parse_iso(when) or _now()
    now_s = now_dt.isoformat(timespec="seconds")
    polarity = max(-1.0, min(1.0, float(r)))

    with db.conn() as c:
        row = c.execute(
            """
            SELECT * FROM preference_memory
             WHERE user_id=? AND tag_type=? AND tag_value=? AND context_key=?
            """,
            (user_id, tag_type, tag_value, key),
        ).fetchone()

        if row is None:
            new_score, new_n = update_score(0.0, 0.0, polarity, k, 0.0, half_life)
            pos = k if polarity > 0 else 0.0
            neg = k if polarity < 0 else 0.0
            pid = db.new_id("pm")
            c.execute(
                """
                INSERT INTO preference_memory
                    (id, user_id, tag_type, tag_value, context_key,
                     score, evidence_count, pos_weight, neg_weight,
                     confidence, last_updated, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    pid, user_id, tag_type, tag_value, key,
                    new_score, new_n, pos, neg,
                    confidence(new_n, new_score), now_s, now_s,
                ),
            )
        else:
            dt = elapsed_days(row["last_updated"], now_dt)
            decay = decay_weight(dt, half_life)
            new_score, new_n = update_score(
                float(row["score"]), float(row["evidence_count"]),
                polarity, k, dt, half_life,
            )
            pos = float(row["pos_weight"]) * decay + (k if polarity > 0 else 0.0)
            neg = float(row["neg_weight"]) * decay + (k if polarity < 0 else 0.0)
            pid = row["id"]
            c.execute(
                """
                UPDATE preference_memory
                   SET score=?, evidence_count=?, pos_weight=?, neg_weight=?,
                       confidence=?, last_updated=?, updated_at=?
                 WHERE id=?
                """,
                (
                    new_score, new_n, pos, neg,
                    confidence(new_n, new_score), now_s, now_s, pid,
                ),
            )

        fresh = c.execute("SELECT * FROM preference_memory WHERE id=?", (pid,)).fetchone()

    return _to_out(fresh, now_dt)


def record_many(
    user_id: str,
    tags: Iterable[tuple[str, str]],
    r: float,
    *,
    k: float = K_EXPLICIT,
    context: Any = None,
    when: Any = None,
) -> list[dict]:
    """一次反馈往往同时说明好几个偏好（颜色 + 版型 + 风格），批量记。

    逐条各开一次连接。单用户场景下一次反馈也就几个 tag，开销可忽略 ——
    换来的是复用 `record()` 不重复事务逻辑。
    """
    return [
        record(user_id, tt, tv, r, k=k, context=context, when=when)
        for tt, tv in tags
    ]


def query(
    user_id: str,
    *,
    context: Any = None,
    tag_type: Optional[str] = None,
    now: Any = None,
    half_life: float = HALF_LIFE_DAYS,
) -> list[dict]:
    """按上下文取偏好，走分层回退。返回按 `confidence` 降序的列表。

    ⚠️ 是**逐 tag 回退**：每个 `(tag_type, tag_value)` 各自沿回退链找第一个
    证据达标的桶。见模块 docstring 的说明。
    """
    key = context if isinstance(context, str) and context else context_key(*(context or (None, None)))
    chain = fallback_chain(key)
    now_dt = _parse_iso(now) or _now()

    sql = "SELECT * FROM preference_memory WHERE user_id=?"
    args: list[Any] = [user_id]
    if tag_type:
        sql += " AND tag_type=?"
        args.append(tag_type)

    with db.conn() as c:
        rows = c.execute(sql, args).fetchall()

    grouped: dict[tuple[str, str], dict[str, sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault((row["tag_type"], row["tag_value"]), {})[row["context_key"]] = row

    out: list[dict] = []
    for by_ctx in grouped.values():
        chosen: Optional[sqlite3.Row] = None
        for level in chain:
            row = by_ctx.get(level)
            if row is not None and float(row["evidence_count"]) >= MIN_EVIDENCE:
                chosen = row
                break
        if chosen is None:
            # 链上没有达标的桶 → 取证据最多的，交给 confidence 去降权，而不是丢掉
            candidates = [by_ctx[level] for level in chain if level in by_ctx]
            if candidates:
                chosen = max(candidates, key=lambda r: float(r["evidence_count"]))
        if chosen is not None:
            out.append(_to_out(chosen, now_dt))

    out.sort(key=lambda d: (d["confidence"], abs(d["decayed_score"])), reverse=True)
    return out


# ---------------------------------------------------------------------------
# 从单品提取偏好标签（供 feedback / tagging 调用）
# ---------------------------------------------------------------------------

def tags_of_item(item: dict) -> list[tuple[str, str]]:
    """把一个单品的属性摊平成 `(tag_type, tag_value)` 列表。

    `tag_type` 取值范围见 `schema.py` 的 `preference_memory` 注释：
    `color | style | occasion | weather | fit | material | pattern | slot`

    注意单品的 `scene` 归到 `occasion`（对用户而言"场合"就是"什么时候穿"），
    且**存归并后的桶名**而不是原始 9 个值 —— 否则记忆表里会同时出现
    `date` 和 `daily` 两套口径。
    """
    out: list[tuple[str, str]] = []

    def push(tag: str, value: Any) -> None:
        if value is None:
            return
        text = str(value).strip()
        if text:
            out.append((tag, text))

    for color in item.get("colors") or []:
        push("color", color)
    push("color", item.get("primary_color"))

    for style in item.get("style") or []:
        push("style", style)

    for field in ("fit", "material", "pattern", "formality", "slot"):
        push(field, item.get(field))

    for scene in item.get("scene") or []:
        bucket = scene_bucket(scene)
        if bucket != "all":
            out.append(("occasion", bucket))

    seen: set[tuple[str, str]] = set()
    deduped: list[tuple[str, str]] = []
    for tag in out:
        if tag not in seen:
            seen.add(tag)
            deduped.append(tag)
    return deduped


def season_of_day(d: Optional[date] = None) -> str:
    """某一天的**日历季节**（`spring` / `summer` / `autumn` / `winter`）。

    刻意返回日历季节而不是桶名：调用方拿到它通常紧接着就调 `context_key(...)`，
    而 `context_key` 自己会归并。这里先归并一次就成了**双重映射**
    （`autumn → transition → all`），所有反馈都会落进 `global` 桶。
    """
    from ..taxonomy import current_season

    return current_season(d)
