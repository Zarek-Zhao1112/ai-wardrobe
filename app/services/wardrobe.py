"""衣橱结构分析：季节化组合数 + 缺口分析 + 录入即时反馈。

**这一节在做的事**：替换一种常见但会漏判的旧实现 —— 硬编码白名单比例法：

    tops    = 计数(type in ["shirt","blouse","t-shirt","top"])
    bottoms = 计数(type in ["pants","jeans","skirt","shorts"])
    if tops/bottoms > 3: "你上装远多于下装，建议买裤子或裙子"

它的问题：sweater / hoodie / cardigan / jacket / vest 都不算"上装"，
dress / leggings / sweatpants 也不算"下装"。
一个「20 件毛衣 + 2 条裤子」的衣橱，它一个字都不会说。
下面 `_legacy_verdict()` 把这条规则原样保留，用来做对照。

**关于"瓶颈"的一个重要修正**：纯乘法模型下，「某槽位少一件」的相对跌幅
恒等于 1/该槽位件数，所以朴素瓶颈指标必然退化成"件数最少的槽位"。
因此这里不返回 bottleneck，改为返回：
  - `blocking_slots`：组合数为 0 时缺哪些槽位（真正的"断链"）
  - `slot_suggestions`：按「绝对增量 ΔC × 多样性折扣」排序的补货建议

**关于枚举搭配样例（P1 收尾修掉的那个坑）**：原实现是**深度优先**（上装在最外层循环），
于是前 N 个样本几乎全用同一件上装 —— 单品一多，**排在后面的单品（尤其是刚录入的）
永远进不了候选池**，而且不报错、只是推荐里再也见不到它。
现在改成**等距采样**（按扁平下标在整个组合空间上均匀取点），把空间铺开。
再配合 `per_slot`（每槽位按偏好只留前 K 件）实现规格 §3.3 的"先取 K 件、再枚举"。
"""

from __future__ import annotations

from typing import Any

from .. import taxonomy
from ..services import items as item_svc

# 旧实现的原始硬编码白名单，原样搬来做 A/B 对照
LEGACY_TOPS = ("shirt", "blouse", "t-shirt", "top")
LEGACY_BOTTOMS = ("pants", "jeans", "skirt", "shorts")


def _legacy_verdict(type_counter: dict) -> str:
    """跑一遍旧规则，看它会给出什么结论（用于说明它为什么漏判）。"""
    mapped = {
        "t-shirt": type_counter.get("tshirt", 0),
        "shirt": type_counter.get("shirt", 0),
        "blouse": type_counter.get("blouse", 0),
        "top": type_counter.get("tank", 0) + type_counter.get("vest", 0),
        "pants": type_counter.get("pants", 0),
        "jeans": type_counter.get("jeans", 0),
        "skirt": type_counter.get("skirt", 0),
        "shorts": type_counter.get("shorts", 0),
    }
    tops = sum(mapped[t] for t in LEGACY_TOPS)
    bottoms = sum(mapped[b] for b in LEGACY_BOTTOMS)
    if tops == 0 or bottoms == 0:
        return f"不上报（白名单只认出 上装 {tops} 件 / 下装 {bottoms} 件，达不到判断条件）"
    ratio = tops / bottoms
    if ratio > 3:
        return f"上报「上装远多于下装」（比例 {ratio:.1f}）"
    if ratio < 0.5:
        return f"上报「下装远多于上装」（比例 {ratio:.1f}）"
    return f"不上报（比例 {ratio:.1f}，在阈值内）"


# ---------------------------------------------------------------------------
# 取数
# ---------------------------------------------------------------------------
def _all_items(user_id: str) -> list[dict]:
    """整个衣橱。**必须用不分页的取数** —— 分页会把 page_size 钳到 200，
    衣橱超过 200 件时组合数会静默偏小而且不报错。"""
    return item_svc.iter_all_items(user_id)


def _pool(all_items: list[dict], season: str) -> list[dict]:
    """参与组合计算的单品：非归档 + 非家居套装 + 这季能穿。"""
    out = []
    for it in all_items:
        if it.get("is_archived"):
            continue
        if (it.get("type") or "other") in taxonomy.HOME_LIKE:
            continue
        if not taxonomy.usable_in(it, season):
            continue
        out.append(it)
    return out


def _by_slot(pool: list[dict]) -> dict:
    slots: dict = {s: [] for s in taxonomy.SLOTS}
    for it in pool:
        slots.setdefault(it["slot"], []).append(it)
    return slots


# ---------------------------------------------------------------------------
# 枚举搭配样例（纯本地，不经过模型 —— 所以 item_id 必然真实）
# ---------------------------------------------------------------------------
#: 推荐时每个槽位最多取多少件参与枚举（规格 §3.3 的「先取 K 件再枚举」）。
#: 12 是折中：12³ ≈ 1700 套候选，够铺开口味差异，又小到能**全量**打分、不必再采样。
PER_SLOT_TOP_K: int = 12


def _count_combos(slots: dict) -> int:
    """某个（可能已被 `per_slot` 截过的）槽位集合能搭出多少套。"""
    n_tops = len(slots.get("top", []))
    n_bottoms = len(slots.get("bottom", []))
    n_full = len(slots.get("full", []))
    n_shoes = len(slots.get("shoes", []))
    return (n_tops * n_bottoms + n_full) * n_shoes


def _cap_slots(slots: dict, per_slot: int, score_of) -> dict:
    """每槽位只留打分最高的 `per_slot` 件。

    ⚠️ **同分时"新的优先"是靠既有顺序白拿的**：`_all_items()` 走的是
    `ORDER BY created_at DESC`（新的在前），而 `_pool` / `_by_slot` 都保序；
    这里用**稳定排序**按分数降序，同分时留下的自然就是比较新的那几件。

    （曾经想显式按 `created_at` 再排一次 —— 没用：`db.now_iso()` 是**秒精度**，
    同一秒里录入的单品时间戳一模一样，排了也分不出先后。）

    为什么在意这个：冷启动阶段几乎所有单品得分都是 0，
    若同分时留下的是最旧的那批，刚录入的单品就永远进不了候选池 ——
    等于把原坑换个地方重生。
    """
    out: dict = {}
    for name, items in slots.items():
        if len(items) <= per_slot:
            out[name] = list(items)
        else:
            out[name] = sorted(items, key=score_of, reverse=True)[:per_slot]
    return out


def _enumerate(slots: dict, limit: int | None = None) -> list[dict]:
    """枚举搭配样例 —— **等距采样，不是深度优先**。

    组合空间是 `(上装 × 下装 + 连身) × 鞋` 的笛卡尔积。把它摊平成一维后
    每隔 `total / take` 取一个点再还原成 `(t, b, s)`，于是 O(take) 就能
    均匀铺满整个空间，**任何单品都会被轮到**。

    `limit=None` 表示全部取出（调用方须已用 `per_slot` 压住规模）。
    """
    tops = slots.get("top", [])
    bottoms = slots.get("bottom", [])
    shoes = slots.get("shoes", [])
    fulls = slots.get("full", [])

    if not shoes:
        return []                      # 没有鞋就搭不出完整的一套

    pairs = [(t, b) for t in tops for b in bottoms]
    split_span = len(pairs) * len(shoes)
    total = split_span + len(fulls) * len(shoes)
    if total == 0:
        return []

    take = total if limit is None else min(limit, total)
    out: list[dict] = []
    for k in range(take):
        idx = (k * total) // take      # 等距取点：最后一个点落在空间末尾
        if idx < split_span:
            t, b = pairs[idx // len(shoes)]
            s = shoes[idx % len(shoes)]
            out.append({"item_ids": [t["id"], b["id"], s["id"]], "kind": "split"})
        else:
            j = idx - split_span
            f = fulls[j // len(shoes)]
            s = shoes[j % len(shoes)]
            out.append({"item_ids": [f["id"], s["id"]], "kind": "full"})
    return out


def _attach_items(previews: list[dict], by_id: dict) -> list[dict]:
    out = []
    for p in previews:
        out.append(
            {
                "item_ids": p["item_ids"],
                "items": [by_id[i] for i in p["item_ids"] if i in by_id],
                "kind": p["kind"],
            }
        )
    return out


# ---------------------------------------------------------------------------
# 缺口分析
# ---------------------------------------------------------------------------
def analyze(user_id: str, season: str | None = None) -> dict:
    all_items = _all_items(user_id)
    stats = taxonomy.count_combinations(all_items, season)
    counts = stats["counts"]

    # 各 type 计数（供 legacy 对照）
    type_counter: dict = {}
    for it in all_items:
        k = it.get("type") or "other"
        type_counter[k] = type_counter.get(k, 0) + 1

    never_worn = [i for i in all_items if not i.get("wear_count")]

    suggestions: list[str] = []
    if not all_items:
        suggestions.append("衣橱是空的，先录几件衣服吧。")
    else:
        if not stats["min_viable"]:
            lacking = "、".join(stats["blocking_labels"]) or "基础单品"
            suggestions.append(
                f"当前这季搭不出任何一套（上装 {counts['top']} / 下装 {counts['bottom']} / "
                f"连身 {counts['full']} / 鞋 {counts['shoes']}）。"
                f"至少要有「一件上装 + 一件下装 + 一双鞋」，或「一件连身 + 一双鞋」。"
                f"缺：{lacking}。"
            )
            # 区分"真的没衣服"和"衣服都在别的季节"。
            # 换季时一个正常衣橱显示 0 套而不给解释，用户只会以为程序坏了。
            any_season = taxonomy.count_ignoring_season(all_items)["total_combinations"]
            if any_season > 0:
                suggestions.append(
                    f"补充：你的衣橱跨季节一共能搭出 {any_season} 套，"
                    f"只是没有「{stats['season_label']}」这一季可穿的组合。"
                    "如果不打算按季节区分，可以不填 season 字段（不填即视为四季通用）。"
                )
        top_sug = stats["suggestions"][0] if stats["suggestions"] else None
        if top_sug and top_sug["delta"] > 0:
            suggestions.append(
                f"补「{top_sug['slot_label']}」收益最高："
                f"每多一件平均多 {top_sug['delta']} 套（当前共 {stats['total_combinations']} 套）。"
            )
            if top_sug["diversity_discount"] < 0.5 and top_sug["current_count"] > 0:
                suggestions.append(
                    f"注意：「{top_sug['slot_label']}」已经有 {top_sug['current_count']} 件、"
                    f"但主色只用到 {round(top_sug['diversity_discount'] * top_sug['current_count'])} 种，"
                    "再补同色的收益会打折，建议换个颜色。"
                )
        if 0 < counts["shoes"] <= 1:
            suggestions.append("只有一双鞋，所有搭配都穿同一双，建议补一双不同风格的。")
        if counts["outer"] == 0:
            suggestions.append("没有外套——换季和降温时会缺少可叠加的层。")
        if never_worn:
            names = "、".join(
                (i.get("name") or i.get("type_label") or "未命名") for i in never_worn[:5]
            )
            suggestions.append(f"有 {len(never_worn)} 件从没穿过：{names}")

    return {
        "season": stats["season"],
        "season_label": stats["season_label"],
        "counts": counts,
        "total_combinations": stats["total_combinations"],
        "min_viable": stats["min_viable"],
        "blocking_slots": stats["blocking_slots"],
        "blocking_labels": stats["blocking_labels"],
        "slot_suggestions": stats["suggestions"],
        "never_worn": never_worn,
        "suggestions": suggestions,
        "legacy_rule_verdict": _legacy_verdict(type_counter),
    }


# ---------------------------------------------------------------------------
# 组合数与样例（供录入即时反馈使用，见设计文档 §9.1 / §9.2）
# ---------------------------------------------------------------------------
def total_combinations(user_id: str, season: str | None = None) -> dict:
    """当前的季节化组合统计。路由用它取"录入前 / 录入后"两个数。"""
    return taxonomy.count_combinations(_all_items(user_id), season)


def combinations_preview(
    user_id: str,
    limit: int | None = 20,
    season: str | None = None,
    *,
    per_slot: int | None = None,
    score_of=None,
) -> dict:
    """枚举当前衣橱能搭出的搭配样例。

    - `limit`：取多少套。传 `None` = 全部取出（调用方需自己保证规模可控）
    - `per_slot` + `score_of`：先按打分把每个槽位截到前 K 件再枚举。
      推荐用（规格 §3.3）：候选从几千降到几百，留下的还都是更合口味的。
      不传就是全量枚举 —— 看衣橱用这个，覆盖优先。

    `truncated` 的含义是「**候选空间里还有没被取出来的**」；`considered` 是被枚举
    的那个空间有多大（`per_slot` 截过时会小于 `total_combinations`）。
    """
    all_items = _all_items(user_id)
    stats = taxonomy.count_combinations(all_items, season)
    pool = _pool(all_items, stats["season"])
    slots = _by_slot(pool)
    if per_slot and score_of is not None:
        slots = _cap_slots(slots, per_slot, score_of)
    by_id = {i["id"]: i for i in all_items}
    previews = _enumerate(slots, limit)

    considered = _count_combos(slots)
    return {
        "season": stats["season"],
        "season_label": stats["season_label"],
        "total_combinations": stats["total_combinations"],
        "counts": stats["counts"],
        "sample": _attach_items(previews, by_id),
        # 边界修正：不能写 len(out) >= limit ——
        # 候选数恰好等于 limit 时那会误报"被截断"
        "truncated": considered > len(previews),
        "considered": considered,
    }


def sample_outfits_with(
    user_id: str, item_id: str, limit: int = 3, season: str | None = None
) -> list[dict]:
    """给出「包含这件单品」的搭配样例。

    录入一件新衣服后立刻展示，让用户看到"它已经能用了"（设计文档 §9.2）。
    """
    all_items = _all_items(user_id)
    by_id = {i["id"]: i for i in all_items}
    target = by_id.get(item_id)
    if not target:
        return []

    stats = taxonomy.count_combinations(all_items, season)
    pool = _pool(all_items, stats["season"])
    slots = _by_slot(pool)

    slot = target["slot"]
    tid = target["id"]
    previews: list[dict] = []

    def add(ids: list, kind: str) -> bool:
        previews.append({"item_ids": ids, "kind": kind})
        return len(previews) < limit

    if slot == "full":
        for s in slots.get("shoes", []):
            if not add([tid, s["id"]], "full"):
                break
    elif slot == "shoes":
        for f in slots.get("full", []):
            if not add([f["id"], tid], "full"):
                break
        if len(previews) < limit:
            for t in slots.get("top", []):
                for b in slots.get("bottom", []):
                    if not add([t["id"], b["id"], tid], "split"):
                        break
                if len(previews) >= limit:
                    break
    elif slot in ("top", "bottom"):
        other = "bottom" if slot == "top" else "top"
        for o in slots.get(other, []):
            for s in slots.get("shoes", []):
                ids = [tid, o["id"], s["id"]] if slot == "top" else [o["id"], tid, s["id"]]
                if not add(ids, "split"):
                    break
            if len(previews) >= limit:
                break
    else:
        # outer / accessory：它们本身不构成基础层，
        # 所以给一整套 + 这件作为叠加层的样子
        for t in slots.get("top", []):
            for b in slots.get("bottom", []):
                for s in slots.get("shoes", []):
                    if not add([t["id"], b["id"], s["id"], tid], "split"):
                        break
                if len(previews) >= limit:
                    break
            if len(previews) >= limit:
                break

    return _attach_items(previews, by_id)
