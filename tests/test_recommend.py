"""`recommend` 的单元测试。

四件事必须钉死：

1. **产出的每个 `item_id` 都真实存在** —— 这是防幻觉的底线
2. **没有模型也要能用**（降级为本地排序，而不是抛异常）
3. **模型给出的顺序被尊重** —— 那是它这一趟唯一的产出
4. 模型给出越界编号时**丢弃并补齐**，不是报错
"""

from __future__ import annotations

from app import db
from app.models import ItemCreate
from app.services import items as item_svc
from app.services import recommend
from app.services.llm.client import LLMError


def _seed(uid: str, tops: int = 3, bottoms: int = 3, shoes: int = 2) -> list[str]:
    """造一个最小可用衣橱。

    ⚠️ **刻意不标 `season`** —— 未标注视为四季可用，这样测试与当前月份无关。
    （P0 踩过一次：测试里硬编码 `summer`，到了秋天全红。）
    """
    made: list[str] = []
    for n in range(tops):
        made.append(
            item_svc.create(
                uid, ItemCreate(type="tshirt", name=f"上衣{n}", primary_color="black")
            )["id"]
        )
    for n in range(bottoms):
        made.append(
            item_svc.create(
                uid, ItemCreate(type="jeans", name=f"裤子{n}", primary_color="blue")
            )["id"]
        )
    for n in range(shoes):
        made.append(
            item_svc.create(
                uid, ItemCreate(type="sneakers", name=f"鞋{n}", primary_color="white")
            )["id"]
        )
    return made


def _new_user() -> str:
    """造一个干净的临时用户（用于"空衣橱"这类需要隔离的测试）。"""
    user_id = db.new_id("u")
    with db.conn() as c:
        c.execute(
            "INSERT INTO users(id,name,created_at) VALUES(?,?,?)",
            (user_id, "临时用户", db.now_iso()),
        )
    return user_id


class TestScoring:
    def test_item_score_is_the_mean_of_matched_tags(self):
        index = {("color", "black"): 0.8, ("slot", "top"): 0.2}
        item = {"id": "x", "primary_color": "black", "colors": ["black"], "slot": "top"}
        assert abs(recommend.item_score(item, index) - 0.5) < 1e-9

    def test_item_without_any_match_scores_zero(self):
        assert recommend.item_score({"id": "x"}, {}) == 0.0

    def test_normalize_flat_input_gives_half(self):
        """没有区分度时一律 0.5，而不是 0（0 会被误读成"很差"）。"""
        assert recommend._normalize([0.7, 0.7, 0.7]) == [0.5, 0.5, 0.5]

    def test_normalize_maps_to_unit_range(self):
        assert recommend._normalize([0.0, 1.0, 2.0]) == [0.0, 0.5, 1.0]

    def test_normalize_empty(self):
        assert recommend._normalize([]) == []


class TestSimilarity:
    def test_identical_outfits_are_maximally_similar(self):
        a = {"item_ids": ["i1", "i2"], "items": []}
        assert recommend.similarity(a, a) == 1.0

    def test_disjoint_outfits_are_dissimilar(self):
        a = {"item_ids": ["i1"], "items": []}
        b = {"item_ids": ["i2"], "items": []}
        assert recommend.similarity(a, b) == 0.0

    def test_color_overlap_alone_counts(self):
        """单品不重合，但主色撞了 —— 也该算有相似度。"""
        a = {"item_ids": ["i1"], "items": [{"primary_color": "black"}, {"primary_color": "white"}]}
        b = {"item_ids": ["i2"], "items": [{"primary_color": "black"}, {"primary_color": "gray"}]}
        assert recommend.similarity(a, b) > 0

    def test_empty_outfits_do_not_crash(self):
        empty = {"item_ids": [], "items": []}
        assert recommend.similarity(empty, empty) == 0.0


class TestMMR:
    def test_returns_requested_count(self):
        cands = [{"item_ids": [f"i{n}"], "_relevance": 1.0 - n * 0.1} for n in range(5)]
        assert len(recommend.mmr_select(cands, 3)) == 3

    def test_count_larger_than_pool(self):
        assert len(recommend.mmr_select([{"item_ids": ["a"], "_relevance": 1.0}], 5)) == 1

    def test_zero_count(self):
        assert recommend.mmr_select([{"item_ids": ["a"], "_relevance": 1.0}], 0) == []

    def test_diversity_beats_a_near_duplicate(self):
        """相关性只差一点点时，应该选完全不重合的那套，而不是高度重合的。

        这里 b 的相关性（0.97）比 c（0.90）高，但 b 与已选的 a 重合一半 ——
        MMR 应当把 c 排在前面。这就是"多样性并进 MMR"的意义。
        """
        a = {"item_ids": ["i1", "i2"], "_relevance": 1.00}
        b = {"item_ids": ["i1", "i3"], "_relevance": 0.97}
        c = {"item_ids": ["i8", "i9"], "_relevance": 0.90}

        picked = recommend.mmr_select([a, b, c], 2)

        assert picked[0] is a
        assert picked[1] is c, "应优先选不重合的 c，而不是高度重合的 b"


class TestRecommend:
    def test_local_path_produces_outfits_without_a_model(self, uid):
        _seed(uid)
        result = recommend.recommend(uid, limit=3, use_llm=False)
        assert result["candidates"] > 0
        assert len(result["outfits"]) == 3
        assert all(o["reason"] is None for o in result["outfits"])
        assert result["degraded"] is False

    def test_every_item_id_really_exists(self, uid):
        """★ 防幻觉的底线断言。"""
        _seed(uid)
        result = recommend.recommend(uid, limit=3, use_llm=False)
        for outfit in result["outfits"]:
            assert outfit["item_ids"], "空搭配不该出现"
            with db.conn() as c:
                for item_id in outfit["item_ids"]:
                    row = c.execute(
                        "SELECT id FROM clothing_items WHERE id=?", (item_id,)
                    ).fetchone()
                    assert row is not None, f"{item_id} 在库里不存在 —— 幻觉漏进来了"

    def test_outfits_are_persisted_as_proposed(self, uid):
        _seed(uid)
        result = recommend.recommend(uid, limit=2, use_llm=False)
        with db.conn() as c:
            for outfit in result["outfits"]:
                row = c.execute(
                    "SELECT status, generated_by FROM outfits WHERE id=?", (outfit["id"],)
                ).fetchone()
                assert row is not None
                assert row["status"] == "proposed"
                assert row["generated_by"] == "llm"

    def test_model_order_is_honored(self, uid, fake_llm):
        """模型排在第一位的那套，必须真的排在第一位。"""
        _seed(uid)
        client = fake_llm([{"ranked": [{"outfit": "O2", "reason": "二号最合适"}]}])
        result = recommend.recommend(uid, limit=1, client=client)
        assert result["degraded"] is False
        assert len(result["outfits"]) == 1
        assert result["outfits"][0]["reason"] == "二号最合适"

    def test_out_of_range_rank_is_dropped_and_backfilled(self, uid, fake_llm):
        """模型编了个不存在的编号 → 丢弃、用本地顺序补齐、**不报错**。"""
        _seed(uid)
        client = fake_llm([{"ranked": [{"outfit": "O999", "reason": "我编的"}]}])
        result = recommend.recommend(uid, limit=2, client=client)
        assert len(result["outfits"]) == 2
        assert all(o["reason"] is None for o in result["outfits"])

    def test_model_failure_degrades_instead_of_raising(self, uid, fake_llm):
        """没有 key / 模型挂了，仍然要能给出搭配 —— 本地排序本身就基于用户偏好。"""
        _seed(uid)
        client = fake_llm(error=LLMError("模型炸了"))
        result = recommend.recommend(uid, limit=2, client=client)
        assert result["degraded"] is True
        assert "模型不可用" in (result["note"] or "")
        assert len(result["outfits"]) == 2

    def test_empty_wardrobe_returns_empty_with_an_explanation(self):
        result = recommend.recommend(_new_user(), limit=3, use_llm=False)
        assert result["outfits"] == []
        assert result["degraded"] is False
        assert "没有可搭出的组合" in (result["note"] or "")

    def test_suggestion_count_is_recorded(self):
        """推荐过就要记账 —— 采纳率指标（设计文档 §7）依赖 `suggestion_count`。

        用**干净的用户**跑：共享 `uid` 的库里已经被其它测试造了几十件单品，
        而候选枚举是深度优先的（上装在最外层），新造的上装会排在 200 个候选之外，
        于是这条断言会假失败。**这是测试隔离问题，不是计数没生效。**

        （顺带记一条真问题：那个深度优先的枚举顺序本身就会让候选池偏斜 ——
        已记进 `docs/tasks.md` 的技术债，留给 W4 审查后统一处理。）
        """
        fresh = _new_user()
        ids = _seed(fresh)

        recommend.recommend(fresh, limit=1, use_llm=False)

        with db.conn() as c:
            counts = [
                c.execute(
                    "SELECT suggestion_count FROM clothing_items WHERE id=?", (item_id,)
                ).fetchone()[0]
                for item_id in ids
            ]
        assert any(count > 0 for count in counts)
