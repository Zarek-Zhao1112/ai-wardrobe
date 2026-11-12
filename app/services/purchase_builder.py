"""§59：围绕用户选中的衣橱衣物生成添置建议。"""
from __future__ import annotations

import json
import threading

from pydantic import Field

from .. import db
from . import aesthetics, garment_profile, purchase, purchase_analysis, purchase_intent as intent
from . import purchase_reference as refs

_MODES = {"single", "multi", "outfit"}
MAX_SELECTED = 12


class BuilderDirection(purchase.Direction):
    owned: list[str] = Field(min_length=1, max_length=MAX_SELECTED)


class BuilderPlan(purchase.ModelPlan):
    directions: list[BuilderDirection] = Field(max_length=3)


def _latest(uid):
    rid = intent._read(uid, "builder-latest")
    return intent._read(uid, "request:" + rid) if rid else None


def _record(uid, request_id):
    record = purchase_analysis.record(uid, request_id)
    if not record.get("builder"):
        raise purchase.PurchaseError("没有找到这次添置生成。", 404)
    return record


def snapshot(uid, record=None, *, inventory=False):
    with intent._lock:
        record = record or _latest(uid)
        plan = intent._read(uid, "builder-result")
        public = intent._public(uid, record)
        if public:
            public.update(mode=record["builder"], item_ids=record["item_ids"])
        return {
            "request": public,
            "result": plan,
            "references": refs.snapshots(uid, [plan]),
            **({"wardrobe": [purchase._public_item(row) for row in purchase.inventory(uid)]} if inventory else {}),
        }


def request_state(uid, request_id):
    return snapshot(uid, _record(uid, request_id))


def query(uid, request_id):
    _record(uid, request_id)
    return purchase_analysis.query(uid, request_id)


def start(uid, request_id, aesthetic, mode, item_ids, *, client=None, background=True):
    rid = intent._identifier(request_id)
    try:
        profile = aesthetics.get_profile(aesthetic)
    except ValueError as error:
        raise purchase.PurchaseError("请选择一个审美方向。", 422) from error
    if mode not in _MODES or not 1 <= len(item_ids) <= MAX_SELECTED or len(set(item_ids)) != len(item_ids):
        raise purchase.PurchaseError("请选择有效的衣橱衣物与添置形式。", 422)
    signature = intent._signature({"kind": "builder", "version": 1, "mode": mode,
        "aesthetic": aesthetic, "items": sorted(item_ids)})
    with intent._lock:
        if intent._existing(uid, rid, signature):
            return request_state(uid, rid)
        previous = _latest(uid)
        if previous and ((uid, previous["id"]) in intent._active or previous["status"] in intent.PENDING or
                any(job["status"] in refs.PENDING for job in purchase_analysis._snapshots(uid, previous).values())):
            raise purchase.PurchaseError("上一轮生成尚未确认，请先查看原任务。", 409)
        pool = purchase.inventory(uid)
        by_id = {row["id"]: row for row in pool}
        if not set(item_ids) <= by_id.keys():
            raise purchase.PurchaseError("所选衣物已变动，请返回重新选择。", 409)
        selected = [by_id[item_id] for item_id in item_ids]
        if mode == "outfit" and garment_profile.wearing_roles(selected) is None:
            raise purchase.PurchaseError("所选衣物里有不能同时穿的单品。请改选衣物，或选择单品推荐。", 422)
        value = {
            "id": rid, "plan_id": rid, "kind": "analyze", "workflow_version": 2, "builder": mode,
            "signature": signature, "status": "running", "phase": "analyzing", "created_at": db.now_iso(),
            "aesthetic": {"id": profile["id"], "name": profile["name"]},
            "item_ids": item_ids, "items": {row["id"]: purchase._public_item(row) for row in selected},
            "fingerprint": purchase._fingerprint(pool), "image_origin": "generated",
            "directions": [], "jobs": {}, "completed": 0, "total": 0,
        }
        with db.conn(immediate=True) as connection:
            intent._write(uid, "request:" + rid, value, connection)
            intent._write(uid, "builder-latest", rid, connection)
        intent._active.add((uid, rid))
    if background:
        threading.Thread(target=purchase_analysis._run, args=(uid, rid, pool, profile, client), daemon=True).start()
    else:
        purchase_analysis._run(uid, rid, pool, profile, client)
    return request_state(uid, rid)


def model_plan(uid, record, pool, profile, client):
    by_id = {row["id"]: row for row in pool}
    selected = [by_id[item_id] for item_id in record["item_ids"]]
    mode = record["builder"]
    independent = mode != "outfit"
    dismissed = purchase._read(uid, "choices", {"dismissed": []})["dismissed"]
    messages, aliases = purchase._messages(selected, profile, dismissed, pool)
    requirements = {
        "single": "只推荐1件单品，必须能够分别搭配全部已选衣物。",
        "multi": "视需要推荐2至3件不同单品，每件分别搭配一件或多件已选衣物，全部已选衣物都要被覆盖。各单品可以分别使用。",
        "outfit": "将全部已选衣物与1至3件新增衣物放在同一套中，凑齐上装、下装和鞋，或连身衣物和鞋。同一穿着位置不重复。",
    }
    messages[0]["content"] += (
        "\n本次 wardrobe 只包含用户选中的衣物。按以下要求生成：" + requirements[mode] +
        " 如果无法合理满足，可选 decision=enough 并说明原因。不要虚构已有衣物、商品或购买链接。"
    )
    if independent:
        messages[0]["content"] += "\n覆盖前文同时穿的规则：每条 owned 是能分别搭配这件新衣的已选衣物，每对仍须位置不冲突。"
    schema = purchase._response_schema(aliases, BuilderPlan, independent=independent)
    raw = (client or intent.get_client()).chat_json(messages, schema=schema, timeout=90, temperature=.4)
    plan = purchase._validate(raw, aliases, pool, model=BuilderPlan, independent=independent)
    directions = plan["directions"]
    if plan["decision"] == "enough":
        return plan
    if (mode == "single" and len(directions) != 1) or (mode == "multi" and len(directions) < 2):
        raise purchase.PurchaseError("AI 给出的数量与所选添置形式不符，可以再生成。", 502)
    if independent:
        used = {item_id for direction in directions for item_id in direction["owned"]}
        if used != set(record["item_ids"]):
            raise purchase.PurchaseError("这次推荐没有覆盖全部所选衣物，可以调整选择后再生成。", 502)
    else:
        combination = [*selected, *({"id": row["id"], "type": row["type"]} for row in directions)]
        roles = garment_profile.wearing_roles(combination)
        slots = set(roles.values()) if roles else set()
        if not ({"top", "bottom", "shoes"} <= slots or {"full", "shoes"} <= slots):
            raise purchase.PurchaseError("这次没有生成一套可同时穿着的完整搭配，可以再生成。", 502)
    return plan


def finish(uid, record, ready, pending):
    """等整组图齐备才替换现有结果，保存记录使用独立快照。"""
    rid = record["id"]
    if pending:
        intent._write(uid, "request:" + rid, record)
        return False
    if len(ready) != len(record["directions"]):
        intent._fail(uid, rid, "这组款式图没有全部生成成功，可以再试一次。")
        return True
    plan = {key: record[key] for key in (
        "item_ids", "items", "aesthetic", "decision", "headline", "summary", "image_origin"
    )}
    plan.update(id=rid, mode=record["builder"], directions=ready)
    with db.conn(immediate=True) as connection:
        if (record["fingerprint"] != purchase._fingerprint(purchase.inventory(uid))
                or intent._read(uid, "builder-latest") != rid):
            intent._write(uid, "request:" + rid, {**record, "status": "failed", "phase": "finished",
                "error": "衣橱已经更新，请重新选择衣物。"}, connection)
        else:
            intent._write(uid, "builder-result", plan, connection)
            intent._write(uid, "request:" + rid, {**record, "status": "completed", "phase": "finished"}, connection)
    return True


def save(uid, request_id, reference_ids):
    rid = intent._identifier(request_id)
    with db.conn(immediate=True) as connection:
        row = connection.execute("SELECT value FROM meta WHERE key=?", (intent._key(uid, "builder-result"),)).fetchone()
        plan = json.loads(row["value"]) if row else None
        if not plan or plan["id"] != rid or not plan["directions"]:
            raise purchase.PurchaseError("这组建议已更新，请重新打开后保存。", 409)
        directions = plan["directions"]
        if set(reference_ids) != {direction["id"] for direction in directions}:
            raise purchase.PurchaseError("请选择完整且可查看的添置建议。", 422)
        choices_key = purchase._key(uid, "choices")
        row = connection.execute("SELECT value FROM meta WHERE key=?", (choices_key,)).fetchone()
        choices = json.loads(row["value"]) if row else {"saved": [], "dismissed": []}
        saved_ids = {direction["id"] for entry in choices["saved"] if entry["id"] == rid for direction in entry["directions"]}
        for direction in directions:
            if direction["id"] in saved_ids:
                continue
            photo = refs.wanted_reference(uid, rid, direction["id"], reference_ids[direction["id"]], connection)
            choices["saved"].append({"id": rid, "aesthetic": plan["aesthetic"], "saved_at": db.now_iso(),
                "image_origin": plan["image_origin"], "directions": [{**direction, "saved_reference": photo}]})
        connection.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (choices_key, json.dumps(choices, ensure_ascii=False)))
    return purchase.get_state(uid)
