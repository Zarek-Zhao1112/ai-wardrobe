"""合成知识库 fixture —— **测试专用，绝不指向使用者的真实笔记库**。

造一个"缩小版真实库"：刻意混入真实库里存在的各种**脏数据与不一致**，
让测试能在不碰真库的前提下覆盖它们：

| 特征 | 模拟的真实情况 |
|---|---|
| `领域/甲领域/` 下笔记写 `领域/甲` 标签 | 目录名与标签不符（真实：金融投资→`领域/金融`） |
| `领域/乙领域/` 用 `category:` 旧式字段 | 旧式 schema（真实：Web开发 01–07） |
| 一篇没有闭合 `---` 的 | 解析失败必须不抛异常 |
| 一篇 `aliases` 用块列表写 | 块列表写法 |
| 一篇值里带英文双引号与逗号 | 引号/逗号不能把解析搞崩 |
| `*.地图.md` / `索引.md` / `inbox` | 结构文件不该参与检索 |
| 一个空文件 | 零字节文件 |
"""

from __future__ import annotations

from pathlib import Path

#: 合成库的全部文件：相对路径 → 内容。
FILES: dict[str, str] = {
    # ---- 结构文件（不该参与"相关笔记"检索）----
    "索引.md": "# 索引\n\n- [[甲领域地图]]\n- [[乙领域地图]]\n",
    "inbox/收件箱.md": "# 收件箱\n\n随手记都先放这里。\n",
    "领域/甲领域/甲领域地图.md": "# 甲 领域 · 地图\n\n- [[Alpha·基础]]\n",
    "领域/乙领域/乙领域地图.md": "# 乙 领域 · 地图\n\n- [[Delta·旧式]]\n",
    # ---- 甲领域：新式 schema，标签是 `领域/甲`（与目录名"甲领域"不符）----
    "领域/甲领域/Alpha·基础.md": (
        "---\n"
        "type: permanent\n"
        "tags: [缓存, 性能, 领域/甲]\n"
        "aliases: [Alpha基础, A基础]\n"
        "created: 2026-01-01\n"
        "source: 与 AI 助手对话学习整理\n"
        "note: 只讲 Alpha 的基础概念与缓存策略。\n"
        "---\n"
        "\n"
        "# Alpha · 基础\n"
        "\n"
        "> 目标：把 Alpha 的基础吃透。\n"
        "\n"
        "## 一、缓存\n"
        "\n"
        "缓存命中率的计算方式是……\n"
    ),
    "领域/甲领域/Beta·进阶.md": (
        "---\n"
        "type: permanent\n"
        "tags: [并发, 锁, 领域/甲]\n"
        "aliases:\n"
        "  - Beta进阶\n"
        "  - B进阶\n"
        "created: 2026-01-02\n"
        "source: 与 AI 助手对话学习整理\n"
        "note: 只讲 Beta 的并发模型与锁语义。\n"
        "---\n"
        "\n"
        "# Beta · 进阶\n"
        "\n"
        "并发与锁的语义差异……\n"
    ),
    "领域/甲领域/Gamma·陷阱.md": (
        "---\n"
        "type: permanent\n"
        "tags: [\"边界: 引号\", 陷阱, 领域/甲]\n"
        "aliases: [\"含, 逗号的别名\"]\n"
        "created: 2026-01-03\n"
        "note: 值里带冒号和逗号，测试解析器。\n"
        "source: 测试\n"
        "---\n"
        "\n"
        "# Gamma · 陷阱\n"
        "\n"
        "边界情况合集。\n"
    ),
    # ---- 乙领域：旧式 schema ----
    # ⚠️ **同时写 `type` 和 `category`** —— 这才是真库的写法（2026-10-06 实测修正）：
    #   真实库里12 篇旧笔记都写着 `type: permanent` + `category: 学习`。
    #   原来这里只写 `category`，让测试测在一个真库里不存在的形态上。
    "领域/乙领域/Delta·旧式.md": (
        "---\n"
        "type: permanent\n"
        "title: Delta·旧式\n"
        "category: 个人档案\n"
        "difficulty: 入门\n"
        "status: 已完善\n"
        "---\n"
        "\n"
        "# Delta · 旧式\n"
        "\n"
        "旧式 frontmatter 的笔记。\n"
    ),
    "领域/乙领域/Epsilon·旧式.md": (
        "---\n"
        "type: permanent\n"
        "title: Epsilon·旧式\n"
        "category: 简历\n"
        "difficulty: 进阶\n"
        "status: 待梳理\n"
        "---\n"
        "\n"
        "# Epsilon · 旧式\n"
        "\n"
        "又一篇旧式笔记。\n"
    ),
    # ---- 脏数据 ----
    "领域/甲领域/坏掉的.md": "---\ntype: permanent\ntags: [没有闭合\n\n# 坏掉的\n\n这篇的 frontmatter 没闭合。\n",
    "领域/甲领域/空的.md": "",
}


def make_vault(root: Path) -> Path:
    """在 `root` 下造出合成知识库，返回 `root`。"""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


def note_names(root: Path) -> set[str]:
    """合成库里所有 `.md` 的 stem（给"只新建"断言用）。"""
    return {p.stem for p in Path(root).rglob("*.md")}


def snapshot_fingerprint(root: Path) -> dict[str, int]:
    """全库指纹：相对路径 → 字节数。**用来断言"没被改动"**。"""
    root = Path(root)
    return {
        p.relative_to(root).as_posix(): p.stat().st_size
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }
