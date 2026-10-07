"""P1 收尾：三条技术债的回归测试。

这三条都是「错了不会当场炸」的类型 —— 没有测试钉住，就会在某个时点悄悄回来：

1. `pattern` 词表分散在两处 → 口径会漂
2. 口语鞋名召回缺口 → 鞋落 `accessory`，从组合计算里消失
3. 候选池深度优先偏斜 → **新录入的单品永远进不了推荐**（影响推荐质量）

⚠️ 临时库是**所有测试文件共享**的，所以这里一律用独立用户，
不然后面那些 `len(sample) == N` 的断言会被别的文件造的单品污染。
"""

from __future__ import annotations

import pytest

from app import db, taxonomy
from app.models import ItemCreate
from app.services import items as item_svc
from app.services import tagging, wardrobe


def _new_user() -> str:
    """造一个干净的临时用户。"""
    user_id = db.new_id("u")
    with db.conn() as c:
        c.execute(
            "INSERT INTO users(id,name,created_at) VALUES(?,?,?)",
            (user_id, "收尾测试用户", db.now_iso()),
        )
    return user_id


def _seed(uid: str, *, tops: int, bottoms: int, shoes: int) -> None:
    """⚠️ 刻意不标 `season` —— 未标注视为四季可用，这样测试与当前月份无关。"""
    for n in range(tops):
        item_svc.create(uid, ItemCreate(type="tshirt", name=f"上装{n}"))
    for n in range(bottoms):
        item_svc.create(uid, ItemCreate(type="jeans", name=f"下装{n}"))
    for n in range(shoes):
        item_svc.create(uid, ItemCreate(type="sneakers", name=f"鞋{n}"))


# ---------------------------------------------------------------------------
# 技术债 1：pattern 词表已上收到 taxonomy，且只有这一份
# ---------------------------------------------------------------------------

class TestPatternVocabulary:
    def test_lives_in_taxonomy_and_is_registered(self):
        assert taxonomy.VOCABULARIES.get("pattern") is taxonomy.PATTERNS

    def test_tagging_keeps_no_second_copy(self):
        """两份口径的根源就是各自维护一份 —— 这里钉死它只有一份。"""
        assert not hasattr(tagging, "PATTERNS")

    def test_normalization_still_resolves(self):
        clean, failed = tagging.normalize_tags({"type": "shirt", "pattern": "条纹"})
        assert clean["pattern"] == "striped"
        assert "pattern" not in failed


# ---------------------------------------------------------------------------
# 技术债 2：口语鞋名的召回
# ---------------------------------------------------------------------------

class TestColloquialShoeNames:
    @pytest.mark.parametrize(
        "word,expected",
        [
            ("小白鞋", "sneakers"),
            ("帆布鞋", "sneakers"),
            ("老爹鞋", "sneakers"),
            ("跑鞋", "sneakers"),
            ("马丁靴", "boots"),
            ("雪地靴", "boots"),
            ("高跟鞋", "other_shoes"),
            ("人字拖", "slippers"),
        ],
    )
    def test_resolves_into_shoes_slot(self, word, expected):
        key = taxonomy.resolve_type(word)
        assert key == expected
        assert taxonomy.slot_of(key) == "shoes"

    def test_none_of_them_collapse_into_accessory(self):
        """原坑：落 `other` → 槽位变 `accessory` → 鞋从组合计算里消失。"""
        bad = [
            w
            for w in ("小白鞋", "帆布鞋", "老爹鞋", "高跟鞋", "马丁靴")
            if taxonomy.slot_of(taxonomy.resolve_type(w)) != "shoes"
        ]
        assert bad == []


# ---------------------------------------------------------------------------
# 技术债 3：候选池不再深度优先偏斜
# ---------------------------------------------------------------------------

class TestCandidatePoolBias:
    def test_full_enumeration_covers_every_item(self):
        """`limit=None` 要覆盖每一件单品 —— 推荐走的正是这条路径。"""
        uid = _new_user()
        _seed(uid, tops=10, bottoms=5, shoes=3)

        preview = wardrobe.combinations_preview(uid, limit=None)

        assert preview["considered"] == 150
        assert preview["truncated"] is False
        sampled = {oid for o in preview["sample"] for oid in o["item_ids"]}
        all_ids = {i["id"] for i in item_svc.iter_all_items(uid)}
        assert all_ids <= sampled, "全量枚举必须覆盖每一件单品"

    def test_small_sample_still_spans_many_tops(self):
        """旧的深度优先下，20 个样本只会用到前 2 件上装（15 + 5）。"""
        uid = _new_user()
        _seed(uid, tops=10, bottoms=5, shoes=3)
        by_id = {i["id"]: i for i in item_svc.iter_all_items(uid)}

        preview = wardrobe.combinations_preview(uid, limit=20)

        assert len(preview["sample"]) == 20
        tops = {
            oid
            for o in preview["sample"]
            for oid in o["item_ids"]
            if by_id[oid]["slot"] == "top"
        }
        assert len(tops) >= 5, f"20 个样本只用到 {len(tops)} 件上装 —— 偏斜回来了"

    def test_per_slot_caps_the_pool_but_not_the_truth(self):
        uid = _new_user()
        _seed(uid, tops=20, bottoms=1, shoes=1)

        preview = wardrobe.combinations_preview(
            uid, limit=None, per_slot=5, score_of=lambda it: 0.0
        )

        assert preview["considered"] == 5            # 枚举空间被截到 5
        assert preview["total_combinations"] == 20   # 但真实组合数没被骗
        assert len(preview["sample"]) == 5

    def test_per_slot_keeps_the_newest_on_ties(self):
        """冷启动全是 0 分时，**最新录入的那件不该被截掉**。

        这条是"新单品永远进不了候选池"这个原坑的直接回归。
        """
        uid = _new_user()
        _seed(uid, tops=3, bottoms=1, shoes=1)

        # 手工错开 created_at —— `now_iso()` 是秒精度，同秒录入分不出先后
        top_ids = [i["id"] for i in item_svc.iter_all_items(uid) if i["slot"] == "top"]
        assert len(top_ids) == 3
        for n, iid in enumerate(top_ids):
            with db.conn() as c:
                c.execute(
                    "UPDATE clothing_items SET created_at=? WHERE id=?",
                    (f"2026-01-0{n + 1}T00:00:00+08:00", iid),
                )

        preview = wardrobe.combinations_preview(
            uid, limit=None, per_slot=1, score_of=lambda it: 0.0
        )
        sampled = {oid for o in preview["sample"] for oid in o["item_ids"]}

        assert top_ids[2] in sampled, "最新的那件被截掉了 —— 同分时没有优先新的"
        assert top_ids[0] not in sampled
