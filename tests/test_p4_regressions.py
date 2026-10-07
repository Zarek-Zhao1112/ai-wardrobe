"""P4 知识库技能的意图分类 / 撞词回归 / 端到端测试。

★ 重点不是"新功能能用"，而是**新功能没抢走旧功能的话**：
知识库的 `learn` 分支插在 `_MATCH` 和 taxonomy 兜底**之前**（否则
「我想学卫衣怎么挑」会被判成"你在描述一件卫衣"），但插在前面就有抢话的风险 ——
所以这里逐条锁住分界线。

全程只碰**合成 vault** + `FakeLLM`，不联网、不碰真实知识库。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from vault_fixture import make_vault  # noqa: E402

from app import config  # noqa: E402
from app.channels import InboundMessage  # noqa: E402
from app.services import conversation  # noqa: E402
from app.services.knowledge import catalog  # noqa: E402


# ===========================================================================
# 1. 新意图能认出来
# ===========================================================================


@pytest.mark.parametrize(
    "text,expected",
    [
        ("我想学缓存", "learn"),
        ("想学并发", "learn"),
        ("学一下 HTTP 鉴权", "learn"),
        ("讲讲 RSI", "learn"),
        ("讲一下黄金", "learn"),
        ("了解一下缓存", "learn"),
        ("教教我 Tailwind", "learn"),
        ("随便挑一个", "browse"),
        ("有什么可以学", "browse"),
        ("能学什么", "browse"),
        ("最近学点东西", "browse"),
    ],
)
def test_new_intents_are_recognized(text, expected):
    assert conversation.classify_intent(text) == expected


# ===========================================================================
# 2. ★★ 撞词矩阵：新分支不许抢旧分支的话
# ===========================================================================


def test_learn_does_not_steal_match():
    """「我想学怎么搭配」含 `_MATCH` 的"搭配" —— 必须在穿搭那边。

    这条锁住的是**分支顺序**：`learn` 排在 `_MATCH` 之前，
    所以它得靠 `_LEARN_GUARD` 主动让路。
    """
    assert conversation.classify_intent("我想学怎么搭配") == "match"
    assert conversation.classify_intent("讲讲这件怎么搭", has_items=True) == "match"


def test_learn_guard_blocks_outfit_talk():
    """带衣橱词的说法不该被判成 learn（那些是"在看衣服"，不是"在学知识"）。"""
    for text in ("讲一下这件", "我想学怎么搭配", "了解一下这件外套", "学一下怎么做搭配"):
        assert conversation.classify_intent(text) != "learn", text


def test_describe_still_wins_for_plain_item_talk():
    """普通"描述一件衣服"不受影响。"""
    assert conversation.classify_intent("我有一件深灰卫衣") == "describe"
    assert conversation.classify_intent("刚买了条黑裤子") == "describe"


def test_learn_wins_over_taxonomy_fallback():
    """★ 反向锁定：含品类词但确实在"学"的句子，必须归 learn（不是 describe）。

    「我想学卫衣怎么挑」里有品类词"卫衣" —— 如果 `learn` 排在 taxonomy 兜底之后，
    这句会被判成"你在描述一件卫衣"。
    """
    assert conversation.classify_intent("我想学卫衣怎么挑") == "learn"


def test_other_intents_unchanged():
    """老意图逐条不变（这是"没抢话"的底线）。"""
    assert conversation.classify_intent("今天穿什么", has_items=True) == "match"
    assert conversation.classify_intent("缺什么") == "gap"
    assert conversation.classify_intent("想买一件外套") == "buy"
    assert conversation.classify_intent("对", has_draft=True) == "confirm"
    assert conversation.classify_intent("这套行", has_outfits=True) == "like"
    assert conversation.classify_intent("换一套", has_outfits=True) == "swap"
    assert conversation.classify_intent("今天天气不错") == "unknown"


# ===========================================================================
# 3. 序号挑笔记
# ===========================================================================


@pytest.mark.parametrize(
    "text",
    [
        "1", "2", "第1", "第 2 篇", "一", "二篇", "3个",
        # ★ 序号范围开到 9：领域数量会长，写死 5 会让"第 6 项"永远选不中
        "6", "第7", "八", "九篇",
    ],
)
def test_note_pick_with_candidates(text):
    assert conversation.classify_intent(text, has_candidates=True) == "note_pick"


def test_note_pick_needs_candidates():
    """没有候选列表时，光说「1」不该被当成"挑笔记"。"""
    assert conversation.classify_intent("1") != "note_pick"
    assert conversation.classify_intent("1", has_outfits=True) == "like"   # 搭配那边的语义还在


# ===========================================================================
# 4. 端到端（合成 vault + FakeLLM）
# ===========================================================================


@pytest.fixture
def kb(tmp_path, monkeypatch):
    """把知识库指到合成 vault 上，并清掉目录缓存。

    ⚠️ 必须 `setattr(config, ...)` 而不是 `setenv` —— `config.KNOWLEDGE_VAULT_DIR`
    是 **import 期求值**的模块常量，改环境变量对它没用。
    """
    root = make_vault(tmp_path / "vault")
    monkeypatch.setattr(config, "KNOWLEDGE_VAULT_DIR", str(root))
    catalog.reset_cache()
    yield root
    catalog.reset_cache()


_counter = {"n": 0}


def _msg(text: str, peer: str = "") -> InboundMessage:
    """造一条入站消息。**每个用例用独立 peer** —— 否则会话状态会跨用例串味。"""
    if not peer:
        _counter["n"] += 1
        peer = f"p4-test-{_counter['n']}"
    return InboundMessage(peer_id=peer, text=text, context_token="ct-test")


def test_e2e_learn_lists_related_notes(kb):
    reply = conversation.handle(_msg("我想学缓存"))
    assert reply and "Alpha·基础" in reply
    assert "回个序号" in reply


def test_e2e_learn_suggests_new_topic_when_missing(kb):
    """★ 库里没有 → 建议新主题，且**明说不会自己建**。"""
    reply = conversation.handle(_msg("我想学量子力学"))
    assert reply and "没有讲" in reply
    assert "开一个新主题" in reply
    # 关键：只是建议，不能真建目录
    assert not (kb / "领域" / "量子力学").exists()


def test_e2e_pick_then_ask(kb, monkeypatch):
    """走完整条链：想学 → 列表 → 挑一篇 → 问问题（模型回答）。"""
    import app.services.llm.client as client_mod
    from conftest import FakeLLM

    peer = "p4-e2e-pick"
    first = conversation.handle(_msg("我想学缓存", peer))
    assert "Alpha·基础" in first

    picked = conversation.handle(_msg("1", peer))
    assert picked and "我们就聊这篇" in picked and "Alpha·基础" in picked

    monkeypatch.setattr(
        client_mod, "get_client", lambda **kw: FakeLLM(["缓存命中率 = 命中 / 总访问。"])
    )
    answered = conversation.handle(_msg("缓存命中率怎么算", peer))
    assert answered and "命中" in answered


def test_e2e_ask_without_focus_falls_back_to_help(kb):
    """没选定任何笔记时，随便问一句应该给帮助菜单，而不是硬聊。"""
    reply = conversation.handle(_msg("随便问一个"))
    assert reply and "我能做这几件事" in reply


def test_e2e_not_configured(monkeypatch):
    """没配路径 → 明确说没配（不是静默失败、更不是崩）。"""
    monkeypatch.setattr(config, "KNOWLEDGE_VAULT_DIR", "")
    reply = conversation.handle(_msg("我想学缓存"))
    assert reply and "还没接上" in reply


def test_e2e_browse_lists_domains(kb):
    reply = conversation.handle(_msg("有什么可以学"))
    assert reply and "领域" in reply
    assert "甲领域" in reply or "乙领域" in reply


def test_e2e_focus_survives_and_resolves(kb):
    """选定后，后续消息仍能定位到同一篇（状态确实存住了）。"""
    peer = "p4-e2e-focus"
    conversation.handle(_msg("我想学缓存", peer))
    conversation.handle(_msg("1", peer))

    from app.services.conversation import _load_state

    state = _load_state(peer)
    assert state.get("note_focus") == "Alpha·基础"
    assert state.get("note_candidates") == []


def test_e2e_stale_focus_is_cleared(kb):
    """焦点指向一篇已不存在的笔记时，要清掉而不是卡死。"""
    peer = "p4-e2e-stale"
    conversation.handle(_msg("我想学缓存", peer))
    conversation.handle(_msg("1", peer))

    from app.services import conversation as conv
    from app import db

    state = conv._load_state(peer)
    state["note_focus"] = "这篇根本不存在"
    conv._save_state(peer, state, context_token="ct")

    reply = conv.handle(_msg("随便说点什么", peer))
    assert reply and "我能做这几件事" in reply   # 回落到帮助，而不是报错
    assert conv._load_state(peer).get("note_focus") is None
