"""P5 · W5 回归测试 —— 每一条都对应 W4 双审查抓到并**已复现**的缺陷。

规矩（沿用 P1–P4）：审查报的每一条，修完必须**当场配回归测试**，
否则它会悄悄回来。

| 编号 | 缺陷 | 来源 |
|---|---|---|
| 1 | `resolve_type` 最长包含匹配没做小写归一 → `黑色A字裙` 落 `other` | 规格官 + Bug 猎手**独立**撞到 |
| 2 | `pick.error` 未定义 + 该分支恒假（前端同款也恒假） | 规格官 + Bug 猎手**独立**撞到 |
| 3 | 没 key 时静默降级（`error=""`） | 规格官 |
| 4 | 微信端缺本地查重（§11.3 管道④） | 规格官 |
| 5 | 缺"用户坚持要记 → 以 other 入库"的出口（§11.3 失败语义表） | 规格官 |
| 6 | 兜底路径把型号 `GT2000` 当分享码删掉 | Bug 猎手 |
| 7 | 兜底路径丢 URL / 分享码 / 平台 | Bug 猎手 |
"""

from __future__ import annotations

import json

import pytest

from app import config, db, taxonomy
from app.channels import InboundMessage
from app.services import conversation as C
from app.services import ingest as I

BLOB_JEANS = (
    "【淘宝】7天无理由退货 https://e.tb.cn/h.jaGM85wRqvc5RFe?tk=hibjTKoH6Go CZ007 "
    '「ALLFIT "2021M"原色黑牛仔裤 不褪色 宽松直筒微喇叭百搭牛仔裤男」\n'
    "点击链接直接打开 或者 淘宝搜索直接打开。"
)
BLOB_UNKNOWN = (
    "【淘宝】7天无理由退货 https://e.tb.cn/h.zzz999?tk=qqq CZ999 "
    "「某品牌2026秋冬神秘新品首发」点击链接直接打开"
)


class TestModelPromptShape:
    """★ 只有**真机**才暴露的一条：prompt 里必须**要 JSON**。

    实测（2026-10-08）：DeepSeek `deepseek-v4-pro` 的
    `capabilities()` 是 `supports_json_schema=False, supports_json_mode=False`
    → `chat_json` 只能走最后一级「纯文本 + 正则抽 JSON」。
    原 prompt 写的是"只输出一个 key，不要解释"，模型于是回**裸词** `other`
    （回答完全正确！），却没有 `{...}` 可抽 →
    抛 `LLMError("策略 text 未能返回可解析的 JSON 对象")`。

    现象极具误导性：看着像"模型没接上"，其实是**我们没跟它要 JSON**。

    ⚠️ 这一组是**弱护栏**（`FakeLLM` 不看 prompt，返回什么都行），
    只能用文本断言守；真正的验收是"配了 key 时真调一次"。
    """

    def test_system_prompt_asks_for_json(self):
        assert "JSON" in I._SYSTEM
        assert '{"type"' in I._SYSTEM

    def test_schema_declares_type_required(self):
        assert I._SCHEMA["required"] == ["type"]

    def test_options_placeholder_is_filled(self):
        """`{options}` 必须真被填上，否则模型看到的是字面量。"""
        rendered = I._SYSTEM.format(options=I._type_options())
        assert "{options}" not in rendered
        assert "pants" in rendered


# ---------------------------------------------------------------------------
# 1 · 归一化：包含匹配的两边都要过一遍
# ---------------------------------------------------------------------------

class TestUppercaseContainment:
    """★ 我 W0 加的 `a字裙` 别名，对商品标题里的标准写法 `A字裙` **失效**。

    根因：包含匹配拿**原样**的词表条目去比归一化过的输入 —— 只对了两半。
    """

    @pytest.mark.parametrize(
        ("text", "want"),
        [
            ("黑色A字裙", "skirt"),
            ("黑色a字裙", "skirt"),
            ("A字裙", "skirt"),
            ("纯棉Tee", "tshirt"),
            ("纯棉tee", "tshirt"),
            ("法式黑色A字裙", "skirt"),
            ("Nike 长袖T恤", "sweatshirt"),
        ],
    )
    def test_case_insensitive_containment(self, text, want):
        assert taxonomy.resolve_type(text) == want

    def test_slot_is_correct_so_it_stays_in_combinations(self):
        """落 other 的槽位是 accessory —— 裙子会**静默**从组合里消失。"""
        assert taxonomy.slot_of(taxonomy.resolve_type("黑色A字裙")) == "bottom"

    @pytest.mark.parametrize("word", ["面包", "法拉利", "asdfgh"])
    def test_still_unknown(self, word):
        """归一化的放宽不能把"真不认识"也说成认识。"""
        assert taxonomy.resolve_type(word) == "other"


# ---------------------------------------------------------------------------
# 2 · `pick.error` 未定义 + 分支恒假
# ---------------------------------------------------------------------------

class TestNoDeadBranch:
    def test_source_has_no_bare_pick_error(self):
        """★ 原来 `_handle_ingest` 里写了裸名 `pick.error`（函数内无此变量）。

        该分支恒为假所以线上没崩，但它是**潜在 NameError**。
        用 **AST** 查，不查字符串 —— 注释里也会提到这个名字（第一版就这么假红过）。
        W4 的**两个** agent 独立撞到同一处。
        """
        import ast
        from pathlib import Path

        tree = ast.parse(Path(C.__file__).read_text(encoding="utf-8"))
        bare = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Attribute)
            and isinstance(n.value, ast.Name)
            and n.value.id == "pick"
        ]
        assert not bare, f"第 {[b.lineno for b in bare]} 行用了未定义的裸名 pick"

    def test_llm_error_shows_up_when_type_unresolved(self, monkeypatch):
        """`used_llm and error` 与 `needs_type` 天然互斥 →
        这条提示只可能出现在 **needs_type** 那一支里，测试就按那条测。"""
        monkeypatch.setattr(config, "LLM_READY", False)
        c = I.build_candidates(BLOB_UNKNOWN)[0]
        assert c.needs_type is True
        assert c.pick.error, "模型没接上必须带原因"

    def test_frontend_condition_is_reachable(self):
        """前端同款恒假条件也要修掉：`!needs_type && used_llm && llm_error` 永不成立。"""
        from pathlib import Path

        src = (Path(C.__file__).resolve().parents[2] / "web" / "js" / "view-add.js").read_text(
            encoding="utf-8"
        )
        assert "!row.needs_type && row.used_llm && row.llm_error" not in src
        assert "row.needs_type && row.llm_error" in src


# ---------------------------------------------------------------------------
# 6 / 7 · 兜底路径：别删型号、别丢链接
# ---------------------------------------------------------------------------

class TestFallbackPath:
    """★ 兜底路径（没有书名号）原来的两个毛病，都是"静默"的。"""

    @pytest.mark.parametrize(
        ("text", "must_keep"),
        [
            ("Nike GT2000 跑步鞋", "GT2000"),
            ("AJ1130 篮球鞋", "AJ1130"),
            ("A1234 冲锋衣", "A1234"),
        ],
    )
    def test_model_number_is_not_stripped(self, text, must_keep):
        """`_CODE_RE` 分不清分享码 `CZ007` 和型号 `GT2000` ——
        剥了会把型号从商品名里删掉。**宁可多留一个分享码。**"""
        (parsed,) = I.parse_share(text)
        assert must_keep in parsed.title

    def test_name_consistent_with_and_without_quotes(self):
        """两条路径对同一标题必须给出一致的 `name`（原来是"主路径保留、兜底删除"）。"""
        plain = I.build_candidates("Nike GT2000 跑步鞋")[0].fields["name"]
        quoted = I.build_candidates("「Nike GT2000 跑步鞋」")[0].fields["name"]
        assert plain == quoted

    def test_fallback_keeps_url_code_platform(self):
        """★ 原来兜底分支只填 `title`/`raw`，`url`/`code`/`platform` **恒为空** ——
        网页预览里因此看不到商品链接。"""
        (parsed,) = I.parse_share("宽松直筒牛仔裤 https://item.taobao.com/item.htm?id=123")
        assert parsed.url == "https://item.taobao.com/item.htm?id=123"

    def test_fallback_reads_platform_and_code(self):
        (parsed,) = I.parse_share("【淘宝】宽松直筒牛仔裤 https://e.tb.cn/h.abc123?tk=x CZ123")
        assert parsed.platform == "淘宝"
        assert parsed.code == "CZ123"

    def test_multiline_still_works(self):
        got = I.parse_share("纯棉圆领T恤 深灰\n宽松直筒牛仔裤\n小白鞋")
        assert [p.title for p in got] == ["纯棉圆领T恤 深灰", "宽松直筒牛仔裤", "小白鞋"]

    @pytest.mark.parametrize("text", ["？？？", "……", "   "])
    def test_pure_symbols_still_rejected(self, text):
        assert I.parse_share(text) == []


# ---------------------------------------------------------------------------
# 3 / 4 / 5 · 微信端
# ---------------------------------------------------------------------------

@pytest.fixture
def fresh(monkeypatch):
    user_id = db.new_id("u")
    with db.conn() as c:
        c.execute(
            "INSERT INTO users(id,name,created_at) VALUES(?,?,?)",
            (user_id, "W5 测试用户", db.now_iso()),
        )
    monkeypatch.setattr(db, "get_default_user_id", lambda: user_id)
    return user_id, f"peer_{db.new_id('p')}@im.wechat"


def _msg(text: str, peer: str) -> InboundMessage:
    return InboundMessage(peer_id=peer, text=text, context_token="ct_w5")


def _draft(peer: str) -> dict:
    with db.conn() as c:
        row = c.execute(
            "SELECT state FROM conversation_state WHERE channel=? AND peer_id=?",
            (C.CHANNEL, peer),
        ).fetchone()
    return (json.loads(row["state"]) if row else {}).get("pending_draft") or {}


def _items(uid: str) -> list[dict]:
    with db.conn() as c:
        rows = c.execute(
            "SELECT name, type, notes FROM clothing_items WHERE user_id=?", (uid,)
        ).fetchall()
    return [dict(r) for r in rows]


class TestWechatDuplicateHint:
    """★ §11.3 管道④：本地查重**对微信端同样成立**。原来只做在网页端。"""

    def test_second_paste_of_same_title_hints(self, fresh):
        uid, peer = fresh
        C.handle(_msg(BLOB_JEANS, peer))
        C.handle(_msg("对", peer))                 # 真入库
        assert len(_items(uid)) == 1

        reply = C.handle(_msg(BLOB_JEANS, peer))   # 再粘一遍
        assert "你可能已经录过" in reply
        assert "仍可以入库" in reply                # **只提示，不阻止**

    def test_first_paste_has_no_hint(self, fresh):
        _uid, peer = fresh
        assert "你可能已经录过" not in C.handle(_msg(BLOB_JEANS, peer))


class TestOtherPassThrough:
    """★ §11.3 失败语义表里那一行"用户坚持要记 → 才允许以 other 入库"。

    原来**没有任何路径可达** —— 说了品类仍认不出，草稿就永远卡住。
    """

    def test_plain_confirm_still_refused(self, fresh):
        uid, peer = fresh
        C.handle(_msg(BLOB_UNKNOWN, peer))
        reply = C.handle(_msg("对", peer))
        assert "还不知道是什么品类" in reply
        assert _items(uid) == []

    def test_refusal_tells_the_user_about_the_way_out(self, fresh):
        """拒绝时必须告诉他出口在哪，否则用户只会反复说「对」。"""
        _uid, peer = fresh
        C.handle(_msg(BLOB_UNKNOWN, peer))
        reply = C.handle(_msg("对", peer))
        assert "就记" in reply

    def test_ji_ji_passes_through_and_marks_it(self, fresh):
        uid, peer = fresh
        C.handle(_msg(BLOB_UNKNOWN, peer))
        reply = C.handle(_msg("就记", peer))

        rows = _items(uid)
        assert len(rows) == 1
        assert rows[0]["type"] == "other"
        assert "品类未识别" in (rows[0]["notes"] or ""), "放行必须**标出**品类未识别"
        assert "已入库" in reply

    def test_other_words_do_not_pass_through(self, fresh):
        """⚠️ 「对」「好」**不能**放行 —— 否则任何一句确认都会静默塞一条 other 进衣橱。"""
        uid, peer = fresh
        C.handle(_msg(BLOB_UNKNOWN, peer))
        for word in ("对", "好的", "可以"):
            C.handle(_msg(word, peer))
        assert _items(uid) == []

    def test_ji_ji_is_confirm_intent_only_with_draft(self):
        """没有草稿时说「就记」，不该被当成确认。"""
        assert C.classify_intent("就记") == "unknown"
        assert C.classify_intent("就记", has_draft=True) == "confirm"
