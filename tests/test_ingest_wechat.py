"""W2 端到端：微信里粘商品分享文案 → 草稿 → 回「对」入库。

规格：`docs/history/设计文档_v0.2.md` §11.3。

⚠️ 这一层盯的是**静默错配** —— 单品名被存成整段脏串、品类没认出却照样入库、
草稿被静默覆盖。这些都不报错，只会让衣橱里多出一条说不清的东西。

模型在测试里是**确定不可用**的（`conftest.py` 把 `LLM_*` 全部置空），
所以"词表认不出"这条路径不会碰网络，而且可复现。
"""

from __future__ import annotations

import json

import pytest

from app import db
from app.channels import InboundMessage
from app.services import conversation as C

BLOB_JEANS = (
    "【淘宝】7天无理由退货 https://e.tb.cn/h.jaGM85wRqvc5RFe?tk=hibjTKoH6Go CZ007 "
    '「ALLFIT "2021M"原色黑牛仔裤 不褪色 宽松直筒微喇叭百搭牛仔裤男」\n'
    "点击链接直接打开 或者 淘宝搜索直接打开。"
)
BLOB_SWEATS = (
    "【淘宝】7天无理由退货 https://e.tb.cn/h.8BycubSyi283nlW?tk=V4TvTKoDnGj CZ193 "
    "「CatandMouse猫与小鼠25AW重磅400G7A炽芯索罗娜磨毛直筒宽松卫裤」\n"
    "点击链接直接打开 或者 淘宝搜索直接打开"
)
# 品类词表里没有的标题 → 本地认不出，测试里又没有模型可用
BLOB_UNKNOWN = (
    "【淘宝】7天无理由退货 https://e.tb.cn/h.zzz999?tk=qqq CZ999 "
    "「某品牌2026秋冬神秘新品首发」点击链接直接打开"
)

SWEATS_TITLE = "CatandMouse猫与小鼠25AW重磅400G7A炽芯索罗娜磨毛直筒宽松卫裤"


@pytest.fixture
def fresh(monkeypatch):
    """独立用户 + 独立 peer（`handle` 自己从库里取用户，没法靠传参隔离）。"""
    user_id = db.new_id("u")
    with db.conn() as c:
        c.execute(
            "INSERT INTO users(id,name,created_at) VALUES(?,?,?)",
            (user_id, "W2 测试用户", db.now_iso()),
        )
    monkeypatch.setattr(db, "get_default_user_id", lambda: user_id)
    return user_id, f"peer_{db.new_id('p')}@im.wechat"


def _msg(text: str, peer: str) -> InboundMessage:
    return InboundMessage(peer_id=peer, text=text, context_token="ct_w2")


def _state_of(peer: str) -> dict:
    with db.conn() as c:
        row = c.execute(
            "SELECT state FROM conversation_state WHERE channel=? AND peer_id=?",
            (C.CHANNEL, peer),
        ).fetchone()
    return json.loads(row["state"]) if row else {}


def _items_of(uid: str) -> list[dict]:
    with db.conn() as c:
        rows = c.execute(
            "SELECT id, name, type FROM clothing_items WHERE user_id=? ORDER BY created_at",
            (uid,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 草稿
# ---------------------------------------------------------------------------

class TestPasteCreatesDraft:
    def test_draft_has_clean_name_and_type(self, fresh):
        uid, peer = fresh
        reply = C.handle(_msg(BLOB_SWEATS, peer))

        draft = _state_of(peer)["pending_draft"]
        assert draft is not None
        assert draft["fields"]["name"] == SWEATS_TITLE
        assert draft["fields"]["type"] == "sweatpants"
        assert "我理解成" in reply
        assert "回「对」" in reply

    def test_name_has_no_share_blob_noise(self, fresh):
        """★ 发现 4 的守卫：`name` 必须是解析出的干净标题。

        confirm 分支的 `setdefault("name", raw_text)` 会把 `raw_text` 当名字 ——
        所以粘贴路径若把整段塞进草稿，单品名就会是
        `【淘宝】7天无理由退货 https://e.tb.cn/...`。
        """
        _uid, peer = fresh
        C.handle(_msg(BLOB_JEANS, peer))
        name = _state_of(peer)["pending_draft"]["fields"]["name"]
        assert "http" not in name
        assert "【淘宝】" not in name
        assert "7天无理由退货" not in name
        assert "CZ007" not in name
        assert name.startswith("ALLFIT")

    def test_paste_is_ingest_not_ask(self, fresh):
        """★ 发现 3 的守卫：粘贴链接**绝不能**被判成知识库提问。"""
        _uid, peer = fresh
        assert C.classify_intent(BLOB_SWEATS) == "ingest"
        assert C.classify_intent(BLOB_UNKNOWN) == "ingest"

    def test_ordinary_chat_still_works(self, fresh):
        """预筛不能误伤正常对话。"""
        _uid, peer = fresh
        assert C.classify_intent("今天穿什么") == "match"
        assert C.classify_intent("这个和那个有什么区别？") == "ask"


# ---------------------------------------------------------------------------
# 入库
# ---------------------------------------------------------------------------

class TestConfirmIngest:
    def test_confirm_stores_clean_name_and_right_type(self, fresh):
        uid, peer = fresh
        C.handle(_msg(BLOB_JEANS, peer))
        reply = C.handle(_msg("对", peer))

        items = _items_of(uid)
        assert len(items) == 1
        assert items[0]["type"] == "jeans"
        assert items[0]["name"].startswith("ALLFIT")
        assert "http" not in items[0]["name"]
        assert "已入库" in reply

    def test_draft_cleared_after_confirm(self, fresh):
        _uid, peer = fresh
        C.handle(_msg(BLOB_JEANS, peer))
        C.handle(_msg("对", peer))
        assert _state_of(peer)["pending_draft"] is None


class TestUnrecognisedCategory:
    """★ 发现 5 的守卫：`ItemCreate` 全字段有默认值 → **任何垃圾解析都能入库成功**。

    所以"认不出就不入库"必须显式写。两条触发路径都测：粘贴来的标题、
    以及「我有一件 XX」里 XX 不在词表。
    """

    def test_paste_asks_instead_of_silently_creating(self, fresh):
        uid, peer = fresh
        reply = C.handle(_msg(BLOB_UNKNOWN, peer))
        assert "没认出" in reply
        assert "品类" in reply
        # 草稿仍留着（等用户补品类），但**绝不能**已经入库
        assert _state_of(peer)["pending_draft"] is not None
        assert _items_of(uid) == []

    def test_confirm_is_refused_when_type_unknown(self, fresh):
        uid, peer = fresh
        C.handle(_msg(BLOB_UNKNOWN, peer))
        reply = C.handle(_msg("对", peer))

        assert "还不知道是什么品类" in reply
        assert _items_of(uid) == [], "认不出品类还是入库了 —— 静默污染衣橱"

    def test_user_supplies_category_then_confirm_works(self, fresh):
        """补一句品类就能入库 —— 走的是现有 `correct` 分支（抽出属性即修正）。"""
        uid, peer = fresh
        C.handle(_msg(BLOB_UNKNOWN, peer))
        C.handle(_msg("卫裤", peer))
        assert _state_of(peer)["pending_draft"]["fields"]["type"] == "sweatpants"
        C.handle(_msg("对", peer))

        items = _items_of(uid)
        assert len(items) == 1
        assert items[0]["type"] == "sweatpants"

    def test_describe_path_also_guarded(self, fresh):
        """同一条闸门也保护老的 describe 路径。

        ⚠️ 标题必须**带别的属性**（这里是颜色 + 版型）：只说「我有一件汉服」时
        `extract_fields` 一个属性都抽不到，describe 分支会**提前返回、根本不建草稿**
        （回"我没在这句话里认出品类"），那样就碰不到闸门。
        带上属性 → 建出**没有 type** 的草稿 → 回「对」时才真正走到闸门。
        """
        uid, peer = fresh
        reply = C.handle(_msg("我有一件深灰宽松汉服", peer))
        assert "我理解成" in reply          # 草稿建出来了
        reply = C.handle(_msg("对", peer))
        assert "还不知道是什么品类" in reply
        assert _items_of(uid) == []


# ---------------------------------------------------------------------------
# 多件 / 草稿冲突
# ---------------------------------------------------------------------------

class TestMultiItemAndConflicts:
    def test_multi_item_blob_takes_first_and_says_so(self, fresh):
        uid, peer = fresh
        reply = C.handle(_msg(BLOB_JEANS + BLOB_SWEATS, peer))

        assert "3 件" not in reply        # 两条文案 = 2 件
        assert "2 件" in reply
        assert "单独发给我" in reply
        assert _state_of(peer)["pending_draft"]["fields"]["name"].startswith("ALLFIT")

    def test_paste_while_draft_pending_reports_dropped(self, fresh):
        """★ 不能静默丢草稿 —— 用户会以为上一件已经入库了。"""
        _uid, peer = fresh
        C.handle(_msg(BLOB_JEANS, peer))
        reply = C.handle(_msg(BLOB_SWEATS, peer))

        assert "放掉了" in reply
        assert _state_of(peer)["pending_draft"]["fields"]["name"] == SWEATS_TITLE

    def test_paste_displaces_pending_note_draft(self, fresh):
        """衣物草稿与笔记草稿强制互斥（规格 §4 硬约束 1）。

        ⚠️ 这条测试曾经是**空的**（W2 变异测试抓出来的）：原来用
        `UPDATE conversation_state ...` 手工塞状态，但那一行**还不存在** →
        0 行受影响 → `_load_state` 返回空状态（`pending_note` 本来就是 None）
        → 断言恒真，去掉 `_clear_note_draft` 也照绿。
        所以现在**先 INSERT 建行**，并且**先断言前置状态真的写进去了**。
        """
        _uid, peer = fresh

        # ① 建行（用 upsert，跟 `_save_state` 同一套列）
        note = {"title": "缓存穿透", "body": "空值缓存 + 布隆过滤器"}
        blob = json.dumps({"pending_note": note}, ensure_ascii=False)
        with db.conn() as c:
            c.execute(
                "INSERT INTO conversation_state"
                "(id, channel, peer_id, context_token, state, last_message_at, created_at) "
                "VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(channel, peer_id) DO UPDATE SET state=excluded.state",
                (db.new_id("cs"), C.CHANNEL, peer, "ct_w2", blob, db.now_iso(), db.now_iso()),
            )

        # ② 前置检查：**没有这一步，测试就是空的**
        assert _state_of(peer).get("pending_note") == note, "前置状态没写进去，测试无意义"

        # ③ 粘贴一件衣服 → 笔记草稿必须让位
        C.handle(_msg(BLOB_JEANS, peer))
        assert _state_of(peer).get("pending_note") is None
        assert _state_of(peer)["pending_draft"] is not None
