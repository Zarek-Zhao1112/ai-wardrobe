"""生成新笔记的测试：拼装（纯函数）+ 端到端（生成 → 预览 → 落盘）。

★ 两条最该被锁住的纪律：
1. **结构由 Python 产，不由模型产** —— 所以 `assemble()` 是纯函数，这里逐字断言格式；
2. **预览不落盘** —— 生成完只发预览，用户回「落」才写（断言"预览后 vault 一个字节没变"）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from vault_fixture import make_vault, snapshot_fingerprint  # noqa: E402

from app import config  # noqa: E402
from app.channels import InboundMessage  # noqa: E402
from app.services import conversation  # noqa: E402
from app.services.knowledge import catalog, generate  # noqa: E402
from app.services.knowledge.generate import NotePlan, Section  # noqa: E402


# ===========================================================================
# 1. 死链必须被剥掉
# ===========================================================================


def test_keeps_links_to_existing_notes():
    text = "见 [[Alpha·基础]] 那篇。"
    out, dropped = generate.strip_unknown_links(text, {"Alpha·基础"})
    assert out == text and dropped == []


def test_strips_links_to_missing_notes():
    """★ 模型爱编链接 —— 库里没有的必须在图谱里消失，不能留死链。"""
    out, dropped = generate.strip_unknown_links("见 [[缓存淘汰策略]]。", {"Alpha·基础"})
    assert "[[" not in out and "缓存淘汰策略" in out
    assert dropped == ["缓存淘汰策略"]


def test_strips_aliased_links_keeps_alias_text():
    """★ 剥掉别名链接时**保留别名文字**（审查抓到，2026-10-06）。

    原来 `[[不存在A|想显示的文字]]` 退化成裸的「不存在A」—— 而作者写
    "见 想显示的文字" 本来就是通顺的，退化成目标名反而语义不对。
    """
    out, dropped = generate.strip_unknown_links("[[不存在A|想显示的文字]]", set())
    assert "[[" not in out and "想显示的文字" in out
    assert dropped == ["不存在A"]


def test_strips_plain_link_to_missing_note():
    out, dropped = generate.strip_unknown_links("[[不存在]]", set())
    assert out == "不存在" and dropped == ["不存在"]


# ===========================================================================
# 2. assemble —— 格式是冻结的
# ===========================================================================


def _plan() -> NotePlan:
    return NotePlan(
        title="缓存一致性",
        subtitle="陈旧读与失效",
        aliases=["缓存一致性模型"],
        tags=["缓存", "一致性"],
        sections=[
            Section(heading="一、什么是陈旧读", body="陈旧读是指……读到旧值。"),
            Section(heading="二、怎么避免", body="用失效策略……避免。"),
        ],
        intro="> 目标：搞清缓存为什么会骗你。\n> **一句话定位：**缓存不是数据库，别当它准。",
        intro_note="只讲缓存一致性与陈旧读。",
        outro=["- 相邻：分布式共识", "- 书籍：DDIA 第五章"],
    )


def test_assemble_frontmatter_field_order():
    meta, _, _ = generate.assemble(_plan(), domain_tag="领域/甲", known_names=set())
    assert list(meta.keys()) == ["type", "tags", "aliases", "created", "source", "note"]
    assert meta["type"] == "permanent"


def test_assemble_domain_tag_goes_last():
    """★ 领域标签必须在 `tags` 最后一位 —— 图谱分色靠它。"""
    meta, _, _ = generate.assemble(_plan(), domain_tag="领域/甲", known_names=set())
    assert meta["tags"][-1] == "领域/甲"
    assert "领域/甲" not in meta["tags"][:-1]


def test_assemble_does_not_duplicate_domain_tag():
    """模型如果自己塞了领域标签，要去掉重加（保证只有最后一个）。"""
    plan = _plan()
    plan.tags = ["缓存", "领域/别的"]
    meta, _, _ = generate.assemble(plan, domain_tag="领域/甲", known_names=set())
    assert meta["tags"].count("领域/甲") == 1
    assert "领域/别的" not in meta["tags"]


def test_assemble_h1_uses_library_convention():
    """H1 是 `# 主标题 · 副标题`（空格 + 中点 + 空格）。"""
    _, body, _ = generate.assemble(_plan(), domain_tag="领域/甲", known_names=set())
    assert body.startswith("# 缓存一致性 · 陈旧读与失效\n")


def test_assemble_section_order_follows_plan():
    _, body, _ = generate.assemble(_plan(), domain_tag="领域/甲", known_names=set())
    i1 = body.index("## 一、什么是陈旧读")
    i2 = body.index("## 二、怎么避免")
    assert i1 < i2


def test_assemble_appends_outro_section():
    _, body, _ = generate.assemble(_plan(), domain_tag="领域/甲", known_names=set())
    assert "## 附：延伸学习线索" in body
    assert body.rstrip().endswith("书籍：DDIA 第五章")


def test_assemble_reports_dropped_links():
    plan = _plan()
    plan.sections[0].body = "见 [[不存在的笔记]]。"
    _, _, dropped = generate.assemble(plan, domain_tag="领域/甲", known_names={"Alpha·基础"})
    assert dropped == ["不存在的笔记"]


def test_assemble_is_deterministic():
    a = generate.assemble(_plan(), domain_tag="领域/甲", known_names=set(), today="2026-10-06")
    b = generate.assemble(_plan(), domain_tag="领域/甲", known_names=set(), today="2026-10-06")
    assert a == b


def test_assemble_note_mentions_given_neighbours():
    """frontmatter 的 `note` 里用**书名号**提相邻笔记（不是 `[[]]`）。"""
    meta, _, _ = generate.assemble(
        _plan(), domain_tag="领域/甲",
        known_names={"Alpha·基础", "Beta·进阶"},
        related=["Alpha·基础"],
    )
    assert "《Alpha·基础》" in meta["note"] and "[[" not in meta["note"]


def test_assemble_never_invents_neighbours():
    """★★ 「相邻」必须是**真的相关**的笔记，不能由 `assemble` 自己瞎挑。

    原来它按字典序取前两个 → 写缓存那篇时"相邻"成了
    《01-前端基础地图》《02-三层全景》，**跟主题毫无关系**（真模型跑一次才看见）。
    没给 `related` 就干脆不写"相邻" —— 宁可没有，也不要假链接。
    """
    meta, _, _ = generate.assemble(
        _plan(), domain_tag="领域/甲",
        known_names={"Alpha·基础", "01-前端基础地图", "02-三层全景"},
    )
    assert "01-前端基础地图" not in meta["note"]
    assert "相邻" not in meta["note"]


# ===========================================================================
# 3. parse_plans —— 模型输出要容错
# ===========================================================================


def test_parse_plans_normal():
    data = {"notes": [{"title": "A", "subtitle": "s", "aliases": ["a"], "tags": ["t"],
                       "sections": [{"heading": "一", "points": ["p"]}]}]}
    plans = generate.parse_plans(data)
    assert len(plans) == 1 and plans[0].title == "A"
    assert plans[0].sections[0].points == ["p"]


def test_parse_plans_tolerates_shapes():
    """模型会飘：章节写成字符串、别名写成单个字符串、缺 sections。"""
    data = {"notes": [
        {"title": "A", "aliases": "只有一个", "sections": ["一、甲", "二、乙"]},
        {"title": "没章节的"},
        "根本不是对象",
    ]}
    plans = generate.parse_plans(data)
    assert len(plans) == 1
    assert plans[0].aliases == ["只有一个"]
    assert [s.heading for s in plans[0].sections] == ["一、甲", "二、乙"]


def test_parse_plans_handles_garbage():
    for bad in (None, [], {}, {"notes": "不是数组"}, {"notes": [None, 1]}):
        assert generate.parse_plans(bad) == []


def test_parse_plans_respects_max_notes():
    data = {"notes": [{"title": f"N{i}", "sections": [{"heading": "h"}]} for i in range(9)]}
    assert len(generate.parse_plans(data, max_notes=2)) == 2


def test_clean_section_removes_duplicated_heading():
    """模型常自己再写一遍标题行 —— 而标题是 `assemble()` 加的，重复了很难看。"""
    got = generate._clean_section("## 一、什么是陈旧读\n\n正文。", "一、什么是陈旧读")
    assert not got.startswith("#")
    assert got.startswith("正文")


def test_clean_section_drops_trailing_pleasantries():
    got = generate._clean_section("正文内容。\n\n希望对你有所帮助", "一、x")
    assert "希望对你有所帮助" not in got


# ===========================================================================
# 4. 预览
# ===========================================================================


def test_render_preview_has_filename_dir_and_outline():
    p = _plan()
    p.sections[0].body = "x" * 100
    p.sections[1].body = "y" * 100
    text = generate.render_preview([p], dir_rel="领域/甲领域", domain_tag="领域/甲",
                                   related=["Alpha·基础"])
    assert "缓存一致性 · 陈旧读与失效.md" in text
    assert "领域/甲领域" in text
    assert "一、什么是陈旧读" in text
    assert "[[Alpha·基础]]" in text
    assert "回「落」" in text


# ===========================================================================
# 5. 端到端：生成 → 预览 → 落盘
# ===========================================================================


@pytest.fixture
def kb(tmp_path, monkeypatch):
    root = make_vault(tmp_path / "vault")
    monkeypatch.setattr(config, "KNOWLEDGE_VAULT_DIR", str(root))
    catalog.reset_cache()
    yield root
    catalog.reset_cache()


_counter = {"n": 0}


def _msg(text: str, peer: str = "") -> InboundMessage:
    if not peer:
        _counter["n"] += 1
        peer = f"p4-gen-{_counter['n']}"
    return InboundMessage(peer_id=peer, text=text, context_token="ct-test")


#: 一篇恒定的假大纲（1 篇 2 节）。**注意是 JSON 字符串** ——
#: 现在走 `chat_text` 路径（原因见 `generate.py` 顶部那段：这家供应商不支持
#: json_schema，而模型会返回裸数组，`chat_json` 判定失败）。
_FAKE_OUTLINE = json.dumps({
    "notes": [{
        "title": "缓存一致性",
        "subtitle": "陈旧读",
        "aliases": ["缓存一致性"],
        "tags": ["缓存"],
        "sections": [
            {"heading": "一、什么是陈旧读", "points": ["定义"]},
            {"heading": "二、怎么避免", "points": ["失效策略"]},
        ],
    }]
}, ensure_ascii=False)

_FAKE_FINISH = json.dumps({
    "intro": "> 目标：搞清缓存。",
    "note": "只讲缓存一致性。",
    "outro": ["- 相邻：Alpha·基础"],
}, ensure_ascii=False)

_LONG = "缓存一致性讲的是多份副本之间的一致问题。" * 12        # ≥120 字，避免被判"太短"


def _fake_llm(monkeypatch):
    import app.services.llm.client as client_mod
    from conftest import FakeLLM

    fake = FakeLLM([
        _FAKE_OUTLINE,                                        # ① 拆题
        _LONG, _LONG,                                         # ② 两节
        _FAKE_FINISH,                                         # ③ 收尾
    ])
    monkeypatch.setattr(client_mod, "get_client", lambda **kw: fake)
    return fake


def test_parse_outline_text_accepts_bare_array():
    """★★ 回归锁定：模型会返回**裸数组**而不是 `{"notes": [...]}`。

    这是实测撞到的：`chat_json` 的 text 策略只认 JSON **对象**，
    于是"模型答得完全正确"却被判定成失败。所以自己抽，两种形状都认。
    """
    bare = '[{"title": "A", "sections": [{"heading": "一"}]}]'
    plans = generate.parse_outline_text(bare)
    assert len(plans) == 1 and plans[0].title == "A"


def test_parse_outline_text_accepts_fenced_object():
    text = '```json\n{"notes": [{"title": "B", "sections": [{"heading": "h"}]}]}\n```'
    plans = generate.parse_outline_text(text)
    assert len(plans) == 1 and plans[0].title == "B"


def test_parse_outline_text_returns_empty_on_garbage():
    for bad in ("", "我写不出来", "{}", "[1,2,3]"):
        assert generate.parse_outline_text(bad) == []


def test_parse_json_any_shapes():
    assert generate.parse_json_any('{"a": 1}') == {"a": 1}
    assert generate.parse_json_any("随便说说 [1, 2] 后面还有话") == [1, 2]
    assert generate.parse_json_any("没有 JSON") is None


def test_e2e_generate_then_save(kb, monkeypatch):
    """走完整条链：聊 → 总结（出预览）→ 落（写盘）。"""
    _fake_llm(monkeypatch)
    peer = "p4-gen-e2e"

    conversation.handle(_msg("我想学缓存", peer))
    conversation.handle(_msg("1", peer))                       # 聚焦 Alpha·基础

    preview = conversation.handle(_msg("帮我总结成笔记", peer))
    assert preview and "拟写" in preview and "缓存一致性" in preview

    # ★★ 预览之后、落盘之前：**库里一个字节都不能变**
    before = snapshot_fingerprint(kb)
    assert "缓存一致性" not in "\n".join(before)

    saved = conversation.handle(_msg("落", peer))
    assert saved and "写好了" in saved

    after = snapshot_fingerprint(kb)
    added = set(after) - set(before)
    assert any("缓存一致性" in a for a in added), added
    # 原有文件仍然一个字节没动
    changed = {k for k in before if before.get(k) != after.get(k)}
    assert changed == set()


def test_e2e_preview_alone_does_not_write(kb, monkeypatch):
    """★★ 只看预览、不说「落」→ **绝不落盘**（需求 5）。"""
    _fake_llm(monkeypatch)
    peer = "p4-gen-preview-only"
    before = snapshot_fingerprint(kb)

    conversation.handle(_msg("我想学缓存", peer))
    conversation.handle(_msg("1", peer))
    conversation.handle(_msg("帮我总结成笔记", peer))

    assert snapshot_fingerprint(kb) == before


def test_e2e_drop_discards(kb, monkeypatch):
    """说「算了」→ 不落盘，且下次不会再写。"""
    _fake_llm(monkeypatch)
    peer = "p4-gen-drop"
    before = snapshot_fingerprint(kb)

    conversation.handle(_msg("我想学缓存", peer))
    conversation.handle(_msg("1", peer))
    conversation.handle(_msg("总结一下", peer))
    reply = conversation.handle(_msg("算了", peer))
    assert reply and "不写了" in reply
    assert snapshot_fingerprint(kb) == before
    # 再说「落」应该被告知"没有待写的东西"
    assert "没有待写" in conversation.handle(_msg("落", peer))


def test_e2e_save_without_pending(kb):
    """没有待落盘内容时说「落」→ 明确告知，而不是静默写个空东西。"""
    reply = conversation.handle(_msg("落"))
    assert reply and "没有待写" in reply


def test_e2e_missing_topic_asks_back(kb):
    """光说「总结」但没主题也没聚焦 → 反问，不瞎猜。"""
    reply = conversation.handle(_msg("总结一下"))
    assert reply and "总结哪一块" in reply
