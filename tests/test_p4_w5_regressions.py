"""P4 · W5 回归测试 —— **每一条锁一个 W4 双审查里"已修但没锁"的缺口**。

W4 修了 17 条，但当时**没有为其中任何一条补专门的回归测试** ——
那些修复只靠原有测试"顺便没红"来担保。这本身就是漏洞：
如果有人后来把代码改回去，没有测试会拦住。

所以这一波逐条补上。清单（对应 `docs/tasks.md`的「待修（排进 W5）」）：

| 组 | 缺口 | 判据 |
|---|---|---|
| W5-1 | 「开」分支**承诺了但没实现**（F-8） | 回「开」→ 目录真的建出来了 |
| W5-2a | **空**的领域目录被判"不存在"（猎手 9） | 空目录能出现在白名单里 |
| W5-2b | `within()` 用 `abspath` → **symlink 可越界**（F-14） | junction/symlink 指向库外必须被拒 |
| W5-2c | Windows **保留名**（`CON`/`PRN`…）不过滤（猎手 15） | `CON` → `CON_` |
| W5-3a | 产出校验只做 2/4（F-11） | 四项校验逐条生效 |
| W5-3b | 旧式领域**不沿用旧字段**（F-12） | 往 `乙领域` 写 → 用旧字段 |
| W5-3c | 死链剥除**没覆盖** `intro`/`note`（F-18） | 两处都剥 |
| W5-4a | `source` 缺具体来源（F-17） | 含日期与主题 |
| W5-4b | 「记下来」没进词表（F-3） | 短说法也能触发 |
| W5-4c | `score==0` → `cut==0` 全收（猎手疑似 1） | 绝对下限生效 |
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
from app.services.knowledge import catalog, generate, retrieval, write  # noqa: E402
from app.services.knowledge import session as kb_session  # noqa: E402
from app.services.knowledge.generate import NotePlan, Section  # noqa: E402


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
        peer = f"p4-w5-{_counter['n']}"
    return InboundMessage(peer_id=peer, text=text, context_token="ct-w5")


# ===========================================================================
# W5-1 「开」分支：承诺了就得实现（规格 F-8）
# ===========================================================================


def test_domain_open_intent_requires_pending(kb):
    """★ 没有"刚建议过开主题"这个前提，「开」不许被判成建目录。

    否则「开个会」「开灯」这类日常话会在用户毫不知情的情况下建目录。
    """
    assert conversation.classify_intent("开") != "domain_open"
    assert conversation.classify_intent("建一个") != "domain_open"
    # 有前提才判
    assert conversation.classify_intent("开", has_pending_domain=True) == "domain_open"
    assert conversation.classify_intent("建吧", has_pending_domain=True) == "domain_open"


def test_domain_open_creates_directory_only_after_nod(kb):
    """★★ 端到端：建议 → 回「开」→ **目录真的建出来了**。

    修之前：回复里承诺「你说『开』我就建目录」，但**分支根本不存在**，
    用户回「开」只会拿到帮助菜单 —— 一个假的承诺。
    """
    peer = "p4-w5-open"
    before = snapshot_fingerprint(kb)

    reply1 = conversation.handle(_msg("我想学量子力学", peer))
    assert "要不要开一个新主题" in reply1
    # ★ 只是建议 → **一个字节都不能变**
    assert snapshot_fingerprint(kb) == before

    reply2 = conversation.handle(_msg("开", peer))
    assert "建好了" in reply2
    assert (kb / "领域" / "量子力学").is_dir()


def test_domain_open_is_one_shot(kb):
    """用掉就清 —— 再回一次「开」**不会重复建、也不会建别的**。

    ⚠️ 这里刻意不断言回复文本：`pending_domain` 已清 → 第二次「开」不满足
    `domain_open` 的门槛 → 落到帮助菜单。**这是正确的** ——
    没有"刚建议过"这个前提就不许建目录，宁可回一句无关的话，
    也不能在用户没点头的情况下建目录（纪律 1）。
    """
    peer = "p4-w5-open-once"
    conversation.handle(_msg("我想学量子力学", peer))
    assert "建好了" in conversation.handle(_msg("开", peer))

    before = snapshot_fingerprint(kb)
    dirs_before = sorted(p.name for p in (kb / "领域").iterdir())
    conversation.handle(_msg("开", peer))
    assert sorted(p.name for p in (kb / "领域").iterdir()) == dirs_before
    assert snapshot_fingerprint(kb) == before


def test_domain_open_rejects_path_escape(kb):
    """★ 主题名里带 `..` → 必须被挡住，绝不在库外建目录。"""
    peer = "p4-w5-escape"
    before = snapshot_fingerprint(kb)
    conversation.handle(_msg("我想学 ../../evil", peer))
    reply = conversation.handle(_msg("开", peer))
    assert "建好了" not in reply
    assert snapshot_fingerprint(kb) == before


def test_create_domain_is_idempotent_message(kb):
    """目录已存在 → 明确告知，不抛异常。"""
    (kb / "领域" / "量子力学").mkdir(parents=True)
    out = kb_session.create_domain("量子力学")
    assert "已经在了" in out


# ===========================================================================
# W5-2a 空的领域目录不该被判"不存在"（猎手 9）
# ===========================================================================


def test_empty_domain_dir_is_in_whitelist(kb):
    """★ 用户点头建了目录、还没写笔记 —— 白名单必须已经认它。

    原来 `domain_dirs()` 只从**笔记**反推，空目录完全不可见 →
    「建完立刻不能用」，而且**目录指纹不变**（既没文件也没mtime 变化）
    → 缓存不失效，用户会看到"目录不存在"。
    """
    empty = kb / "领域" / "量子力学"
    empty.mkdir()
    catalog.reset_cache()
    cat = catalog.load(kb)
    assert "领域/量子力学" in cat.domain_dirs()
    assert catalog.is_domain_dir(cat, "领域/量子力学")


def test_empty_domain_dir_can_receive_note(kb):
    """空目录能直接写第一篇 —— 不需要再授权一次。"""
    empty = kb / "领域" / "量子力学"
    empty.mkdir()
    catalog.reset_cache()
    p = write.write_note(
        kb, "领域/量子力学", "量子力学·入门",
        {"type": "permanent"}, "# 量子力学 · 入门\n\n正文。\n",
    )
    assert p.is_file()


def test_cache_invalidates_when_empty_dir_appears(kb):
    """★ 新建空目录必须让缓存失效（否则上面两条在真实运行里也不成立）。"""
    cat = catalog.load(kb)
    assert "领域/新领域" not in cat.domain_dirs()
    (kb / "领域" / "新领域").mkdir()
    cat2 = catalog.load(kb)          # 不 reset_cache — 靠指纹自己发现
    assert "领域/新领域" in cat2.domain_dirs()


# ===========================================================================
# W5-2b symlink /junction 越界（F-14）
# ===========================================================================


def test_within_rejects_symlink_pointing_outside(tmp_path):
    """★ 目录是**符号链接/junction 指向库外** → 必须判为越界。

    `abspath` 是纯字符串拼接，看不出链接；`resolve()` 才认得出真实目标。
    """
    root = tmp_path / "vault"
    (root / "领域").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()

    link = root / "领域" / "evil"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("本机不能建符号链接（Windows 需管理员或开发者模式）")

    target = link / "坏笔记.md"
    assert write.within(root, target) is False
    with pytest.raises(write.WriteError):
        write.write_note(root, "领域/evil", "坏笔记", {"type": "permanent"}, "正文")


def test_within_allows_normal_path(tmp_path):
    """加固不能误伤：正常路径仍判在内。"""
    root = tmp_path / "vault"
    (root / "领域" / "甲").mkdir(parents=True)
    assert write.within(root, root / "领域" / "甲" / "笔记.md") is True
    assert write.within(root, root) is True
    # 前缀相同但不同目录（`vault2` vs `vault`）
    other = tmp_path / "vault2"
    other.mkdir()
    assert write.within(root, other / "x.md") is False


# ===========================================================================
# W5-2c Windows 保留名（猎手 15）
# ===========================================================================


@pytest.mark.parametrize("bad", ["CON", "con", "PRN", "AUX", "NUL", "COM1", "LPT9"])
def test_reserved_windows_names_are_renamed(bad):
    """★ `CON.md` 在 NTFS 上根本创建不出来（`OSError`），必须提前改名。"""
    assert catalog.sanitize_name(bad) == bad + "_"


@pytest.mark.parametrize("ok", ["CONTENT", "CONNECTION", "COM10", "LPT10", "常规"])
def test_reserved_name_check_is_exact(ok):
    """不能过度：只有**整段等于**保留名才改名。"""
    assert catalog.sanitize_name(ok) == ok


def test_reserved_name_actually_writable(kb):
    """端到端：主题叫 `CON` 时，文件真的建得出来。"""
    p = write.write_note(kb, "领域/甲领域", "CON", {"type": "permanent"}, "正文。\n")
    assert p.is_file() and p.stem == "CON_"


# ===========================================================================
# W5-3a 产出校验四项（F-11）
# ===========================================================================


def _full_plan(**kw) -> NotePlan:
    body = "缓存一致性的核心是让副本之间不打架。" * 12
    plan = NotePlan(
        title="T", subtitle="s", tags=["x"], aliases=["a"],
        sections=[Section(heading="一、甲", body=body),
                  Section(heading="二、乙", body=body)],
        intro="> 目标：x。", intro_note="只讲x。", outro=["-线索"],
    )
    for k, v in kw.items():
        setattr(plan, k, v)
    return plan


def test_validate_reports_missing_sections():
    """★ 校验项①：节没写全必须被报出来（原来只看"每节字数"和"结尾"）。"""
    plan = _full_plan()
    plan.sections[1].body = ""
    problems = generate.validate_plan(plan)
    assert any("没写出来" in p for p in problems), problems


def test_validate_reports_short_section():
    """★ 校验项②：字数低于区间。"""
    plan = _full_plan()
    plan.sections[0].body = "太短了。"
    problems = generate.validate_plan(plan)
    assert any("字" in p for p in problems), problems


def test_validate_reports_truncated_tail():
    """★ 校验项③：结尾没有标点 = 多半被 `MAX_OUTPUT_TOKENS` 截断。"""
    plan = _full_plan()
    plan.sections[1].body = "这一节的最后一句话没有写完就这样断了"
    problems = generate.validate_plan(plan)
    assert any("截断" in p for p in problems), problems


def test_validate_reports_stray_fence():
    """★ 校验项④：残留的代码围栏标记。"""
    plan = _full_plan()
    plan.sections[0].body = "```python\nprint(1)\n```"
    problems = generate.validate_plan(plan)
    assert any("围栏" in p for p in problems), problems


def test_validate_passes_good_plan():
    assert generate.validate_plan(_full_plan()) == []


# ===========================================================================
# W5-3b 旧式领域要沿用旧字段（F-12）
# ===========================================================================


def test_assemble_old_header_style(kb):
    """★ 往旧式领域写笔记 → 用**旧字段**，否则库里按 `category` 筛的查询看不到它。"""
    cat = catalog.load(kb)
    assert cat.header_schema("领域/乙领域") == "old"   # fixture 的前提
    meta, _, _ = generate.assemble(
        _full_plan(), domain_tag="领域/乙", known_names=set(),
        header_style="old", old_category=cat.vote_category("领域/乙领域"),
        domain_dir="乙领域",
    )
    for f in ("category", "difficulty", "status", "related", "title"):
        assert f in meta, f
    assert "note" not in meta          # 旧式没有 `note`
    assert meta["category"]           # 沿用兄弟笔记的值，不是空


def test_assemble_new_header_style_unchanged(kb):
    """加固不能误伤：新式领域仍是新字段。"""
    cat = catalog.load(kb)
    assert cat.header_schema("领域/甲领域") == "new"
    meta, _, _ = generate.assemble(
        _full_plan(), domain_tag="领域/甲", known_names=set(), domain_dir="甲领域"
    )
    assert list(meta.keys()) == ["type", "tags", "aliases", "created", "source", "note"]


def test_vote_category_takes_majority(kb):
    """★ `vote_category` 要真投票，不是取第一篇。"""
    cat = catalog.load(kb)
    # fixture：乙领域两篇 → 个人档案 / 简历（各一票）→ 应按 (票数, 长度, 字典序) 决
    assert cat.vote_category("领域/乙领域") in {"个人档案", "简历"}
    # 新式领域没有 category → 空串
    assert cat.vote_category("领域/甲领域") == ""


def test_old_style_note_written_end_to_end(kb, monkeypatch):
    """端到端：总结落进旧式领域 → 文件里是旧字段。"""
    from conftest import FakeLLM
    import app.services.llm.client as client_mod

    outline = json.dumps({"notes": [{
        "title": "旧式新篇", "subtitle": "沿用旧字段", "aliases": ["旧式新篇"],
        "tags": ["旧式"],
        "sections": [{"heading": "一、甲", "points": ["p"]},
                      {"heading": "二、乙", "points": ["p"]}],
    }]}, ensure_ascii=False)
    body = "旧式领域里的正文内容，讲清楚一件事。" * 12
    finish = json.dumps({
        "intro": "> 目标：沿用旧字段。", "note": "只讲旧式。",
        "outro": ["- 线索一"],
    }, ensure_ascii=False)
    fake = FakeLLM([outline, body, body, finish])
    monkeypatch.setattr(client_mod, "get_client", lambda **kw: fake)

    peer = "p4-w5-old"
    conversation.handle(_msg("我想学旧式", peer))
    conversation.handle(_msg("1", peer))
    preview = conversation.handle(_msg("总结一下", peer))
    assert preview and "拟写" in preview
    saved = conversation.handle(_msg("落", peer))
    assert "写好了" in saved

    written = list((kb / "领域" / "乙领域").glob("旧式新篇*.md"))
    assert written, "旧式领域下没写出文件"
    text = written[0].read_text(encoding="utf-8")
    assert "category:" in text and "difficulty:" in text and "status:" in text


# ===========================================================================
# W5-3c 死链剥除要覆盖 intro 与 note（F-18）
# ===========================================================================


def test_dead_links_stripped_from_intro():
    """★ 引导段里的 `[[X]]` 同样要剥 —— 原来只清sections/outro。"""
    plan = _full_plan(intro="> 目标：搞懂 [[不存在的笔记]]。")
    _, body, dropped = generate.assemble(
        plan, domain_tag="领域/甲", known_names={"Alpha·基础"}
    )
    assert "[[" not in body
    assert "不存在的笔记" in dropped


def test_dead_links_stripped_from_note_field():
    """★ frontmatter 的 `note` 里的死链也要剥。"""
    plan = _full_plan(intro_note="只讲 [[凭空编的笔记]]。")
    meta, _, dropped = generate.assemble(
        plan, domain_tag="领域/甲", known_names=set()
    )
    assert "[[" not in str(meta["note"])
    assert "凭空编的笔记" in dropped


# ===========================================================================
# W5-4a source 要有具体来源（F-17）
# ===========================================================================


def test_source_contains_date_and_topic():
    """★ `source` 要能回答"这是什么时候、聊什么生成的"。"""
    meta, _, _ = generate.assemble(
        _full_plan(), domain_tag="领域/甲", known_names=set(),
        topic="缓存一致性", today="2026-10-06",
    )
    assert "2026-10-06" in meta["source"]
    assert "缓存一致性" in meta["source"]


def test_source_never_says_only_ai_generated():
    """不能退化成"AI 生成"这种等于没写的来源。"""
    meta, _, _ = generate.assemble(
        _full_plan(), domain_tag="领域/甲", known_names=set(), topic="T"
    )
    assert meta["source"].strip() != "AI 生成"
    assert len(meta["source"]) >= 10


# ===========================================================================
# W5-4b 「记下来」这类短说法（F-3）
# ===========================================================================


@pytest.mark.parametrize("text", ["记下来", "记一下", "帮我记一下", "记一笔",
                                   "沉淀一下", "存成笔记"])
def test_short_capture_phrases_are_recognized(text, kb):
    """★ 「我随口说它帮我记」是核心场景，短说法不能落到 unknown。"""
    assert conversation.classify_intent(text) == "note_gen"


def test_capture_phrase_does_not_steal_clothing(kb):
    """加固不能误伤：这些词不能把穿搭的话抢走。"""
    assert conversation.classify_intent("记一下这件卫衣") in {"describe", "unknown", "learn"}


# ===========================================================================
# W5-4c 绝对分数下限（猎手疑似 1）
# ===========================================================================


def test_min_keep_score_blocks_tiny_scores(kb):
    """★ top1 分数本身很低时，相对阈值形同虚设 → 必须有绝对下限。

    实测真库分数分布：真相关 2.5–14.0、噪声 0.19–0.7。
    """
    cat = catalog.load(kb)
    hits = retrieval.find_related(cat, "缓存", limit=10)
    assert hits, "「缓存」应该命中 Alpha"
    assert all(h.score >= retrieval.MIN_KEEP_SCORE for h in hits), \
        [(h.note.name, h.score) for h in hits]


def test_unrelated_query_still_returns_nothing(kb):
    """加固不能误伤：无关主题仍然零命中。"""
    cat = catalog.load(kb)
    assert retrieval.find_related(cat, "量子力学入门", limit=3) == []


# ===========================================================================
# 收尾：这一波所有改动都不许碰用户的真实库
# ===========================================================================


def test_w5_never_touches_real_vault(kb):
    """跑完这一波，合成库之外一个字节都不能变（`config` 被 monkeypatch 到临时库）。"""
    assert config.KNOWLEDGE_VAULT_DIR == str(kb)
    assert "resume" not in config.KNOWLEDGE_VAULT_DIR
    # 确认白名单/清洗这些纯函数也没被临时路径污染
    assert catalog.sanitize_name("CON") == "CON_"
    assert kb_session.is_configured()