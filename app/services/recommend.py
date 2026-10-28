"""§20：完整审美方案召回与组套→核心单品多样化→模型受控排序。

场合不参与新推荐；保存生成时方案与五维事实快照。旧评分/MMR辅助函数
保留兼容既有调用与回归，新链路以aesthetics中的整套关系和核心单品多样性为准。
"""

from __future__ import annotations

import json
import re
from contextlib import nullcontext
from typing import Any, Optional

from .. import db
from . import aesthetics, preference, wardrobe
from .outfit_constraints import AnchorUnavailable, validate_anchor as _check_anchor
from .llm.client import LLMClient, LLMError, get_client
from .llm.prompts import AliasMap

#: MMR 的相关性权重。0.7 表示「七分看合不合口味，三分看够不够多样」。
MMR_LAMBDA: float = 0.7

#: 单次送给模型的候选套数上限。再多就是白烧 token —— 模型挑不出第 30 套的差别。
MAX_TO_LLM: int = 12




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
        if row["tag_type"] not in {"occasion", "formality"}:
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
    profile: dict | None = None,
) -> list[dict]:
    """让模型重排并写理由。返回 `[{"outfit": "O1", "reason": "..."}]`（可能被裁剪）。

    ⚠️ `item_lines` 是带短别名与真实属性的JSON行，必须传入模型。
    否则模型只看到 `O1: A1 + A2 + A3` 这种纯代号，却要写「为什么这套合适」——
    它唯一能做的就是编。（W4 审查抓到：这个返回值原本被丢掉了。）

    ⚠️ 模型返回越界编号时**丢弃**而不是报错 —— 一个坏编号不该毁掉整次推荐。
    """
    catalog = _catalog(outfit_ids, outfits_by_id, aliases)
    local_assessments = "\n".join(f"{oid}：" + json.dumps({d["key"]: d["status"] for d in outfits_by_id[oid].get("_assessment", {}).get("dimensions", [])}, ensure_ascii=False) for oid in outfit_ids)
    profile = profile or aesthetics.get_profile()
    goals = "\n".join(f"{d['label']}：{d['goal']}" for d in profile["dimensions"])
    prompt = f"""完整审美方案：{profile['name']}，版本{profile['version']}。
核心：{profile['core']}
可变化：{profile['flexible']}
五维安排：
{goals}
季节：{season or '未指定'}（仅表示衣橱过滤，不代表具体温度）。
单品是JSON行，缺失字段是null或空数组；只依据这些属性，不读取或猜测场合：
{item_lines}
候选搭配：
{catalog}
本地五维评估（matched仅表示已知属性线索符合方向，approximate为近似尝试，unknown为信息不足）：
{local_assessments}
请从候选中按同一套审美挑选并排序{count}套。颜色、图案和松紧不是硬性禁配。
只能返回已有候选编号，不得增减单品。理由只引用已知属性与方案目标，20~60字。
理由使用颜色和品类名称，不出现O1或A1等内部代号。
不能由材质成分断言光泽、挺阔、垂坠、柔软、保暖，不能从裤长断言上衣长度、腰线、显高显瘦或三七比例。
缺信息时承认未知；不要声称看过照片或实际试穿。
输出JSON：{{"ranked":[{{"outfit":"O1","reason":"..."}}]}}"""
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
        reason = reason.strip()[:500] if isinstance(reason, str) else None
        # 代号只供模型选择；带代号的理由舍弃，页面仍可展示本地五维事实依据。
        if reason and re.search(r"[OA][0-9]+", reason, re.I):
            reason = None
        out.append({"outfit": oid, "reason": reason or None})
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
    aesthetic: dict | None = None,
    connection=None,
) -> dict:
    oid = db.new_id("of")
    with (nullcontext(connection) if connection is not None else db.conn()) as c:
        c.execute(
            """
            INSERT INTO outfits
                (id,user_id,item_ids,scene,weather,reason,confidence,
                 status,generated_by,source,created_at,aesthetic_snapshot)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                oid, user_id,
                json.dumps(outfit["item_ids"], ensure_ascii=False),
                scene, weather, reason, confidence,
                "proposed", "llm", source, now,
                json.dumps(aesthetic, ensure_ascii=False) if aesthetic else None,
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
    try:
        snapshot = json.loads(row.get("aesthetic_snapshot") or "null")
    except (ValueError, TypeError):
        snapshot = None
    if not isinstance(snapshot, dict):
        snapshot = None
    return {
        "id": row["id"],
        "item_ids": ids,
        "items": [items_by_id[i] for i in ids if i in items_by_id],
        "scene": row.get("scene"),
        "weather": row.get("weather"),
        "reason": row.get("reason"),
        "confidence": row.get("confidence"),
        "aesthetic": snapshot,
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
    aesthetic: str | None = None,
    anchor_item_id: str | None = None,
) -> dict:
    """给当前上下文推荐 `limit` 套搭配。

    返回：
        `{"season", "season_label", "candidates", "outfits": [...], "degraded", "note"}`

    **降级行为（重要）**：未配模型 / 模型调用失败时，**不抛异常**，而是退回
    "只用本地打分排序"，`outfits` 里的 `reason` 为空、`degraded=True`。
    理由：没有 key 也应该能用（本地排序本身已经基于用户偏好），
    而且这让 P1 阶段（尚未配 key）也能端到端验证整条链路。
    """
    # §20：场合形参仅兼容旧入口，不参与新生成、偏好或保存。
    scene = None
    profile = aesthetics.get_profile(aesthetic)
    actual_season = wardrobe.total_combinations(user_id, season)["season"]
    all_items = wardrobe._all_items(user_id)
    _check_anchor(all_items, anchor_item_id, actual_season)
    index = build_score_index(user_id, actual_season, None)
    sample = aesthetics.build_candidates(all_items, profile, actual_season,
        preference_score=lambda it: item_score(it, index), anchor_item_id=anchor_item_id)
    if not sample:
        return {"season": actual_season, "season_label": wardrobe.taxonomy.SEASON_LABELS.get(actual_season, actual_season),
            "candidates": 0, "outfits": [], "degraded": False, "aesthetic": profile,
            "anchor_item_id": anchor_item_id,
            "note": ("暂时找不到包含这件衣服的完整搭配。可以调整季节或补充相关衣物。" if anchor_item_id else
                "当前季节没有可搭出的组合。需要上装、下装和鞋，或连身衣和鞋；也可以检查季节标注。")}

    # 同一方案既参与召回，也评价整套关系；旧颜色/正式度多样性硬修复不用于此链路。
    scores = _normalize([outfit_score(outfit, index) for outfit in sample])
    for outfit, personal in zip(sample, scores):
        assessment = aesthetics.assess(outfit, profile)
        outfit["_assessment"] = assessment
        outfit["_relevance"] = .85 * assessment["score"] + .15 * personal
    picked = aesthetics.shortlist(sample, limit)
    candidates_for_model = aesthetics.shortlist(sample, max(MAX_TO_LLM, limit))
    outfits_by_id = {f"O{n}": outfit for n, outfit in enumerate(candidates_for_model, start=1)}
    items_by_id = {item["id"]: item for outfit in sample for item in outfit["items"]}
    model_items = {item["id"]: item for outfit in candidates_for_model for item in outfit["items"]}
    aliases = AliasMap()
    aliases.build(list(model_items.values()))
    # JSON行避免名称、备注和分隔符混淆；只传受控字段，场合与正式度均不进入模型。
    item_lines = "\n".join(json.dumps({"code": aliases.alias_of(item["id"]),
        **{key: item.get(key) for key in ("type", "slot", "primary_color", "colors", "material", "fit", "pattern", "length", "style")}},
        ensure_ascii=False) for item in model_items.values())
    degraded, note, ranked = False, None, []
    if use_llm:
        try:
            ranked = _llm_rank(client or get_client(), list(outfits_by_id), outfits_by_id, aliases,
                item_lines, count=min(limit, len(outfits_by_id)), scene=None, season=actual_season, profile=profile)
            if not ranked:
                degraded = True
                note = "AI未返回有效候选，已按同一审美方案提供本地搭配。"
        except LLMError as exc:
            degraded = True
            note = f"模型不可用，已按同一审美方案提供本地搭配：{exc}"

    # ---- 决定最终顺序 ----
    # ⚠️ 必须**保持模型给出的顺序** —— 那是它这一趟唯一的产出。
    # 去重按 `item_ids` 元组（dict 不可 hash），因为同一套可能同时来自模型和本地补齐。
    final: list[tuple[dict, Optional[str]]] = []
    seen: set[tuple] = set()

    def _add(outfit: dict, reason: Optional[str]) -> None:
        key = tuple(sorted(outfit.get("item_ids") or []))
        if not key or key in seen or (anchor_item_id and anchor_item_id not in key):
            return
        seen.add(key)
        final.append((outfit, reason))

    for entry in ranked:                      # 模型排的在前
        if len(final) >= limit:
            break
        _add(outfits_by_id[entry["outfit"]], entry["reason"])

    for cand in picked:                       # 不够就按同一方案的本地顺序补齐
        if len(final) >= limit:
            break
        _add(cand, None)

    # ---- 落库 ----
    now = db.now_iso()
    saved: list[dict] = []
    # 先取得写锁，再复核并保存整批，堵住复核和首套/后续套之间的删除竞态。
    with db.conn(immediate=True) as connection:
        if anchor_item_id:
            from .items import to_out
            row = connection.execute("SELECT * FROM clothing_items WHERE id=? AND user_id=?",
                                     (anchor_item_id, user_id)).fetchone()
            _check_anchor([to_out(row)] if row else [], anchor_item_id, actual_season)
        for outfit, reason in final:
            confidence = round(max(0.0, min(1.0, float(outfit.get("_relevance", 0.0)))), 4)
            snapshot = {**profile, "assessment": {key: value for key, value in outfit["_assessment"].items() if key != "score"}}
            if anchor_item_id:
                snapshot["anchor_item_id"] = anchor_item_id
            row = _persist(
                user_id, outfit, scene=scene, weather=weather, reason=reason,
                confidence=confidence, source=source, now=now, aesthetic=snapshot, connection=connection,
            )
            saved.append(_to_out(row, items_by_id))

    return {
        "season": actual_season,
        "season_label": wardrobe.taxonomy.SEASON_LABELS.get(actual_season, actual_season),
        "aesthetic": profile,
        "anchor_item_id": anchor_item_id,
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
