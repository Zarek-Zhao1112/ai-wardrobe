"""批量导入测试：重点是**失败语义**。

语义已定（2026-10-03 拍板）：**跳过并回报清单**，不是整体回滚。
- 理由：导入是补数据的体力活，一件撞了不该让另外 19 件白干
- 但必须**逐件**回报，否则会变成「以为导完了，其实少了几件」
  —— 那正是最坏的一种失败：静默的
"""

from __future__ import annotations

from app.models import ItemCreate
from app.services import items as item_svc


class TestCreateMany:
    def test_empty_list(self, uid):
        result = item_svc.create_many(uid, [])
        assert result["total"] == 0
        assert result["created"] == 0
        assert result["outcomes"] == []

    def test_all_created(self, uid):
        payloads = [ItemCreate(type="tshirt", name=f"顺批{i}") for i in range(5)]
        result = item_svc.create_many(uid, payloads)

        assert (result["total"], result["created"], result["skipped"], result["failed"]) == (
            5, 5, 0, 0,
        )
        assert [o["index"] for o in result["outcomes"]] == [0, 1, 2, 3, 4]
        assert all(o["item_id"] for o in result["outcomes"])

    def test_duplicate_hash_is_skipped_and_the_rest_survive(self, uid):
        """★ 核心语义：一件撞了，其余照常入库。"""
        payloads = [
            ItemCreate(type="tshirt", name="重复甲", image_hash="bulk_dup_key"),
            ItemCreate(type="tshirt", name="重复乙", image_hash="bulk_dup_key"),
            ItemCreate(type="tshirt", name="重复丙", image_hash="bulk_dup_key"),
            ItemCreate(type="jeans", name="正常件"),
        ]
        result = item_svc.create_many(uid, payloads)

        assert result["total"] == 4
        assert result["created"] == 2, "第一件和最后一件应当成功"
        assert result["skipped"] == 2
        assert result["failed"] == 0

    def test_outcome_points_at_the_right_index_and_reason(self, uid):
        """回报必须指明**是第几件、为什么** —— 否则用户不知道该改哪一条。"""
        payloads = [
            ItemCreate(type="tshirt", name="先来的", image_hash="bulk_idx_key"),
            ItemCreate(type="tshirt", name="后来的", image_hash="bulk_idx_key"),
        ]
        result = item_svc.create_many(uid, payloads)
        skipped = [o for o in result["outcomes"] if o["status"] == "skipped"]

        assert len(skipped) == 1
        assert skipped[0]["index"] == 1
        assert skipped[0]["name"] == "后来的"
        assert "指纹重复" in skipped[0]["reason"]
        assert skipped[0]["item_id"] is None

    def test_success_outcomes_carry_the_new_id(self, uid):
        result = item_svc.create_many(uid, [ItemCreate(type="shirt", name="带id")])
        created = result["outcomes"][0]
        assert created["status"] == "created"
        assert created["item_id"]
        assert item_svc.get(created["item_id"], uid) is not None

    def test_failed_is_counted_separately_from_skipped(self, uid, monkeypatch):
        """「重复被跳过」和「数据出错」的处理方式不同，必须分开计数。"""
        original = item_svc.create

        def flaky(user_id, payload):
            if payload.name == "坏数据":
                raise RuntimeError("模拟的未知错误")
            return original(user_id, payload)

        monkeypatch.setattr(item_svc, "create", flaky)

        payloads = [
            ItemCreate(type="tshirt", name="好数据"),
            ItemCreate(type="tshirt", name="坏数据"),
        ]
        result = item_svc.create_many(uid, payloads)

        assert result["created"] == 1
        assert result["failed"] == 1
        assert result["skipped"] == 0
        assert "RuntimeError" in result["outcomes"][1]["reason"]
