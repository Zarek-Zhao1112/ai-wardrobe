"""preference.py 的单元测试。

用法（在项目根目录执行）：
    .venv/Scripts/python.exe -m pytest tests/test_preference.py -q

设计要点：
- **纯函数部分不碰库**，直接断言公式 —— 衰减 / 增量更新 / 置信度这三处
  错了会**静默**（算出来是个正常数，但不对），必须逐个钉死
- 落库部分用临时库，绝不碰 `data/wardrobe.db`
- 有一组测试专门盯「分桶口径必须与 taxonomy 一致」—— 两边分裂会让
  组合计算和偏好计算各说各话

环境隔离（`WARDROBE_DB`）与 `db.init()` 都在 `tests/conftest.py` ——
那里是唯一可靠的位置，因为 `db.py` 在 **import 期**就把 `DB_PATH` 定死了。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app import db, taxonomy
from app.services import preference as P

TOL = 1e-6


# ---------------------------------------------------------------------------
# 一、衰减
# ---------------------------------------------------------------------------

class TestDecay:
    def test_zero_elapsed_is_no_decay(self):
        assert P.decay_weight(0) == 1.0

    def test_one_half_life_halves(self):
        assert abs(P.decay_weight(P.HALF_LIFE_DAYS) - 0.5) < TOL

    def test_design_target_90_days_is_quarter(self):
        """设计目标：90 天后权重降到 1/4。这条钉死了 H=45 这个推导。"""
        assert abs(P.decay_weight(90) - 0.25) < TOL

    def test_negative_dt_does_not_decay(self):
        """时钟被改到过去时不许凭空扣分。"""
        assert P.decay_weight(-30) == 1.0

    def test_elapsed_days_tolerates_garbage(self):
        assert P.elapsed_days(None) == 0.0
        assert P.elapsed_days("不是时间") == 0.0

    def test_elapsed_days_reads_iso_with_offset(self):
        now = datetime.now().astimezone()
        past = now - timedelta(days=10)
        assert abs(P.elapsed_days(past.isoformat(), now) - 10) < 0.01


# ---------------------------------------------------------------------------
# 二、增量更新（公式错了会静默，这里逐项钉）
# ---------------------------------------------------------------------------

class TestUpdateScore:
    def test_first_evidence_becomes_the_score(self):
        score, n = P.update_score(0.0, 0.0, 1.0, 2.0, 0.0)
        assert abs(score - 1.0) < TOL
        assert abs(n - 2.0) < TOL

    def test_opposite_evidence_cancels(self):
        """喜欢 2 条之后否定 2 条 → 归零，证据量累到 4。"""
        score, n = P.update_score(1.0, 2.0, -1.0, 2.0, 0.0)
        assert abs(score - 0.0) < TOL
        assert abs(n - 4.0) < TOL

    def test_stale_evidence_loses_its_grip(self):
        """老证据随时间淡化 → 同样一条反向证据，**隔得越久越能扭转结论**。

        这条钉的是衰减的**方向**。写反的后果很隐蔽：
        系统会变成"越久以前喜欢过就越改不掉"，用户永远无法推翻旧偏好。
        """
        fresh, n_fresh = P.update_score(1.0, 2.0, -1.0, 2.0, 0.0)
        stale, n_stale = P.update_score(1.0, 2.0, -1.0, 2.0, P.HALF_LIFE_DAYS)
        assert stale < fresh
        assert n_stale < n_fresh
        assert stale >= -1.0, "反向证据再强也不该越过 -1"

    def test_hand_computed_value(self):
        """手算校验：w=0.5, score=(1*0.5*2 + (-1)*2)/(2*0.5+2) = -1/3。"""
        score, n = P.update_score(1.0, 2.0, -1.0, 2.0, P.HALF_LIFE_DAYS)
        assert abs(score - (-1.0 / 3.0)) < TOL
        assert abs(n - 3.0) < TOL

    def test_score_stays_in_range(self):
        score, _ = P.update_score(1.0, 10.0, 1.0, 2.0, 0.0)
        assert -1.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# 三、置信度与冷启动
# ---------------------------------------------------------------------------

class TestConfidence:
    def test_no_evidence_no_confidence(self):
        assert P.confidence(0, 0.0) == 0.0

    def test_cold_start_is_capped(self):
        """n < 5 时不许显得自信。"""
        for n in (0, 1, 2.5, 4):
            assert P.confidence(n, 1.0) <= P.MIN_CONF_CAP + TOL

    def test_mature_bucket_reaches_high(self):
        assert abs(P.confidence(100, 1.0) - (100 / 105)) < TOL

    def test_neutral_score_halves_the_confidence(self):
        assert abs(P.confidence(100, 0.0) - (100 / 105) * 0.5) < TOL

    def test_symmetry_on_polarity(self):
        assert abs(P.confidence(30, -0.8) - P.confidence(30, 0.8)) < TOL

    def test_explore_epsilon_three_tiers(self):
        assert P.explore_epsilon(0) is None
        assert P.explore_epsilon(4.9) is None
        assert P.explore_epsilon(5) == P.EPS_WARM
        assert P.explore_epsilon(19.9) == P.EPS_WARM
        assert P.explore_epsilon(20) == P.EPS_COLD


# ---------------------------------------------------------------------------
# 四、分桶
# ---------------------------------------------------------------------------

class TestBuckets:
    def test_season_merge(self):
        assert P.season_bucket("summer") == "summer"
        assert P.season_bucket("winter") == "winter"
        assert P.season_bucket("spring") == "transition"
        assert P.season_bucket("autumn") == "transition"

    def test_unknown_season_falls_to_all(self):
        assert P.season_bucket(None) == "all"
        assert P.season_bucket("") == "all"
        assert P.season_bucket("夏") == "all"  # 中文正名不在这里处理，避免第二套口径

    def test_scene_merge_follows_user_decision(self):
        """2026-10-03 拍板：date/party/travel/school 归日常。"""
        for s in ("daily", "date", "party", "travel", "school"):
            assert P.scene_bucket(s) == "daily"
        assert P.scene_bucket("work") == "work"
        assert P.scene_bucket("formal") == "formal"
        assert P.scene_bucket("sport") == "sport"
        assert P.scene_bucket("home") == "home"

    def test_multi_scene_takes_priority(self):
        assert P.scene_bucket(["daily", "work"]) == "work"
        assert P.scene_bucket(["daily", "formal", "work"]) == "formal"

    def test_scene_bucket_covers_taxonomy(self):
        """**口径一致性**：taxonomy 里出现的每个 scene 都必须能归并，否则会静默落 all。"""
        missing = set(taxonomy.SCENES) - set(P.SCENE_BUCKETS)
        assert not missing, f"这些 scene 没有归并规则：{sorted(missing)}"

    def test_season_bucket_covers_taxonomy(self):
        missing = set(taxonomy.SEASONS) - set(P.SEASON_BUCKETS)
        assert not missing, f"这些 season 没有归并规则：{sorted(missing)}"

    def test_bucket_names_are_closed_sets(self):
        for name in P.SEASON_BUCKETS.values():
            assert name in P.SEASON_BUCKET_NAMES
        for name in P.SCENE_BUCKETS.values():
            assert name in P.SCENE_BUCKET_NAMES

    def test_bucket_mapping_is_idempotent(self):
        """★ 回归：桶名再喂进来必须原样返回。

        少了这条保证，`context_key(season_of_day(d), scene)` 会把季节映射两次
        （`autumn → transition → all`），**所有反馈都挤进 `global` 桶**，
        而且不报任何错 —— 分桶静默失效。（W3 集成时实测踩到。）
        """
        assert P.season_bucket("transition") == "transition"
        assert P.season_bucket("summer") == "summer"
        assert P.season_bucket("all") == "all"
        assert P.scene_bucket("daily") == "daily"
        assert P.scene_bucket("all") == "all"

    def test_season_of_day_returns_calendar_season(self):
        """`season_of_day` 必须返回**日历季节**，归并交给 `context_key` 做。"""
        from datetime import date

        assert P.season_of_day(date(2026, 10, 3)) == "autumn"
        assert P.season_of_day(date(2026, 7, 1)) == "summer"

        # 完整链路：秋天的反馈要落在 transition 桶，**不是** global
        key = P.context_key(P.season_of_day(date(2026, 10, 3)), None)
        assert key == "transition|all"

    def test_context_key_composition(self):
        assert P.context_key("summer", "daily") == "summer|daily"
        assert P.context_key("summer") == "summer|all"
        assert P.context_key(None, "work") == "all|work"
        assert P.context_key() == "global"

    def test_split_context_key(self):
        assert P.split_context_key("summer|daily") == ("summer", "daily")
        assert P.split_context_key("global") == ("all", "all")

    def test_fallback_chain_full(self):
        assert P.fallback_chain("summer|daily") == [
            "summer|daily", "summer|all", "all|daily", "global",
        ]

    def test_fallback_chain_never_contains_all_all(self):
        for key in ("summer|daily", "summer|all", "all|daily", "global"):
            assert "all|all" not in P.fallback_chain(key)
            assert "global" in P.fallback_chain(key)

    def test_fallback_chain_is_deduped(self):
        for key in ("summer|all", "global"):
            chain = P.fallback_chain(key)
            assert len(chain) == len(set(chain))


# ---------------------------------------------------------------------------
# 五、从单品提取标签
# ---------------------------------------------------------------------------

class TestTagsOfItem:
    def test_extracts_expected_tags(self):
        item = {
            "colors": ["black"],
            "primary_color": "black",
            "style": ["minimal", "casual"],
            "fit": "loose",
            "material": "cotton",
            "pattern": "solid",
            "formality": "casual",
            "slot": "top",
            "scene": ["work", "daily"],
        }
        tags = P.tags_of_item(item)
        assert ("color", "black") in tags
        assert ("style", "minimal") in tags
        assert ("style", "casual") in tags
        assert ("fit", "loose") in tags
        assert ("material", "cotton") in tags
        assert ("slot", "top") in tags
        assert len(tags) == len(set(tags)), "不该有重复标签"

    def test_scene_is_stored_as_merged_occasion(self):
        """存归并后的桶名，否则表里会同时出现 date 和 daily 两套口径。"""
        tags = P.tags_of_item({"scene": ["date", "party"]})
        assert ("occasion", "daily") in tags
        assert not any(v == "date" for _, v in tags)

    def test_empty_item_yields_nothing(self):
        assert P.tags_of_item({}) == []

    def test_none_values_are_skipped(self):
        tags = P.tags_of_item({"fit": None, "material": "", "colors": []})
        assert tags == []


# ---------------------------------------------------------------------------
# 六、落库与分层回退
# ---------------------------------------------------------------------------

def _many(uid: str, tag_type: str, tag_value: str, times: int, r: float, ctx) -> None:
    for _ in range(times):
        P.record(uid, tag_type, tag_value, r, context=ctx)


class TestRecordAndQuery:
    def test_first_record_creates_row(self, uid):
        row = P.record(uid, "fit", "slim", 1.0, context="summer|daily")
        assert row["evidence_count"] == int(P.K_EXPLICIT)
        assert abs(row["score"] - 1.0) < TOL
        assert row["context_key"] == "summer|daily"

    def test_repeated_record_moves_score(self, uid):
        P.record(uid, "fit", "oversize", 1.0, context="winter|work")
        P.record(uid, "fit", "oversize", 1.0, context="winter|work")
        after_like = P.record(uid, "fit", "oversize", 1.0, context="winter|work")
        assert after_like["evidence_count"] == 6
        assert after_like["score"] > 0

        after_dislike = P.record(uid, "fit", "oversize", -1.0, context="winter|work")
        assert after_dislike["score"] < after_like["score"]
        assert after_dislike["evidence_count"] == 8

    def test_pos_neg_weights_are_maintained(self, uid):
        P.record(uid, "pattern", "striped", 1.0, context="global")
        P.record(uid, "pattern", "striped", -1.0, context="global")
        with db.conn() as c:
            row = c.execute(
                "SELECT pos_weight, neg_weight FROM preference_memory "
                "WHERE user_id=? AND tag_type='pattern' AND tag_value='striped'",
                (uid,),
            ).fetchone()
        assert row["pos_weight"] > 0
        assert row["neg_weight"] > 0

    def test_falls_back_to_coarse_bucket(self, uid):
        """只在 `global` 有证据，用细桶查也能拿到 —— 这是分层回退的意义。"""
        _many(uid, "material", "linen", 3, 1.0, "global")
        found = P.query(uid, context=P.context_key("summer", "daily"))
        hit = [d for d in found if d["tag_value"] == "linen"]
        assert len(hit) == 1
        assert hit[0]["evidence_count"] >= P.MIN_EVIDENCE

    def test_prefers_fine_bucket_over_global(self, uid):
        """细桶证据够时不走回退。"""
        _many(uid, "color", "olive", 3, 1.0, "summer|daily")     # 细桶 6 条
        P.record(uid, "color", "olive", -1.0, context="global")   # 全局 2 条
        hit = [d for d in P.query(uid, context="summer|daily") if d["tag_value"] == "olive"]
        assert len(hit) == 1
        assert hit[0]["context_key"] == "summer|daily"
        assert hit[0]["score"] > 0

    def test_per_tag_fallback(self, uid):
        """逐 tag 回退：一个 tag 命中细桶、另一个退回 global，同一次查询都要拿到。"""
        _many(uid, "color", "beige", 3, 1.0, "summer|work")   # 细桶够
        _many(uid, "style", "vintage", 3, 1.0, "global")      # 只有全局够

        found = P.query(uid, context="summer|work")
        by_value = {d["tag_value"]: d for d in found}
        assert by_value["beige"]["context_key"] == "summer|work"
        assert by_value["vintage"]["context_key"] == "global"

    def test_weak_evidence_is_kept_but_discounted(self, uid):
        """证据不足的桶不该被丢掉，只该被降权（交给 confidence）。"""
        P.record(uid, "style", "korean", 1.0, context="all|home")
        found = P.query(uid, context="all|home", tag_type="style")
        hit = [d for d in found if d["tag_value"] == "korean"]
        assert len(hit) == 1
        assert hit[0]["confidence"] <= P.MIN_CONF_CAP + TOL

    def test_query_filters_by_tag_type(self, uid):
        found = P.query(uid, context="summer|daily", tag_type="color")
        assert all(d["tag_type"] == "color" for d in found)

    def test_query_sorted_by_confidence_desc(self, uid):
        found = P.query(uid, context="summer|daily")
        confs = [d["confidence"] for d in found]
        assert confs == sorted(confs, reverse=True)

    def test_reading_projects_decay(self, uid):
        """写入时用 45 天前的时间，读出来应该是原分数的一半。"""
        past = datetime.now().astimezone() - timedelta(days=P.HALF_LIFE_DAYS)
        P.record(uid, "fit", "tight", 1.0, context="transition|formal", when=past)
        found = P.query(uid, context="transition|formal", tag_type="fit")
        hit = [d for d in found if d["tag_value"] == "tight"]
        assert len(hit) == 1
        assert abs(hit[0]["decayed_score"] - hit[0]["score"] * 0.5) < 0.01

    def test_returned_shape_matches_preference_out(self, uid):
        """返回的键必须能直接喂给 models.PreferenceOut。"""
        from app.models import PreferenceOut

        found = P.query(uid, context="summer|daily")
        assert found, "前置数据没准备好"
        for row in found:
            PreferenceOut(**row)  # 字段缺失或多余都会在这里炸
