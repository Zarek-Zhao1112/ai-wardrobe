"""P3 · W4 审查 → W5 修复的**回归测试**。

每一条都对应一个"改坏就红"的具体缺陷。没有这些测试，以后任何一次重构
都可能把老 bug 原样带回来 —— 它们**全是静默的**（不报错，只是悄悄做错事）。

分两组：
- **通道层** `app/channels/clawbot.py` —— 用 `httpx.MockTransport`，不联网
- **会话状态机** `app/services/conversation.py` —— 纯函数 + 端到端

来源：`docs/tasks.md` 的 P3 · W4 裁决表。
"""

from __future__ import annotations

import json

import httpx
import pytest

from app import config, db
from app.channels import InboundMessage, reset_channel_cache
from app.channels.clawbot import ClawBotChannel, ClawBotSession
from app.services import conversation as C
from app.services import items as item_svc


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """会话文件落到系统临时目录，**别碰项目的 `data/`**。"""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path, raising=False)
    reset_channel_cache()
    yield
    reset_channel_cache()


@pytest.fixture
def fresh(monkeypatch):
    """独立用户 + 独立 peer（与 `test_conversation.py` 同一套隔离手法）。"""
    user_id = db.new_id("u")
    with db.conn() as c:
        c.execute(
            "INSERT INTO users(id,name,created_at) VALUES(?,?,?)",
            (user_id, "W5 回归测试用户", db.now_iso()),
        )
    monkeypatch.setattr(db, "get_default_user_id", lambda: user_id)
    return user_id, f"w5_{db.new_id('p')}@im.wechat"


def _chan(handler, **kw) -> ClawBotChannel:
    return ClawBotChannel(
        base_url="https://example.test",
        transport=httpx.MockTransport(handler),
        **kw,
    )


def _msg(text: str, peer: str) -> InboundMessage:
    return InboundMessage(peer_id=peer, text=text, context_token="ct_w5")


def _state_of(peer: str) -> dict:
    with db.conn() as c:
        row = c.execute(
            "SELECT state FROM conversation_state WHERE channel=? AND peer_id=?",
            (C.CHANNEL, peer),
        ).fetchone()
    return json.loads(row["state"]) if row else {}


def _seed_wardrobe(uid: str, n_tops: int = 3, n_bottoms: int = 3) -> list[str]:
    """凑一个能出搭配的最小衣橱（不标 season → 与当前月份无关）。"""
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


# ===========================================================================
# 一、通道层
# ===========================================================================


def test_bad_create_time_does_not_raise():
    """⚠️ 回归：`create_time_ms` 是畸形值时必须**留空**，而不是抛异常。

    协议文档把时间戳标成 📄（未实测）。服务端一旦给的单位不是毫秒
    （实测 `1700000000000000` → `OSError [Errno 22]`；`9e30` → `OverflowError`），
    而这个函数跑在 `fetch_updates` 的解析循环里 —— 抛出去会**拖掉整批消息**。
    """
    body = {"ret": 0, "get_updates_buf": "C2", "msgs": [
        {"from_user_id": "u@im.wechat", "context_token": "CT",
         "item_list": [{"type": 1, "text_item": {"text": "早上穿什么"}}],
         "create_time_ms": 1700000000000000},
    ]}
    ch = _chan(lambda r: httpx.Response(200, json=body))
    ch.save_session(ClawBotSession(token="TOK"))

    msgs = ch.fetch_updates()

    assert [m.text for m in msgs] == ["早上穿什么"]   # 消息还在，没被吞掉
    assert msgs[0].created_at == ""                   # 时间留空，不是崩溃


def test_unhashable_item_type_does_not_raise():
    """⚠️ 回归：`item_list[0].type` 不是 int 时，`dict.get` 会抛 `TypeError`。"""
    body = {"ret": 0, "get_updates_buf": "C2", "msgs": [
        {"from_user_id": "u", "context_token": "CT",
         "item_list": [{"type": {"weird": 1}}]},
    ]}
    ch = _chan(lambda r: httpx.Response(200, json=body))
    ch.save_session(ClawBotSession(token="TOK"))

    msgs = ch.fetch_updates()

    assert len(msgs) == 1
    assert msgs[0].kind == "text"


def test_cursor_advances_even_if_one_message_is_bad(monkeypatch):
    """⚠️ 回归（P0）：游标必须在**解析消息之前**落盘。

    解析曾经会抛非 `ChannelError` 的异常，而游标在解析**之后**才存 →
    同一批消息每次都被重拉、每次都抛 → 通道**焊死**，且只有日志看得出来。
    """
    bad_and_good = [
        {"from_user_id": "a", "item_list": [{"type": 1, "text_item": {"text": "ok"}}]},
        {"from_user_id": "b", "item_list": [{"type": 1, "text_item": {"text": "bad"}}]},
    ]
    body = {"ret": 0, "get_updates_buf": "C2", "msgs": bad_and_good}
    ch = _chan(lambda r: httpx.Response(200, json=body))
    ch.save_session(ClawBotSession(token="TOK", updates_buf="C1"))

    good = ClawBotChannel._to_inbound(bad_and_good[0])

    def boom(msg):
        if msg.get("from_user_id") == "b":
            raise RuntimeError("模拟解析炸了")
        return good

    monkeypatch.setattr(ClawBotChannel, "_to_inbound", staticmethod(boom))

    msgs = ch.fetch_updates()

    assert [m.text for m in msgs] == ["ok"]              # 坏的那条被跳过
    assert ch.load_session().updates_buf == "C2"         # ★ 游标一定推进了


def test_fetch_uses_session_base_url():
    """⚠️ 回归：绑定响应里下发的 `baseurl` 必须被用上。

    以前 `fetch_updates` 一律用构造时的 host，把 session 里存着的那个
    **完全忽略** —— 服务端要求换 host 时会静默打到旧域名。
    """
    seen: dict[str, str] = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"ret": 0, "get_updates_buf": "C2", "msgs": []})

    ch = _chan(handler)
    ch.save_session(ClawBotSession(token="TOK", base_url="https://session-host.test"))

    ch.fetch_updates()

    assert seen["url"].startswith("https://session-host.test/")


def test_send_uses_session_base_url():
    """同上，发送侧。"""
    seen: dict[str, str] = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"ret": 0})

    ch = _chan(handler)
    ch.save_session(ClawBotSession(token="TOK", base_url="https://session-host.test"))

    ch.send_text("u@im.wechat", "hi", context_token="CT")

    assert seen["url"].startswith("https://session-host.test/")


def test_base_url_falls_back_when_session_has_none():
    """session 没下发 host 时，回落到构造时的默认 host（别把 endpoint 拼空）。"""
    seen: dict[str, str] = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"ret": 0, "get_updates_buf": "C2", "msgs": []})

    ch = _chan(handler)
    ch.save_session(ClawBotSession(token="TOK"))

    ch.fetch_updates()

    assert seen["url"].startswith("https://example.test/")


# ===========================================================================
# 二、会话状态机 · 意图识别
# ===========================================================================


class TestNewItemIsNotACorrection:
    """⚠️ 回归（P0）：草稿还在时又说「我买了X」，曾经被判成**对草稿的修正**。

    判据是 `t.startswith(("买了", ...))`，而「我买了条裤子」开头是「我」——
    于是新单品的属性被**合并进旧草稿**，用户回「对」入库的是一条串了味的衣服。
    """

    @pytest.mark.parametrize("text", [
        "我买了条裤子",
        "我刚买了件外套",
        "我有一件卫衣",
        "入手了一件夹克",
    ])
    def test_buying_phrases_are_new_items(self, text):
        assert C.classify_intent(text, has_draft=True) == "describe"

    @pytest.mark.parametrize("text", ["不买了", "别买了"])
    def test_negation_is_not_a_new_item(self, text):
        assert C._is_new_item(text) is False

    def test_correction_still_wins_when_it_is_one(self):
        """别把"修正"一起误判成新的一件（矫枉过正）。"""
        assert C.classify_intent("颜色是深蓝", has_draft=True) == "correct"

    def test_new_item_does_not_merge_into_draft(self, fresh):
        """端到端：旧草稿必须被**放下并明说**，新草稿是干净的。"""
        _user_id, peer = fresh
        C.handle(_msg("我有一件深灰宽松卫衣", peer))

        reply = C.handle(_msg("我买了条裤子", peer))

        draft = _state_of(peer)["pending_draft"]
        assert draft["raw_text"] == "我买了条裤子"
        # ★ 关键：旧草稿的"灰"不许被并进新草稿
        assert "colors" not in (draft["fields"] or {})
        assert "上一件" in reply        # 明确告知旧草稿已放下（不能静默丢）


class TestFeedbackWords:
    """⚠️ 回归：`_LIKE` 里曾有单字「行」→「我去**银行**」被判成 like，
    并**真的往偏好记忆里写了一条反馈**。"""

    @pytest.mark.parametrize("text", ["我去银行", "今天要去银行办事", "银行几点关门"])
    def test_bank_is_not_feedback(self, text):
        assert C.classify_intent(text, has_outfits=True) == "unknown"

    @pytest.mark.parametrize("text", ["行", "行的", "可以", "这套行", "不错", "就这套"])
    def test_short_affirmatives_still_work(self, text):
        assert C.classify_intent(text, has_outfits=True) == "like"

    def test_question_wins_over_praise(self):
        """「今天穿什么**好看**的」同时含 _MATCH 与 _LIKE —— 问句优先，
        否则会白写一条反馈进偏好记忆。"""
        assert C.classify_intent("今天穿什么好看的", has_outfits=True) == "match"


class TestReferentialMatch:
    """规格 §4 分支 7：有 `last_items` 时，光说「这件」也算在问搭配。"""

    def test_refer_with_items(self):
        assert C.classify_intent("这件", has_items=True) == "match"

    def test_refer_without_items_is_not_match(self):
        assert C.classify_intent("这件", has_items=False) == "unknown"


class TestUnresolvedReporting:
    """⚠️ 回归：`unresolved` 曾经**恒为空字典** —— 回复里"（这几个没认出来：…）"
    是死代码。用户说了「带帽」而库里什么都没记，且永远不知道自己被丢了什么。"""

    @pytest.mark.parametrize("text,expect", [
        ("我有一件带帽卫衣", "带帽"),
        ("我有一件加厚卫衣", "加厚"),
        ("我有一件落肩卫衣", "落肩"),
    ])
    def test_unrecognised_part_is_reported(self, text, expect):
        fields, unresolved = C.extract_fields(text)
        assert fields.get("type") == "hoodie"
        assert unresolved.get("未识别") == expect

    def test_clean_description_reports_nothing(self):
        """别把"全都认出来了"也报成没认出来 —— 误报会让提示变噪音。"""
        _fields, unresolved = C.extract_fields("我有一件卫衣")
        assert unresolved == {}

    def test_single_char_residue_is_not_reported(self):
        """「深灰」里「灰」已被识别、只剩单字「深」—— 单字残留**刻意不报**
        （否则「深」「大」「旧」这类碎片会把提示变成噪音）。
        代价：「深灰」被静默存成「灰」—— 这是词表缺口语别名，属已知技术债。"""
        _fields, unresolved = C.extract_fields("我有一件深灰宽松卫衣")
        assert unresolved == {}


# ===========================================================================
# 三、会话状态机 · 端到端
# ===========================================================================


def test_out_of_range_index_is_refused_not_misattributed(fresh):
    """⚠️ 回归：只挑了 3 套时回「5」，以前会**兜底记到第 1 套**上 ——
    等于把反馈写给了另一套衣服。现在明确拒绝，且不动状态。
    """
    user_id, _peer = fresh
    state = {"last_outfits": ["of_only_one"], "last_items": [], "pending_draft": None}

    reply = C._handle_feedback(user_id, state, "like", "5")

    assert "没有第 5 套" in reply
    assert state["last_outfits"] == ["of_only_one"]      # 状态没被动过


def test_swap_excludes_previously_shown(fresh):
    """⚠️ 回归：「换一套」必须**排除刚给过的那几套**（规格 §4.1）。

    以前的实现把 `last_outfits` 清空后原样再推荐一遍 ——
    用户说"换一套"，拿回来的是一模一样的三套。
    """
    user_id, peer = fresh
    _seed_wardrobe(user_id)

    C.handle(_msg("今天穿什么", peer))
    before = set(_state_of(peer)["last_outfits"])
    assert before, "第一轮就该给出搭配"

    C.handle(_msg("换一套", peer))
    after = set(_state_of(peer)["last_outfits"])

    assert after, "换一套之后不能空手"
    assert after - before, "换了一套，结果还是原来那几套"


def test_deleted_items_do_not_crash_feedback(fresh):
    """⚠️ 回归：搭配里的单品被删后再回「这套行」→ 以前 `handle` 会**抛 ValueError**
    （worker 兜底 → 用户只看到"出错了"，反馈静默丢失）。
    """
    user_id, peer = fresh
    seed_ids = _seed_wardrobe(user_id)
    C.handle(_msg("今天穿什么", peer))
    assert _state_of(peer)["last_outfits"], "先要拿到搭配"

    # 把这批搭配涉及的单品全删掉，制造"搭配里的人已经不在了"
    from app.services import recommend as rec_svc
    victims: set[str] = set()
    for oid in _state_of(peer)["last_outfits"]:
        row = rec_svc.get_outfit(user_id, oid)
        victims.update(i for i in (row or {}).get("item_ids") or [] if i in seed_ids)

    assert victims, "搭配里应该有能删的单品"
    for iid in victims:
        # ★ 参数顺序别写反（2026-10-22 审查抓到这里原本是
        #   `delete(user_id, iid)`，签名是 `delete(item_id, user_id)`）——
        #   写反时 rowcount 恒为 0，一件都没删掉，这条回归等于空转。
        #   先去重：同一件会出现在好几套搭配里，重复删第二次必然返回 False。
        assert item_svc.delete(iid, user_id) is True, f"没删掉 {iid}"

    reply = C.handle(_msg("1", peer))       # 不许抛异常

    assert isinstance(reply, str) and reply.strip()
