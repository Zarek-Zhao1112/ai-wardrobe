"""搭配推荐：本地枚举候选 → 本地打分 → MMR 去重 → **LLM 只排序和写理由**。

## 核心设计：让幻觉在结构上不可能

v0.1 §13 的做法是"让模型输出 item_id，再做校验"。那是**提示词约束**问题 ——
约束得再好，模型仍可能编一个不存在的 id 出来，你只能在事后抓。

这里换一条路（设计文档 §10）：

    本地枚举合法组合（item_id 必然真实）→ 模型只负责**重排**和**写理由**

模型的输入里只有短别名，输出里只有"选第几套 + 一句理由"。它**没有机会**生成
item_id，因为它从来没拿到过真 id。这是结构性的，不依赖提示词写得好不好。

## 防幻觉的三层（前两层在这里）

1. 候选全部由本地 `wardrobe.combinations_preview()` 枚举 —— 组合里的 id 来自数据库
2. 模型返回的编号要过一遍范围校验，越界的丢弃（**不是报错** —— 一个坏编号不该让整次推荐作废）
3. （结构性）模型根本不生成 id

## 打分怎么算

单品得分 = 它身上所有 tag 的 `decayed_score × confidence` 的**均值**
（均值而非求和：否则属性填得多的单品天然占便宜，那是打标完整度的差异，不是偏好差异）

组合得分 = 组合内单品得分的均值。

再归一化到 `[0,1]` 才进 MMR —— MMR 的公式假定相关性是非负的，
而偏好分本身有正负，直接代进去会让「多样性惩罚」压过「相关性」。

## 多样性为什么并进 MMR，而不是选完再检查

设计文档写的是硬约束「3 套中 ≥2 套主色不同、≥2 套 formality 不同」。
但如果把它做成"选完再检查"，在只有 3 套时发现不满足，修复策略会很难看
（换掉哪一套？换进来的又可能撞另一套）。所以这里把**属性相似度直接并入 MMR 的距离项**，
让多样性在挑选过程中自然满足。等价，但不用打补丁。
"""

from __future__ import annotations

import json
from typing import Any, Optional

from .. import db
from . import preference, wardrobe
from .llm.client import LLMClient, LLMError, get_client
from .llm.prompts import AliasMap

#: MMR 的相关性权重。0.7 表示「七分看合不合口味，三分看够不够多样」。
MMR_LAMBDA: float = 0.7

#: 单次送给模型的候选套数上限。再多就是白烧 token —— 模型挑不出第 30 套的差别。
MAX_TO_LLM: int = 12

REASON_PROMPT = """你在帮一个人挑今天穿哪套衣服。

**单品行**（格式：代号|槽位|主色|材质|版型|正式度|季节，缺的字段留空）：
{items}

**候选搭配**（每套由若干件单品组成，用上面那些代号表示）：
{catalog}

场合：{scene}
季节：{season}

请从中挑出最合适的 {count} 套并**排序**（第 1 套是你最推荐的），
每套用一行为理由，说明为什么这样搭合适（20~30 字，口语化，不要客套话）。
理由里可以引用单品的颜色/材质/款型来支撑你的判断。

**只能从上面这些搭配里挑**，不要组合出新的搭配。

输出 JSON：
{{"ranked": [{{"outfit": "O1", "reason": "..."}}, ...]}}
"""


# ---------------------------------------------------------------------------
# 本地打分
# ---------------------------------------------------------------------------

def build_score_index(user_id: str, season: Any, scene: Any) -> dict[tuple[str, str], float]:
    """把偏好记忆摊成 `(tag_type, tag_value) -> 有效分` 的索引。

    有效分 = `decayed_score × confidence`：既考虑"多喜欢"，也考虑"多确定"。
    一个只出现过两次的偏好不该和二十次的偏好等权。
    """
    context = preference.context_key(season, scene)
    index: dict[tuple[str, str], float] = {}
    for row in preference.query(user_id, context=context):
        index[(row["tag_type"], row["tag_value"])] = row["decayed_score"] * row["confidence"]
    return index


def item_score(item: dict, index: dict[tuple[str, str], float]) -> float:
    """单品与当前偏好的契合度 = 命中 tag 得分的均值。"""
    values = [index[t] for t in preference.tags_of_item(item) if t in index]
    if not values:
        return 0.0
    return sum(values) / len(values)


def outfit_score(outfit: dict, index: dict[tuple[str, str], float]) -> float:
    """组合得分为组合内单品得分的均值。"""
    items = outfit.get("items") or []
    if not items:
        return 0.0
    return sum(item_score(i, index) for i in items) / len(items)


def _normalize(scores: list[float]) -> list[float]:
    """把分数线性映射到 `[0,1]`。全相等时一律给 0.5（没有区分度可言）。"""
    if not scores:
        return []
    lo, hi = min(scores), max(scores)
    if hi - lo < 1e-9:
        return [0.5] * len(scores)
    return [(s - lo) / (hi - lo) for s in scores]


# ---------------------------------------------------------------------------
# 多样性
# ---------------------------------------------------------------------------

def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _attr_set(outfit: dict, field: str) -> set:
    values = set()
    for item in outfit.get("items") or []:
        value = item.get(field)
        if value:
            values.add(str(value))
    return values


def similarity(a: dict, b: dict) -> float:
    """两套搭配的相似度：单品重合度为主，属性重合度为辅。

    单品重合是硬信号（同一件衣服出现两次就是雷同）；
    属性重合是软信号 —— 你没道理因为两套都用白鞋就判它们重复。
    所以取 `max` 而不是加权和：只要任一项高度重合，就该被推开。
    """
    by_item = _jaccard(set(a.get("item_ids") or []), set(b.get("item_ids") or []))
    colors = _jaccard(_attr_set(a, "primary_color"), _attr_set(b, "primary_color"))
    formal = _jaccard(_attr_set(a, "formality"), _attr_set(b, "formality"))
    return max(by_item, 0.6 * colors + 0.4 * formal)


def mmr_select(candidates: list[dict], count: int, lam: float = MMR_LAMBDA) -> list[dict]:
    """MMR 贪心选 `count` 套：`pick = argmax[ λ·相关性 − (1−λ)·最大相似度 ]`。

    贪心而非全局最优：组合数上千时全局搜索没有收益，而贪心结果稳定可复现
    （每一步的选择只依赖已选集合，所以同样的输入永远得到同样的输出 —— 这对测试很重要）。
    """
    if count <= 0 or not candidates:
        return []
    pool = list(candidates)
    picked: list[dict] = []
    while pool and len(picked) < count:
        best: Optional[dict] = None
        best_value = float("-inf")
        for cand in pool:
            if picked:
                penalty = max(similarity(cand, p) for p in picked)
            else:
                penalty = 0.0
            value = lam * float(cand.get("_relevance", 0.0)) - (1.0 - lam) * penalty
            if value > best_value:
                best, best_value = cand, value
        if best is None:
            break
        picked.append(best)
        pool.remove(best)
    return picked


def _attr_union(outfits: list[dict], field: str) -> set:
    values: set = set()
    for outfit in outfits:
        values |= _attr_set(outfit, field)
    return values


def _diversity_ok(picked: list[dict]) -> bool:
    """硬约束（设计文档 §10.4）：≥2 套主色不同、≥2 套正式度不同。

    只有 1 套时无从比较，算通过。
    """
    if len(picked) < 2:
        return True
    if len(_attr_union(picked, "primary_color")) < 2:
        return False
    if len(_attr_union(picked, "formality")) < 2:
        return False
    return True


def ensure_diversity(picked: list[dict], pool: list[dict]) -> list[dict]:
    """MMR 之后补一次**硬约束**修复。

    为什么必须补：MMR 只是**软**约束。相关性差距足够大时它必然牺牲多样性 ——
    反例（W4 规格核对官给的）：4 个候选里 3 套同色、相关性 0.98~1.0，
    另 1 套异色但相关性 0。`λ=0.7` 下同色的仍然全胜：
    `0.7*0.99 − 0.3*1.0 = 0.393 > 0.7*0 − 0.3*0 = 0`。
    **数学上无解**，只能后置修复。

    策略：从池子里找一套**能同时满足两个约束**的，替换掉最后一套。
    找不到就原样返回 —— **尽力而为，不报错**：衣橱里如果全是黑衣服，
    这个约束根本不可能满足，那不该让推荐整个失败。
    """
    if _diversity_ok(picked) or not picked:
        return picked

    used = {tuple(o.get("item_ids") or []) for o in picked}
    for cand in pool:                       # pool 已按相关性降序
        key = tuple(cand.get("item_ids") or [])
        if key in used:
            continue
        trial = picked[:-1] + [cand]
        if _diversity_ok(trial):
            return trial
    return picked


# ---------------------------------------------------------------------------
# LLM 排序
# ---------------------------------------------------------------------------

def _catalog(outfit_ids: list[str], outfits_by_id: dict[str, dict], aliases: AliasMap) -> str:
    """把候选套装配成给模型看的清单。单品用别名，套装用 `O1..On`。"""
    lines: list[str] = []
    for oid in outfit_ids:
        outfit = outfits_by_id[oid]
        codes = [aliases.alias_of(i) or "?" for i in outfit["item_ids"]]
        lines.append(f"{oid}: " + " + ".join(codes))
    return "\n".join(lines)


def _llm_rank(
    client: LLMClient,
    outfit_ids: list[str],
    outfits_by_id: dict[str, dict],
    aliases: AliasMap,
    item_lines: str,
    *,
    count: int,
    scene: Any,
    season: Any,
) -> list[dict]:
    """让模型重排并写理由。返回 `[{"outfit": "O1", "reason": "..."}]`（可能被裁剪）。

    ⚠️ `item_lines` 是 `AliasMap.build()` 的返回值，**必须真的塞进 prompt**。
    否则模型只看到 `O1: A1 + A2 + A3` 这种纯代号，却要写「为什么这套合适」——
    它唯一能做的就是编。（W4 审查抓到：这个返回值原本被丢掉了。）

    ⚠️ 模型返回越界编号时**丢弃**而不是报错 —— 一个坏编号不该毁掉整次推荐。
    """
    catalog = _catalog(outfit_ids, outfits_by_id, aliases)
    prompt = REASON_PROMPT.format(
        items=item_lines,
        catalog=catalog,
        scene=scene or "未指定",
        season=season or "未指定",
        count=count,
    )
    data = client.chat_json(
        [
            {"role": "system", "content": "你是一个穿搭顾问，只从给定选项里挑选并排序。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
    )
    ranked = data.get("ranked") if isinstance(data, dict) else None
    if not isinstance(ranked, list):
        return []

    valid = set(outfit_ids)
    out: list[dict] = []
    seen: set[str] = set()
    for entry in ranked:
        if not isinstance(entry, dict):
            continue
        oid = str(entry.get("outfit", "")).strip().upper()
        if oid not in valid or oid in seen:
            continue
        seen.add(oid)
        reason = entry.get("reason")
        out.append({"outfit": oid, "reason": str(reason).strip() if reason else None})
    return out


# ---------------------------------------------------------------------------
# 落库
# ---------------------------------------------------------------------------

def _persist(
    user_id: str,
    outfit: dict,
    *,
    scene: Any,
    weather: Any,
    reason: Optional[str],
    confidence: Optional[float],
    source: str,
    now: str,
) -> dict:
    oid = db.new_id("of")
    with db.conn() as c:
        c.execute(
            """
            INSERT INTO outfits
                (id,user_id,item_ids,scene,weather,reason,confidence,
                 status,generated_by,source,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                oid, user_id,
                json.dumps(outfit["item_ids"], ensure_ascii=False),
                scene, weather, reason, confidence,
                "proposed", "llm", source, now,
            ),
        )
        # 记录"这件被推荐过" —— 采纳率指标（设计文档 §7）依赖它
        for item_id in outfit["item_ids"]:
            c.execute(
                """
                UPDATE clothing_items
                   SET suggestion_count = suggestion_count + 1,
                       last_suggested_at = ?,
                       updated_at = ?
                 WHERE id=? AND user_id=?
                """,
                (now, now, item_id, user_id),
            )
        row = c.execute("SELECT * FROM outfits WHERE id=?", (oid,)).fetchone()
    return dict(row)


def _json_ids(raw: Any) -> list[str]:
    """解析 `item_ids` 这类 JSON 数组列。坏数据返回空列表，不抛异常。"""
    try:
        value = json.loads(raw if raw else "[]")
    except (TypeError, ValueError):
        return []
    return [str(v) for v in value] if isinstance(value, list) else []


def _to_out(row: dict, items_by_id: dict[str, dict]) -> dict:
    ids = _json_ids(row.get("item_ids"))
    return {
        "id": row["id"],
        "item_ids": ids,
        "items": [items_by_id[i] for i in ids if i in items_by_id],
        "scene": row.get("scene"),
        "weather": row.get("weather"),
        "reason": row.get("reason"),
        "confidence": row.get("confidence"),
        "status": row.get("status") or "proposed",
        "generated_by": row.get("generated_by") or "llm",
        "created_at": row["created_at"],
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def recommend(
    user_id: str,
    *,
    scene: Any = None,
    season: Any = None,
    limit: int = 3,
    weather: Any = None,
    source: str = "api",
    client: Optional[LLMClient] = None,
    use_llm: bool = True,
) -> dict:
    """给当前上下文推荐 `limit` 套搭配。

    返回：
        `{"season", "season_label", "candidates", "outfits": [...], "degraded", "note"}`

    **降级行为（重要）**：未配模型 / 模型调用失败时，**不抛异常**，而是退回
    "只用本地打分排序"，`outfits` 里的 `reason` 为空、`degraded=True`。
    理由：没有 key 也应该能用（本地排序本身已经基于用户偏好），
    而且这让 P1 阶段（尚未配 key）也能端到端验证整条链路。
    """
    # 先定季节才能建偏好索引（索引按「季节 × 场合」分桶）。
    # 不能反过来用 preview 的 season 推 —— 那会变成先有鸡还是先有蛋。
    actual_season = wardrobe.total_combinations(user_id, season)["season"]
    index = build_score_index(user_id, actual_season, scene)

    # 候选 = 每槽位按偏好取前 K 件，再**全量**枚举。
    # `limit=None` 是刻意的：池子已被 `per_slot` 压到千余套，全取才不会漏掉
    # "最好的那一套" —— 只要采样，就一定会漏东西。
    preview = wardrobe.combinations_preview(
        user_id,
        limit=None,
        season=actual_season,
        per_slot=wardrobe.PER_SLOT_TOP_K,
        score_of=lambda it: item_score(it, index),
    )
    sample = preview.get("sample") or []

    items_by_id: dict[str, dict] = {}
    for outfit in sample:
        for item in outfit.get("items") or []:
            items_by_id[item["id"]] = item

    if not sample:
        return {
            "season": actual_season,
            "season_label": preview.get("season_label"),
            "candidates": 0,
            "outfits": [],
            "degraded": False,
            "note": "当前季节没有可搭出的组合（衣橱可能还没到最小可用量）",
        }

    # ---- 本地打分 + MMR 选 ----
    scored: list[dict] = []
    for outfit in sample:
        scored.append({**outfit, "_score": outfit_score(outfit, index)})
    relevance = _normalize([c["_score"] for c in scored])
    for cand, rel in zip(scored, relevance):
        cand["_relevance"] = rel

    scored.sort(key=lambda c: c["_relevance"], reverse=True)
    picked = mmr_select(scored, limit)
    # MMR 是软约束，补一次硬约束校验（≥2 套主色不同、≥2 套正式度不同）
    picked = ensure_diversity(picked, scored)

    # ---- 给模型看的那批：本地排前 MAX_TO_LLM 套 ----
    shortlist = scored[:MAX_TO_LLM]
    outfits_by_id = {f"O{n}": o for n, o in enumerate(shortlist, start=1)}

    aliases = AliasMap()
    # ⚠️ 这个返回值**必须留着传下去** —— 它就是给模型看的单品行文本。
    # 丢掉它的话，模型只看到 `O1: A1 + A2 + A3` 一堆代号，却要写"为什么合适"。
    item_lines = aliases.build([items_by_id[i] for i in items_by_id])

    degraded = False
    note: Optional[str] = None
    ranked: list[dict] = []

    if use_llm and shortlist:
        try:
            cli = client or get_client()
            ranked = _llm_rank(
                cli, list(outfits_by_id.keys()), outfits_by_id, aliases, item_lines,
                count=min(limit, len(outfits_by_id)),
                scene=scene, season=preview.get("season_label") or actual_season,
            )
        except LLMError as exc:
            degraded = True
            note = f"模型不可用，已退回本地排序：{exc}"

    # ---- 决定最终顺序 ----
    # ⚠️ 必须**保持模型给出的顺序** —— 那是它这一趟唯一的产出。
    # 去重按 `item_ids` 元组（dict 不可 hash），因为同一套可能同时来自模型和本地补齐。
    final: list[tuple[dict, Optional[str]]] = []
    seen: set[tuple] = set()

    def _add(outfit: dict, reason: Optional[str]) -> None:
        key = tuple(outfit.get("item_ids") or [])
        if not key or key in seen:
            return
        seen.add(key)
        final.append((outfit, reason))

    for entry in ranked:                      # 模型排的在前
        if len(final) >= limit:
            break
        _add(outfits_by_id[entry["outfit"]], entry["reason"])

    for cand in picked:                       # 不够就用本地 MMR 顺序补齐
        if len(final) >= limit:
            break
        _add(cand, None)

    # ---- 落库 ----
    now = db.now_iso()
    saved: list[dict] = []
    for outfit, reason in final:
        confidence = round(max(0.0, min(1.0, float(outfit.get("_relevance", 0.0)))), 4)
        row = _persist(
            user_id, outfit,
            scene=scene, weather=weather, reason=reason,
            confidence=confidence, source=source, now=now,
        )
        saved.append(_to_out(row, items_by_id))

    return {
        "season": actual_season,
        "season_label": preview.get("season_label"),
        "candidates": len(sample),
        "outfits": saved,
        "degraded": degraded,
        "note": note,
    }


# ---------------------------------------------------------------------------
# 读取历史搭配（供路由展示）
# ---------------------------------------------------------------------------

def _load_items_by_id(user_id: str, ids: list[str]) -> dict[str, dict]:
    """按 id 批量取单品。**带 user_id 过滤**（归属校验，与其它模块同一口径）。"""
    if not ids:
        return {}
    from .items import to_out

    placeholders = ",".join("?" * len(ids))
    with db.conn() as c:
        rows = c.execute(
            f"SELECT * FROM clothing_items WHERE user_id=? AND id IN ({placeholders})",
            [user_id, *ids],
        ).fetchall()
    return {row["id"]: to_out(row) for row in rows}


def list_outfits(user_id: str, *, limit: int = 20) -> list[dict]:
    """最近生成过的搭配，新的在前。含反馈后的状态。"""
    with db.conn() as c:
        rows = c.execute(
            "SELECT * FROM outfits WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()

    out: list[dict] = []
    for row in rows:
        data = dict(row)
        ids = _json_ids(data.get("item_ids"))
        out.append(_to_out(data, _load_items_by_id(user_id, ids)))
    return out


def get_outfit(user_id: str, outfit_id: str) -> Optional[dict]:
    with db.conn() as c:
        row = c.execute(
            "SELECT * FROM outfits WHERE id=? AND user_id=?", (outfit_id, user_id)
        ).fetchone()
    if row is None:
        return None
    data = dict(row)
    return _to_out(data, _load_items_by_id(user_id, _json_ids(data.get("item_ids"))))
