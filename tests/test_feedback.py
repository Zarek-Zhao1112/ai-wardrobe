"""`feedback` 的单元测试。

核心断言不是"反馈写进表了"，而是**"偏好记忆真的变了"** ——
反馈如果只落库、不改变记忆，整个闭环就没闭合，而那种缺陷是**静默**的
（接口返回 200，界面一切正常，只是 AI 永远学不会）。

每个测试刻意用不同的颜色值，避免共享测试库时互相干扰。
"""

from __future__ import annotations

import json

import pytest

from app import db
from app.models import ItemCreate
from app.services import feedback
from app.services import items as item_svc


def _make_item(uid: str, **fields) -> str:
    payload = {"type": fields.pop("type", "tshirt"), "name": fields.pop("name", "测试单品")}
    payload.update(fields)
    return item_svc.create(uid, ItemCreate(**payload))["id"]


def _make_outfit(uid: str, item_ids: list[str], scene=None) -> str:
    outfit_id = db.new_id("of")
    with db.conn() as c:
        c.execute(
            "INSERT INTO outfits (id,user_id,item_ids,scene,status,generated_by,created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (outfit_id, uid, json.dumps(item_ids), scene, "proposed", "llm", db.now_iso()),
        )
    return outfit_id


def _pref_rows(uid: str, tag_type: str, tag_value: str) -> list[dict]:
    with db.conn() as c:
        rows = c.execute(
            "SELECT * FROM preference_memory WHERE user_id=? AND tag_type=? AND tag_value=?",
            (uid, tag_type, tag_value),
        ).fetchall()
    return [dict(r) for r in rows]


def _outfit_status(outfit_id: str) -> str:
    with db.conn() as c:
        row = c.execute("SELECT status FROM outfits WHERE id=?", (outfit_id,)).fetchone()
    return row["status"]


class TestSubmit:
    def test_like_writes_feedback_and_moves_outfit_status(self, uid):
        item_id = _make_item(uid, primary_color="black")
        outfit_id = _make_outfit(uid, [item_id])

        result = feedback.submit(uid, feedback_type="like", outfit_id=outfit_id)

        assert result["duplicate"] is False
        assert result["recorded"] > 0
        assert result["feedback"]["feedback_type"] == "like"
        assert _outfit_status(outfit_id) == "accepted"

    def test_like_actually_moves_preference_memory(self, uid):
        """★ 闭环的核心：反馈必须真的改变记忆。"""
        item_id = _make_item(uid, primary_color="gray")
        outfit_id = _make_outfit(uid, [item_id])

        assert _pref_rows(uid, "color", "gray") == [], "前置条件：不该已有记录"

        feedback.submit(uid, feedback_type="like", outfit_id=outfit_id)

        rows = _pref_rows(uid, "color", "gray")
        assert len(rows) == 1
        assert rows[0]["score"] > 0
        assert rows[0]["evidence_count"] > 0
        assert rows[0]["pos_weight"] > 0

    def test_json_columns_are_not_exploded_into_characters(self, uid):
        """★ 回归：`colors` 是 JSON 数组列，从数据库读**原始行**时它是字符串。

        不做 `to_out()` 转换的话，`for c in '["cornflower"]'` 会**逐字符**迭代，
        往记忆里写进 `color:"["`、`color:"w"` 这种垃圾 —— **而接口照样返回 200**。

        单测抓不到这个：单测手上是 `items.create()` 的返回值（已经转过），
        只有"从库里读原始行"的那条路径才会踩到，所以是集成时才暴露的。
        """
        item_id = _make_item(uid, colors=["cornflower"], primary_color="cornflower")
        outfit_id = _make_outfit(uid, [item_id])

        feedback.submit(uid, feedback_type="like", outfit_id=outfit_id)

        assert _pref_rows(uid, "color", "cornflower"), "完整的颜色值应当被记录"
        assert _pref_rows(uid, "color", "[") == [], "不该出现单字符垃圾 tag"

    def test_dislike_pushes_score_negative(self, uid):
        item_id = _make_item(uid, primary_color="beige")
        outfit_id = _make_outfit(uid, [item_id])

        feedback.submit(uid, feedback_type="dislike", outfit_id=outfit_id)

        rows = _pref_rows(uid, "color", "beige")
        assert rows[0]["score"] < 0
        assert rows[0]["neg_weight"] > 0
        assert _outfit_status(outfit_id) == "rejected"

    def test_swap_is_weaker_than_dislike(self, uid):
        """「换一套」只说明这套不合适，不等于不喜欢里面每一件 → 负分应当更轻。"""
        swap_item = _make_item(uid, primary_color="green")
        dislike_item = _make_item(uid, primary_color="red")
        feedback.submit(uid, feedback_type="swap", outfit_id=_make_outfit(uid, [swap_item]))
        feedback.submit(uid, feedback_type="dislike", outfit_id=_make_outfit(uid, [dislike_item]))

        swap_score = _pref_rows(uid, "color", "green")[0]["score"]
        dislike_score = _pref_rows(uid, "color", "red")[0]["score"]
        assert swap_score > dislike_score

    def test_duplicate_submission_is_idempotent(self, uid):
        """防网络重试污染：同搭配同类型重复提交不重复累加证据。"""
        item_id = _make_item(uid, primary_color="pink")
        outfit_id = _make_outfit(uid, [item_id])

        first = feedback.submit(uid, feedback_type="like", outfit_id=outfit_id)
        evidence_after_first = _pref_rows(uid, "color", "pink")[0]["evidence_count"]
        second = feedback.submit(uid, feedback_type="like", outfit_id=outfit_id)

        assert second["duplicate"] is True
        assert second["recorded"] == 0
        assert second["feedback"]["id"] == first["feedback"]["id"]
        assert _pref_rows(uid, "color", "pink")[0]["evidence_count"] == evidence_after_first

    def test_changing_mind_is_not_a_duplicate(self, uid):
        item_id = _make_item(uid, primary_color="yellow")
        outfit_id = _make_outfit(uid, [item_id])

        feedback.submit(uid, feedback_type="like", outfit_id=outfit_id)
        again = feedback.submit(uid, feedback_type="dislike", outfit_id=outfit_id)

        assert again["duplicate"] is False
        assert _outfit_status(outfit_id) == "rejected"

    def test_unknown_feedback_type_is_rejected(self, uid):
        with pytest.raises(ValueError, match="feedback_type"):
            feedback.submit(uid, feedback_type="meh", item_ids=["whatever"])

    def test_feedback_without_real_items_is_rejected(self, uid):
        with pytest.raises(ValueError, match="没有关联到任何真实单品"):
            feedback.submit(uid, feedback_type="like", item_ids=["it_根本不存在"])

    def test_context_key_carries_the_scene(self, uid):
        item_id = _make_item(uid, primary_color="orange")
        outfit_id = _make_outfit(uid, [item_id], scene="work")
        result = feedback.submit(uid, feedback_type="like", outfit_id=outfit_id)
        assert result["context_key"].endswith("|work")

    def test_ratings_and_comment_are_stored(self, uid):
        item_id = _make_item(uid, primary_color="white")
        outfit_id = _make_outfit(uid, [item_id])
        result = feedback.submit(
            uid,
            feedback_type="like",
            outfit_id=outfit_id,
            rating=5,
            comfort_rating=4,
            style_rating=5,
            comment="很舒服",
        )
        fb = result["feedback"]
        assert fb["rating"] == 5
        assert fb["comfort_rating"] == 4
        assert fb["style_rating"] == 5
        assert fb["comment"] == "很舒服"

    def test_every_item_in_the_outfit_gets_evidence(self, uid):
        """一次反馈要覆盖整套里的每件单品，不能只学第一件。"""
        top = _make_item(uid, primary_color="light_blue")
        bottom = _make_item(uid, type="jeans", primary_color="light_blue")
        outfit_id = _make_outfit(uid, [top, bottom])

        result = feedback.submit(uid, feedback_type="like", outfit_id=outfit_id)

        assert len(result["updated_tags"]) > 0
        # 两件都是浅蓝 → 同一个 (color, light_blue) 被记了两次（每次 k=2）
        # 用 approx 而不是精确等号：n 是浮点累积（每次更新乘一个略小于 1 的
        # 衰减因子），库里存的会是 3.9999996 而不是 4 —— 展示层才 round。
        rows = _pref_rows(uid, "color", "light_blue")
        assert rows[0]["evidence_count"] == pytest.approx(4.0, abs=0.01)

    def test_feedback_against_wear_event_also_works(self, uid):
        """schema 允许对"实际穿了什么"反馈（wear_event），这条路也要通。"""
        item_id = _make_item(uid, primary_color="navy")
        event_id = db.new_id("we")
        with db.conn() as c:
            c.execute(
                "INSERT INTO wear_events (id,user_id,worn_at,item_ids,occasion,source,created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (event_id, uid, "2026-10-03", json.dumps([item_id]), "daily",
                 "self_recorded", db.now_iso()),
            )
        result = feedback.submit(uid, feedback_type="like", wear_event_id=event_id)
        assert result["recorded"] > 0
        assert result["context_key"].endswith("|daily")
