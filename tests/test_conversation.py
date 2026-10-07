"""会话状态机的测试。

分两层：

- **纯函数**（意图识别 / 序号解析 / 属性抽取）—— 不碰库不碰网络，断言确定
- **端到端**（`handle` 走完整流程）—— 用**独立用户 + 独立 peer**

⚠️ 这一层盯的是**静默错配**：用户说"第 2 套"记到了别的搭配上、
说"对"却没入库、草稿被静默丢弃……这些都不会报错，只会让用户觉得"它没懂我"。

---

**为什么端到端测试必须用 `fresh` fixture**：`conversation.handle` 只收一个
`InboundMessage`，用户是它**自己**从 `db.get_default_user_id()` 取的 ——
所以测试没法靠传参隔离，只能打补丁。

不打补丁的后果实测过：`test_no_wardrobe_gives_actionable_reply` 期望空衣橱，
却拿到了别的测试造出来的 3 套搭配（临时库是 session 级共享的）。
"""

from __future__ import annotations

import json

import pytest

from app import db
from app.channels import InboundMessage
from app.services import conversation as C
from app.services import items as item_svc


@pytest.fixture
def fresh(monkeypatch):
    """独立用户 + 独立 peer。返回 `(user_id, peer_id)`。"""
    from app.models import ItemCreate  # noqa: F401  (确认模型可导入)

    user_id = db.new_id("u")
    with db.conn() as c:
        c.execute(
            "INSERT INTO users(id,name,created_at) VALUES(?,?,?)",
            (user_id, "会话测试用户", db.now_iso()),
        )
    monkeypatch.setattr(db, "get_default_user_id", lambda: user_id)
    peer = f"peer_{db.new_id('p')}@im.wechat"
    return user_id, peer


def _msg(text: str, peer: str = "u_test@im.wechat") -> InboundMessage:
    return InboundMessage(peer_id=peer, text=text, context_token="ct_test")


def _state_of(peer: str) -> dict:
    with db.conn() as c:
        row = c.execute(
            "SELECT state FROM conversation_state WHERE channel=? AND peer_id=?",
            (C.CHANNEL, peer),
        ).fetchone()
    return json.loads(row["state"]) if row else {}


def _seed_wardrobe(uid: str, n_tops: int = 3, n_bottoms: int = 3) -> list[str]:
    """凑一个能出搭配的最小衣橱。

    ⚠️ 刻意**不标 season** —— 未标注视为四季可用，测试因此与当前月份无关。
    """
    from app.models import ItemCreate

    ids: list[str] = []
    for i in range(n_tops):
        ids.append(item_svc.create(uid, ItemCreate(
            type="tshirt", name=f"上衣{i}", colors=["white"], primary_color="white"))["id"])
    for i in range(n_bottoms):
        ids.append(item_svc.create(uid, ItemCreate(
            type="jeans", name=f"裤子{i}", colors=["navy"], primary_color="navy"))["id"])
    ids.append(item_svc.create(uid, ItemCreate(
        type="sneakers", name="鞋", colors=["white"], primary_color="white"))["id"])
    return ids


# ---------------------------------------------------------------------------
# 纯函数
# ---------------------------------------------------------------------------


class TestIntentClassification:
    """★ 顺序即语义 —— 同一个词在不同上下文里是不同意图。"""

    @pytest.mark.parametrize(
        "text,ctx,expect",
        [
            ("对", {"has_draft": True}, "confirm"),
            ("好的", {"has_draft": True}, "confirm"),
            # 同一个「可以」：有草稿时是"确认草稿"，有候选时是"选这套"
            ("可以", {"has_draft": True}, "confirm"),
            ("可以", {"has_outfits": True}, "like"),
            # 修正草稿的三种说法
            ("颜色是深蓝", {"has_draft": True}, "correct"),
            ("材质改成棉", {"has_draft": True}, "correct"),
            ("是深蓝的", {"has_draft": True}, "correct"),
            # 但「我有一件…」是**新的一件**，不是修正
            ("我有一件深灰宽松卫衣", {"has_draft": True}, "describe"),
            # 反馈
            ("2", {"has_outfits": True}, "like"),
            ("第2套", {"has_outfits": True}, "like"),
            ("2不行", {"has_outfits": True}, "dislike"),
            ("第2套不行", {"has_outfits": True}, "dislike"),
            ("这套行", {"has_outfits": True}, "like"),
            ("换一套", {"has_outfits": True}, "swap"),
            # 无上下文
            ("今天穿什么", {}, "match"),
            ("这件怎么搭", {}, "match"),
            ("缺什么", {}, "gap"),
            ("想买这件适合我吗", {}, "buy"),
            ("我有一件深灰宽松卫衣", {}, "describe"),
            ("随便说点什么", {}, "unknown"),
            ("", {}, "unknown"),
        ],
    )
    def test_cases(self, text, ctx, expect):
        assert C.classify_intent(text, **ctx) == expect

    def test_index_like_only_fires_with_outfits(self):
        """没有候选时，光回个「2」不该被当成反馈（否则会指到空气上）。"""
        assert C.classify_intent("2", has_outfits=False) == "unknown"


class TestParseIndex:
    @pytest.mark.parametrize(
        "text,expect",
        [("2", 1), ("第2套", 1), ("二", 1), ("第二套", 1),
         ("第 3 套", 2), ("2套", 1), ("3", 2)],
    )
    def test_valid(self, text, expect):
        assert C.parse_index(text, 3) == expect

    @pytest.mark.parametrize("text", ["5", "0", "", "第9套", "随便"])
    def test_invalid_or_out_of_range(self, text):
        assert C.parse_index(text, 3) is None


class TestExtractFields:
    def test_basic(self):
        fields, _ = C.extract_fields("我有一件深灰宽松卫衣")
        assert fields["type"] == "hoodie"
        assert fields["colors"] == ["gray"]
        assert fields["fit"] == "loose"

    def test_type_word_does_not_leak_into_material(self):
        """★ 回归：`牛仔裤` 里含 `牛仔`，而 `牛仔` 是材质词表的 denim。

        实测过：不加防护时「刚买一条牛仔裤」被抽成 `material: denim` —— 会**写坏数据**。
        """
        fields, _ = C.extract_fields("刚买一条牛仔裤")
        assert fields["type"] == "jeans"
        assert "material" not in fields, "品类名里的子串漏进了材质"

    def test_material_still_works_when_it_is_really_material(self):
        """但「牛仔短裤」里的「牛仔」**确实**指材质，不能被过度防护吞掉。"""
        fields, _ = C.extract_fields("一条牛仔短裤")
        assert fields["type"] == "shorts"
        assert fields.get("material") == "denim"

    def test_nothing_recognized(self):
        fields, _ = C.extract_fields("随便说点什么")
        assert fields == {}


class TestDescribeDraft:
    def test_renders_labels_not_keys(self):
        """回显给用户看的必须是中文标签，不是 `hoodie`/`gray` 这种 key。"""
        out = C.describe_draft({"type": "hoodie", "colors": ["gray"], "fit": "loose"})
        assert "卫衣" in out and "灰" in out and "宽松" in out
        assert "hoodie" not in out and "gray" not in out

    def test_empty(self):
        assert "什么都没认出来" in C.describe_draft({})


class TestTimezoneHandling:
    """★ 时区双向归一 —— 这个坑踩过三次了，必须钉住。"""

    def test_aware_stamp_does_not_crash(self):
        """`db.now_iso()` 产出的是带 `+08:00` 的。"""
        assert C._expired(db.now_iso(), C.SESSION_TTL) is False

    def test_naive_stamp_does_not_crash(self):
        """但手工写进库的可能是 naive —— 缺这一半就会抛 TypeError。"""
        assert C._expired("2020-01-01T00:00:00", C.SESSION_TTL) is True
        assert C._expired("2999-01-01T00:00:00", C.SESSION_TTL) is False

    @pytest.mark.parametrize("bad", ["", "不是时间", None])
    def test_garbage_stamp_is_not_expired(self, bad):
        """解析不了就不当过期（宁可多留一会儿，也不要因为脏数据清空会话）。"""
        assert C._expired(bad, C.SESSION_TTL) is False


# ---------------------------------------------------------------------------
# 端到端
# ---------------------------------------------------------------------------


class TestDescribeFlow:
    """场景 C：文字描述 → 草稿 → 确认 → 入库。"""

    def test_describe_creates_draft_not_item(self, fresh):
        uid, peer = fresh
        before = len(item_svc.iter_all_items(uid))
        reply = C.handle(_msg("我有一件深灰宽松卫衣", peer=peer))
        assert "我理解成" in reply
        assert "卫衣" in reply
        # ★ 关键：**没有直接入库**（用户选了"先问一句再入库"）
        assert len(item_svc.iter_all_items(uid)) == before
        assert _state_of(peer)["pending_draft"]["fields"]["type"] == "hoodie"

    def test_confirm_then_creates_item(self, fresh):
        uid, peer = fresh
        C.handle(_msg("我有一件深灰宽松卫衣", peer=peer))
        reply = C.handle(_msg("对", peer=peer))
        assert "已入库" in reply
        assert _state_of(peer)["pending_draft"] is None
        assert any(i["type"] == "hoodie" for i in item_svc.iter_all_items(uid))

    def test_correct_merges_instead_of_replacing(self, fresh):
        """修正只改提到的字段 —— **不能把没提到的属性洗掉**。"""
        _, peer = fresh
        C.handle(_msg("我有一件深灰宽松卫衣", peer=peer))
        C.handle(_msg("颜色是深蓝", peer=peer))
        fields = _state_of(peer)["pending_draft"]["fields"]
        # ⚠️ 期望是 `blue` 而不是 `navy`：词表里"深蓝"这个**口语说法不存在**
        # （只有 `blue`=蓝 / `navy`=藏蓝），子串匹配命中的是「蓝」。
        # 要真正准确得在 `taxonomy` 里补颜色别名 —— 属技术债，见 `docs/tasks.md`。
        assert fields["colors"] == ["blue"]       # 改掉了
        assert fields["fit"] == "loose"           # ★ 还在
        assert fields["type"] == "hoodie"         # ★ 还在

    def test_unrecognized_correction_keeps_draft(self, fresh):
        """★ 用户想修正但没说清楚时，**草稿不能丢** ——
        说不清 ≠ 改主意。原来的实现会丢掉草稿，用户会以为那件衣服没了。"""
        _, peer = fresh
        C.handle(_msg("我有一件深灰宽松卫衣", peer=peer))
        reply = C.handle(_msg("颜色改一下", peer=peer))
        assert _state_of(peer)["pending_draft"] is not None, "草稿被误丢了"
        assert "没听懂" in reply
        assert "卫衣" in reply          # 回显当前草稿，让用户知道还在

    def test_swap_writes_no_feedback(self, fresh):
        """★「换一套」= 这套不合适，但**不等于不喜欢里面的每件** →
        不该写 dislike（那会污染单品偏好），只重新推荐。"""
        uid, peer = fresh
        _seed_wardrobe(uid)
        C.handle(_msg("今天穿什么", peer=peer))
        first_batch = _state_of(peer)["last_outfits"]

        C.handle(_msg("换一套", peer=peer))

        # ⚠️ 必须按**这批搭配**过滤，不能 `COUNT(*) FROM feedback` ——
        # 临时库是 session 级共享的，全表计数会数到别的测试的记录。
        with db.conn() as c:
            placeholders = ",".join("?" * len(first_batch))
            n = c.execute(
                f"SELECT COUNT(*) FROM feedback WHERE outfit_id IN ({placeholders})",
                first_batch,
            ).fetchone()[0]
        assert n == 0, "「换一套」被当成 dislike 写进库了"
        # 而且换完之后应该有一批**新的**候选
        assert _state_of(peer)["last_outfits"] != first_batch

    def test_new_description_abandons_draft_but_says_so(self, fresh):
        """★ 丢弃草稿必须**说出来** —— 静默丢弃会让用户以为那件已入库。"""
        _, peer = fresh
        C.handle(_msg("我有一件深灰宽松卫衣", peer=peer))
        reply = C.handle(_msg("我有一件白色棉T恤", peer=peer))
        assert "放掉了" in reply
        assert _state_of(peer)["pending_draft"]["fields"]["type"] == "tshirt"


class TestMatchFlow:
    """场景 A：问搭配 → 存候选 → 反馈指代。"""

    def test_match_replies_with_numbered_outfits(self, fresh):
        uid, peer = fresh
        _seed_wardrobe(uid)
        reply = C.handle(_msg("今天穿什么", peer=peer))
        assert "1️⃣" in reply
        assert "回「1」" in reply
        assert 1 <= len(_state_of(peer)["last_outfits"]) <= 3

    def test_index_feedback_targets_the_right_outfit(self, fresh):
        """★ 最要紧的一条：回「2」必须记到 `last_outfits[1]` 上。"""
        uid, peer = fresh
        _seed_wardrobe(uid)
        C.handle(_msg("今天穿什么", peer=peer))
        outfits = _state_of(peer)["last_outfits"]

        def n_for(oid: str) -> int:
            # ⚠️ 每次单独开连接。**不能在打开的连接里调 `C.handle`** ——
            # 它内部会自己开 `db.conn()` 写库，而外层的连接因为隔离看不到
            # 内层写的数据（我第一版就是这么写的，结果误报"记到了别的搭配上"）。
            with db.conn() as c:
                return c.execute("SELECT COUNT(*) FROM feedback WHERE outfit_id=?",
                                 (oid,)).fetchone()[0]

        assert all(n_for(o) == 0 for o in outfits)

        C.handle(_msg("2", peer=peer))

        assert n_for(outfits[1]) == 1, "记到了别的搭配上"
        assert n_for(outfits[0]) == 0

    def test_plain_like_targets_first(self, fresh):
        """「这套行」没给序号 → 默认指最推荐那套。"""
        uid, peer = fresh
        _seed_wardrobe(uid)
        C.handle(_msg("今天穿什么", peer=peer))
        outfits = _state_of(peer)["last_outfits"]
        C.handle(_msg("这套行", peer=peer))
        with db.conn() as c:
            assert c.execute("SELECT COUNT(*) FROM feedback WHERE outfit_id=?",
                             (outfits[0],)).fetchone()[0] == 1

    def test_swap_writes_no_feedback(self, fresh):
        """★「换一套」= 这套不合适，但**不等于不喜欢里面的每件** →
        不该写 dislike（那会污染单品偏好），只重新推荐。"""
        uid, peer = fresh
        _seed_wardrobe(uid)
        C.handle(_msg("今天穿什么", peer=peer))
        first_batch = _state_of(peer)["last_outfits"]

        C.handle(_msg("换一套", peer=peer))

        # ⚠️ 必须按**这批搭配**过滤，不能 `COUNT(*) FROM feedback` ——
        # 临时库是 session 级共享的，全表计数会数到别的测试的记录。
        with db.conn() as c:
            placeholders = ",".join("?" * len(first_batch))
            n = c.execute(
                f"SELECT COUNT(*) FROM feedback WHERE outfit_id IN ({placeholders})",
                first_batch,
            ).fetchone()[0]
        assert n == 0, "「换一套」被当成 dislike 写进库了"
        # 而且换完之后应该有一批**新的**候选
        assert _state_of(peer)["last_outfits"] != first_batch

    def test_feedback_without_candidates_asks_instead_of_guessing(self, fresh):
        """★ 没有候选时用户回「1」—— 要**反问**，不能瞎记到某个搭配上。"""
        _, peer = fresh
        reply = C.handle(_msg("1", peer=peer))
        assert "不确定" in reply or "哪套" in reply

    def test_no_wardrobe_gives_actionable_reply(self, fresh):
        _, peer = fresh
        reply = C.handle(_msg("今天穿什么", peer=peer))
        assert "缺什么" in reply or "不够" in reply


class TestGapFlow:
    def test_gap_reports_combinations(self, fresh):
        uid, peer = fresh
        _seed_wardrobe(uid)
        reply = C.handle(_msg("缺什么", peer=peer))
        assert "能搭出" in reply


class TestOtherFlows:
    def test_buy_scene_is_honestly_deferred(self, fresh):
        """不在范围内的场景要**如实说**，不能假装能做。"""
        _, peer = fresh
        reply = C.handle(_msg("我想买这件，适合我吗", peer=peer))
        assert "还没做" in reply or "下一阶段" in reply

    def test_unknown_lists_capabilities(self, fresh):
        _, peer = fresh
        reply = C.handle(_msg("随便说点什么", peer=peer))
        assert "今天穿什么" in reply

    def test_empty_text_does_not_crash(self, fresh):
        """图片/文件在第二阶段才处理 —— 现在要**如实告知**，不能静默不回。"""
        _, peer = fresh
        assert C.handle(_msg("", peer=peer)) is not None


class TestSessionState:
    def test_state_is_persisted_with_context_token(self, fresh):
        """`context_token` 要落库 —— 设计文档 §13 明确要求持久化它。"""
        _, peer = fresh
        C.handle(InboundMessage(peer_id=peer, text="缺什么", context_token="ct_abc"))
        with db.conn() as c:
            row = c.execute(
                "SELECT context_token FROM conversation_state WHERE peer_id=?",
                (peer,),
            ).fetchone()
        assert row["context_token"] == "ct_abc"

    def test_outfits_expire_but_items_do_not(self, fresh):
        """★ 搭配候选会过期（隔天回「第一套」没意义），
        但"最近那件单品"不过期（"这件怎么搭"隔天也成立）。"""
        uid, peer = fresh
        _seed_wardrobe(uid)
        C.handle(_msg("今天穿什么", peer=peer))
        state = _state_of(peer)
        assert state["last_outfits"]

        # 把上次活动时间拨到 3 小时前（超过 2 小时 TTL）
        state["updated_at"] = "2020-01-01T00:00:00"
        state["last_items"] = ["it_fake"]
        with db.conn() as c:
            c.execute(
                "UPDATE conversation_state SET state=?, last_message_at=? "
                "WHERE channel=? AND peer_id=?",
                (json.dumps(state), "2020-01-01T00:00:00", C.CHANNEL, peer),
            )

        fresh_state = C._load_state(peer)
        assert fresh_state["last_outfits"] == [], "搭配候选该过期"
        assert fresh_state["last_items"] == ["it_fake"], "单品记忆不该过期"

    def test_draft_expires_faster_than_session(self, fresh):
        """一个"要不要入库"的问题放两小时再问就没意义了 → 草稿 TTL 更短。"""
        _, peer = fresh
        C.handle(_msg("我有一件深灰宽松卫衣", peer=peer))
        state = _state_of(peer)
        state["updated_at"] = db.now_iso()            # 会话没过期
        state["pending_draft"]["at"] = "2020-01-01T00:00:00"   # 但草稿很旧
        with db.conn() as c:
            c.execute("UPDATE conversation_state SET state=? WHERE channel=? AND peer_id=?",
                      (json.dumps(state), C.CHANNEL, peer))
        assert C._load_state(peer)["pending_draft"] is None

    def test_corrupted_state_does_not_crash(self, fresh):
        """库里状态坏了不该让整条链路崩 —— 当成新会话即可。"""
        _, peer = fresh
        with db.conn() as c:
            c.execute(
                "INSERT INTO conversation_state (id,channel,peer_id,context_token,state,"
                "last_message_at,created_at) VALUES (?,?,?,?,?,?,?)",
                ("cs_bad", C.CHANNEL, peer, "t", "{不是合法 JSON", "x", "x"),
            )
        assert C._load_state(peer)["last_outfits"] == []
        assert C.handle(_msg("随便说点什么", peer=peer)) is not None

    def test_two_peers_do_not_share_state(self, fresh):
        uid, peer = fresh
        _seed_wardrobe(uid)
        C.handle(_msg("今天穿什么", peer=peer))
        assert _state_of(peer)["last_outfits"]
        assert _state_of("someone_else@im.wechat") == {}
