"""知识库技能的测试。

覆盖三层：
1. **frontmatter** —— 各种写法与脏输入（解析失败必须不抛）
2. **catalog / retrieval** —— 扫描、标签投票、词法检索、确定性
3. **write** —— 六条安全约束（越界、白名单、不覆盖、原子写、不留残留）

★ 全程只碰**合成 vault**（`tmp_path`）—— 绝不指向使用者的真实笔记库。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from vault_fixture import make_vault, note_names, snapshot_fingerprint  # noqa: E402

from app.services.knowledge import catalog, frontmatter, retrieval, write  # noqa: E402


@pytest.fixture
def vault(tmp_path):
    """合成知识库。缓存按路径分键，但仍显式清一次，避免跨用例串味。"""
    root = make_vault(tmp_path / "vault")
    catalog.reset_cache()
    yield root
    catalog.reset_cache()


# ===========================================================================
# 1. frontmatter
# ===========================================================================


def test_parse_scalar_and_inline_list():
    text = "---\ntype: permanent\ntags: [a, b, c]\ncreated: 2026-01-01\n---\n\n# 标题\n\n正文\n"
    meta, body = frontmatter.parse(text)
    assert meta["type"] == "permanent"
    assert meta["tags"] == ["a", "b", "c"]
    assert meta["created"] == "2026-01-01"      # ★ 不能变成 date 对象
    assert body.strip().startswith("# 标题")


def test_parse_block_list():
    text = "---\naliases:\n  - 甲\n  - 乙\n---\n正文\n"
    meta, _ = frontmatter.parse(text)
    assert meta["aliases"] == ["甲", "乙"]


def test_parse_strips_quotes_but_keeps_inner_commas():
    """★ 不能对值直接 `split(",")` —— 引号里的逗号是一个整体。"""
    text = '---\ntags: ["含, 逗号的", 普通]\nnote: "外面有引号"\n---\n正文\n'
    meta, _ = frontmatter.parse(text)
    assert meta["tags"] == ["含, 逗号的", "普通"]
    assert meta["note"] == "外面有引号"


def test_parse_value_with_chinese_colon_and_book_marks():
    """真实库的 `note` 就长这样（含《》与中文冒号），必须原样保留。"""
    val = "通用方法见《投资方法论·估值与择时》，因子框架见《红利低波·因子与专题》。"
    meta, _ = frontmatter.parse(f"---\nnote: {val}\n---\n正文\n")
    assert meta["note"] == val


def test_parse_no_frontmatter():
    meta, body = frontmatter.parse("# 就是篇正文\n\n没有头部\n")
    assert meta == {}
    assert body.startswith("# 就是篇正文")


def test_parse_unclosed_delimiter_treated_as_no_frontmatter():
    """★ 只有开头没有结尾 → 当没有 frontmatter（返回 `({}, 原文)`）。"""
    text = "---\ntype: permanent\n\n# 标题\n\n正文（这里没有闭合分隔线）\n"
    meta, body = frontmatter.parse(text)
    assert meta == {}
    assert body == text


def test_parse_body_containing_hr_not_truncated():
    """★ 孪生风险：`---` 既是 frontmatter 边界、也是正文的 Markdown 分隔线。

    必须只认**第一个闭合**的那个，否则正文会被腰斩。
    """
    text = "---\ntype: permanent\n---\n\n# 标题\n\n---\n\n下半部分\n"
    meta, body = frontmatter.parse(text)
    assert meta == {"type": "permanent"}
    assert "下半部分" in body
    assert body.strip().startswith("# 标题")


def test_parse_never_raises_on_garbage():
    """★ 纪律：任何垃圾输入都不抛异常（扫描一篇坏笔记不能毁掉整库）。"""
    garbage = [
        "", "---", "---\n", "---\n---", "---\n: :\n---\n",
        "---\n[[[列表\n---\n", "---\n" + "a" * 5000 + ": 1\n---\n",
        "\x00\x01\x02", "---\nkey: \"未闭合的引号\n---\n",
    ]
    for g in garbage:
        meta, body = frontmatter.parse(g)
        assert isinstance(meta, dict)
        assert isinstance(body, str)


def test_render_roundtrip():
    """render → parse 要能还原（写侧与读侧自洽）。"""
    meta = {
        "type": "permanent",
        "tags": ["指数", "领域/金融"],
        "aliases": ["别名一", "别名二"],
        "created": "2026-08-30",
        "source": "与 AI 助手对话学习整理",
        "note": "只讲…。通用方法见《投资方法论·估值与择时》。",
    }
    body = "# 主标题 · 副标题\n\n> 目标：测试。\n"
    text = frontmatter.render(meta, body)
    got, got_body = frontmatter.parse(text)
    assert got == meta
    assert got_body.strip() == body.strip()


def test_render_escapes_colon_in_value():
    """值里有冒号 → 输出时加引号，读回来仍是原值。"""
    meta = {"source": "见 http://example.com 与 A:B"}
    text = frontmatter.render(meta, "正文")
    got, _ = frontmatter.parse(text)
    assert got["source"] == meta["source"]


def test_as_list_normalizes():
    assert frontmatter.as_list(None) == []
    assert frontmatter.as_list("") == []
    assert frontmatter.as_list("单个") == ["单个"]
    assert frontmatter.as_list(["a", "", "b"]) == ["a", "b"]


# ===========================================================================
# 2. catalog
# ===========================================================================


def test_scan_finds_every_md(vault):
    assert len(catalog.load(vault).notes) == len(note_names(vault))


def test_schema_detection(vault):
    cat = catalog.load(vault)
    by = {n.name: n for n in cat.notes}
    assert by["Alpha·基础"].schema == "new"          # 有 type
    assert by["Delta·旧式"].schema == "old"          # 有 category
    assert by["坏掉的"].schema == "none"             # frontmatter 没闭合
    assert by["空的"].schema == "none"               # 空文件


def test_broken_note_does_not_break_scan(vault):
    """★ 有坏笔记时，其余笔记必须照常扫出来。"""
    cat = catalog.load(vault)
    names = {n.name for n in cat.notes}
    assert {"Alpha·基础", "Beta·进阶", "Gamma·陷阱", "坏掉的", "空的"} <= names


def test_domain_dirs(vault):
    # 用 set 比较：排序是按 Unicode 码点（"乙" < "甲"），顺序不是契约
    assert set(catalog.load(vault).domain_dirs()) == {"领域/甲领域", "领域/乙领域"}


def test_vote_domain_tag_uses_majority_not_dirname(vault):
    """★★ 甲领域下 3 篇都写 `领域/甲`（与目录名"甲领域"不同）→ 必须投票得到 `领域/甲`。"""
    cat = catalog.load(vault)
    assert cat.vote_domain_tag("领域/甲领域") == "领域/甲"


def test_vote_domain_tag_empty_when_no_tags(vault):
    """乙领域是旧式笔记，没有 `领域/*` 标签 → 投票结果为空（调用方据此回落到目录名）。"""
    assert catalog.load(vault).vote_domain_tag("领域/乙领域") == ""


def test_header_schema_votes_for_old(vault):
    assert catalog.load(vault).header_schema("领域/乙领域") == "old"
    assert catalog.load(vault).header_schema("领域/甲领域") == "new"


def test_compact_lines_excludes_maps_and_structures(vault):
    lines = catalog.load(vault).compact_lines()
    joined = "\n".join(lines)
    assert "甲领域地图" not in joined
    assert "收件箱" not in joined
    assert "Alpha·基础" in joined


def test_load_is_cached_until_tree_changes(vault):
    a = catalog.load(vault)
    b = catalog.load(vault)
    assert a is b                      # 命中缓存
    (vault / "领域" / "甲领域" / "新的.md").write_text(
        "---\ntype: permanent\ntags: [x]\nnote: 新的\n---\n# 新的\n", encoding="utf-8"
    )
    c = catalog.load(vault)
    assert c is not a                  # 指纹变了 → 重建
    assert "新的" in {n.name for n in c.notes}


# ---- sanitize_name ----


def test_sanitize_name_strips_path_separators():
    assert catalog.sanitize_name("a/b\\c") == "abc"


def test_sanitize_name_strips_illegal_windows_chars():
    assert catalog.sanitize_name('a:b*c?d"e<f>g|h') == "abcdefgh"


def test_sanitize_name_kills_traversal():
    """★ 防目录穿越的第一道闸。"""
    assert ".." not in catalog.sanitize_name("../../etc/passwd")
    assert "/" not in catalog.sanitize_name("../../etc/passwd")


def test_sanitize_name_trims_trailing_dot_and_space():
    """Windows 不允许文件以点或空格结尾。"""
    assert catalog.sanitize_name("名字. ") == "名字"
    assert catalog.sanitize_name("  名字  ") == "名字"


def test_sanitize_name_truncates():
    assert len(catalog.sanitize_name("名" * 200)) == catalog.MAX_NAME_LEN


def test_sanitize_name_empty():
    assert catalog.sanitize_name("") == ""
    assert catalog.sanitize_name("   ") == ""
    assert catalog.sanitize_name("...") == ""


def test_join_name_uses_library_convention():
    """实测写法是 `主标题 · 副标题`（空格 + 中点 + 空格）。"""
    assert catalog.join_name("RSI与BIAS", "振荡指标辨析") == "RSI与BIAS · 振荡指标辨析"
    assert catalog.join_name("只有主标题", "") == "只有主标题"
    assert catalog.join_name("", "只有副标题") == "只有副标题"
    assert catalog.join_name("", "") == ""


# ===========================================================================
# 3. retrieval
# ===========================================================================


def test_normalize_topic_strips_stopwords():
    assert "怎么" not in retrieval.normalize_topic("缓存怎么用")
    assert "缓存" in retrieval.normalize_topic("我想学缓存")


def test_stopwords_are_stripped_longest_first():
    """★★ 回归锁定：剥停用词必须**长词优先**。

    否则「黄金**为什么**涨」会被 "什么" 先拆成「黄金为 涨」——
    残字「为」粘在实词上，查询词变成「黄金为」，库里自然查不到，
    **整条查询静默失效**（2026-10-06 真库回归时抓到，命中数从 1 变 0）。
    """
    assert "为" not in retrieval.normalize_topic("黄金为什么涨")
    assert retrieval.query_terms("黄金为什么涨") == ["黄金"]
    assert retrieval.normalize_topic("为什么") == ""


def test_grams_two_chars():
    assert retrieval.grams("缓存") == {"缓存"}
    assert retrieval.grams("") == set()
    assert retrieval.grams("a") == {"a"}


def test_find_related_hits_expected_note(vault):
    cat = catalog.load(vault)
    hits = retrieval.find_related(cat, "我想学缓存", limit=3)
    assert hits and hits[0].note.name == "Alpha·基础"


def test_find_related_returns_empty_for_unrelated(vault):
    """★★ 返回 `[]` 的语义 = "库里没有这个主题" → 调用方据此建议开新领域。"""
    cat = catalog.load(vault)
    for q in ("量子力学入门", "怎么做红烧肉", "推荐个手机", "abandon", "zzzz"):
        assert retrieval.find_related(cat, q, limit=3) == [], q


def test_find_related_is_deterministic(vault):
    cat = catalog.load(vault)
    a = [(h.note.name, h.score) for h in retrieval.find_related(cat, "并发与锁", limit=5)]
    b = [(h.note.name, h.score) for h in retrieval.find_related(cat, "并发与锁", limit=5)]
    assert a == b and a


def test_find_related_respects_limit(vault):
    cat = catalog.load(vault)
    assert len(retrieval.find_related(cat, "缓存 并发 锁 陷阱", limit=1)) <= 1


def test_recent_in_dir_excludes_maps(vault):
    got = retrieval.recent_in_dir(catalog.load(vault), "领域/甲领域", limit=5)
    assert got and all(not n.name.endswith("地图") for n in got)


def test_read_body_strips_frontmatter(vault):
    cat = catalog.load(vault)
    note = next(n for n in cat.notes if n.name == "Alpha·基础")
    body = retrieval.read_body(vault, note)
    assert body.startswith("# Alpha · 基础")
    assert "type: permanent" not in body


def test_read_body_missing_file_returns_empty(vault):
    cat = catalog.load(vault)
    note = next(n for n in cat.notes if n.name == "Alpha·基础")
    ghost = catalog.NoteMeta(rel="领域/甲领域/不存在.md", name="不存在", dir_rel="领域/甲领域", title="x")
    assert retrieval.read_body(vault, ghost) == ""


def test_domain_overview(vault):
    ov = retrieval.domain_overview(catalog.load(vault))
    assert ("领域/甲领域", 3, "领域/甲") in ov


# ===========================================================================
# 4. write —— 六条安全约束
# ===========================================================================


def _meta(name: str = "测试笔记") -> dict:
    return {
        "type": "permanent",
        "tags": ["测试", "领域/甲"],
        "aliases": [],
        "created": "2026-10-06",
        "source": "与 AI 助手对话学习整理",
        "note": f"{name} 的一句话定位。",
    }


def test_write_creates_note(vault):
    p = write.write_note(vault, "领域/甲领域", "新笔记·测试", _meta(), "# 新笔记 · 测试\n\n正文\n")
    assert p.exists()
    meta, body = frontmatter.parse(p.read_text(encoding="utf-8"))
    assert meta["type"] == "permanent"
    assert body.strip().startswith("# 新笔记 · 测试")


def test_write_rejects_outside_domain_root(vault):
    """★ 约束一：落点必须在 `领域/` 下。"""
    with pytest.raises(write.WriteError):
        write.write_note(vault, "inbox", "偷渡", _meta(), "正文")
    with pytest.raises(write.WriteError):
        write.write_note(vault, "", "偷渡", _meta(), "正文")


def test_write_rejects_traversal(vault):
    """★ 约束三：`..` 必须被吃掉，最终只能落在 `领域/` 下。

    `领域/../../../tmp` 清洗后变成 `领域/tmp` —— 目录不存在且未授权 → 拒绝。
    """
    with pytest.raises(write.WriteError):
        write.write_note(vault, "领域/../../../tmp", "越界", _meta(), "正文")
    # 确认没有任何文件被写到 vault 之外
    assert not (vault.parent / "tmp").exists()


def test_write_rejects_unknown_dir_without_permission(vault):
    """★ 约束一：不存在的目录必须先获得用户点头（`allow_new_dir=True`）。"""
    with pytest.raises(write.WriteError):
        write.write_note(vault, "领域/全新主题", "新主题笔记", _meta(), "正文")


def test_write_allows_new_dir_when_flagged(vault):
    p = write.write_note(
        vault, "领域/全新主题", "新主题笔记", _meta(), "正文", allow_new_dir=True
    )
    assert p.exists() and p.parent.name == "全新主题"


def test_write_rejects_deep_new_dir(vault):
    """★ 约束六：新目录**只建一级**。"""
    with pytest.raises(write.WriteError):
        write.write_note(vault, "领域/新主题/子目录", "深目录", _meta(), "正文", allow_new_dir=True)


def test_write_never_overwrites(vault):
    """★ 约束四：同名自动加 `-2`，**内容绝不覆盖**。"""
    p1 = write.write_note(vault, "领域/甲领域", "重复名", _meta(), "第一篇内容\n")
    p2 = write.write_note(vault, "领域/甲领域", "重复名", _meta(), "第二篇内容\n")
    assert p1 != p2
    assert p2.name == "重复名-2.md"
    assert "第一篇内容" in p1.read_text(encoding="utf-8")
    assert "第二篇内容" in p2.read_text(encoding="utf-8")


def test_write_sanitizes_filename(vault):
    p = write.write_note(vault, "领域/甲领域", '名字/带:非法*字符?', _meta(), "正文")
    assert p.name == "名字带非法字符.md"


def test_write_rejects_empty_name(vault):
    with pytest.raises(write.WriteError):
        write.write_note(vault, "领域/甲领域", "...", _meta(), "正文")


def test_write_leaves_no_temp_files(vault):
    """★ 约束五：原子写结束后不能留下 `.tmp-*` 残留。"""
    write.write_note(vault, "领域/甲领域", "干净落盘", _meta(), "正文")
    leftovers = [p.name for p in vault.rglob(".tmp-*")]
    assert leftovers == []


def test_write_creates_parent_dir_only_after_checks(vault):
    """被拒绝的落点**不该留下空目录**（mkdir 在所有校验之后）。"""
    with pytest.raises(write.WriteError):
        write.write_note(vault, "领域/不该建的", "x", _meta(), "正文")
    assert not (vault / "领域" / "不该建的").exists()


def test_write_only_adds_files_and_never_touches_existing(vault):
    """★★ 用户的核心边界：**只新建，绝不修改已有 33 篇**。

    用"全库文件 → 字节数"指纹断言：写新笔记前后，**所有原有文件的指纹必须完全一致**。
    """
    before = snapshot_fingerprint(vault)
    write.write_note(vault, "领域/甲领域", "又一篇新的·子主题", _meta(), "# 又一篇新的 · 子主题\n\n正文\n")
    after = snapshot_fingerprint(vault)

    changed = {k for k in before if before.get(k) != after.get(k)}
    added = set(after) - set(before)
    removed = set(before) - set(after)

    assert changed == set(), f"原有文件被改动了：{changed}"
    assert removed == set(), f"原有文件被删了：{removed}"
    assert added == {"领域/甲领域/又一篇新的·子主题.md"}


# ---- 路径守卫本身 ----


def test_within_semantics(tmp_path, vault):
    assert write.within(vault, vault / "领域" / "x.md") is True
    assert write.within(vault, vault) is True
    assert write.within(vault, vault.parent / "evil.md") is False
    assert write.within(vault, tmp_path / "other" / "x.md") is False


def test_within_is_case_insensitive_on_windows(vault):
    """★ Windows 大小写不敏感 —— 不能因为盘符/目录大小写不同就判成"越界"。"""
    upper = Path(str(vault).upper())
    assert write.within(vault, upper / "领域" / "x.md") is True


def test_map_line_format():
    assert write.map_line("RSI与BIAS·振荡指标辨析") == "- [[RSI与BIAS·振荡指标辨析]]"
