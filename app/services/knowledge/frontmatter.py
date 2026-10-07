"""YAML frontmatter 的极简读写。

**为什么手写，而不是装 `pyyaml`**：本项目零 YAML 依赖（全仓 `grep yaml` 零命中），
而要处理的只是 Obsidian 笔记头部那几种固定写法（浅层标量 + 内联列表 + 少量块列表）；
写侧由 `render_frontmatter()` 自产、格式完全可控。引 pyyaml 还得自己切 `---` 块，
并没有省事 —— 反而多一个装包环节和一条新的失败路径。

★★ **核心纪律：解析失败绝不抛异常。**
用户的库里会有各种历史写法（实测同一个库里有 3 套 schema，见
`docs/knowledge-design.md` §3.2）。读不动就**原样当正文**处理 ——
宁可少解析出几个字段，也不能让一次目录扫描崩掉整条链路。

---

## 支持的写法

```yaml
---
type: permanent                              # 标量
tags: [指数, 沪深300, 领域/金融]              # 内联列表
aliases:                                     # 块列表
  - 沪深300
  - 宽基指数
note: 只讲…。通用方法见《投资方法论·估值与择时》。   # 值里可以有逗号、书名号、冒号
---
```

**不支持的**（遇到就当普通行跳过，不报错）：嵌套映射、多行字符串 `|` / `>`、
锚点引用。库里没有这些写法。
"""

from __future__ import annotations

import re
from typing import Any

#: frontmatter 块的边界行。
_DELIM = "---"

#: `key: value` 一行。key 限定为字母数字下划线 —— 库里的字段名都长这样。
_KV_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$")

#: 块列表项 `- value`（缩进不限）。
_ITEM_RE = re.compile(r"^\s*-\s+(.*)$")

#: 需要加引号才安全的字符 —— 值里出现这些，输出时包双引号。
_NEED_QUOTE = (":", "#", "[", "]", "{", "}")


# ---------------------------------------------------------------------------
# 读
# ---------------------------------------------------------------------------


def split(text: str) -> tuple[str, str]:
    """把整篇笔记切成 `(frontmatter 原文, 正文)`。

    没有 frontmatter 时返回 `("", 原文)`。
    ⚠️ 只认**第一行**就是 `---` 的那种（与 Obsidian 一致）；
    且必须有**闭合**的 `---` —— 只有开头没有结尾的，一律当没有 frontmatter
    （否则一篇正文里恰好有 `---` 分隔线的笔记会被腰斩）。
    """
    if not text:
        return "", text
    lines = text.split("\n")
    # ⚠️ **BOM 必须显式剥**：U+FEFF 不是空白字符，`str.strip()` 不会去掉它 ——
    # 带 BOM 的笔记（Windows 上 PowerShell 的 `>` / `Set-Content`、部分编辑器
    # 默认产出）会因此**整篇被当成没有 frontmatter** → `schema=none` →
    # 从索引、`compact_lines`、检索里**全部隐形**（审查抓到，真库现在恰好没 BOM，
    # 属于靠数据运气）。
    if lines[0].lstrip("﻿").strip() != _DELIM:
        return "", text
    for i in range(1, len(lines)):
        if lines[i].strip() == _DELIM:
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1 :])
    return "", text


def parse(text: str) -> tuple[dict[str, Any], str]:
    """解析出 `(字段字典, 正文)`。**任何异常写法都不抛，最差返回 `({}, 原文)`。**"""
    try:
        fm, body = split(text)
        if not fm:
            return {}, body
        return _parse_block(fm), body
    except Exception:  # noqa: BLE001 —— 兜底是刻意的：扫描一篇坏笔记不能拖垮整库
        return {}, text


def _parse_block(fm: str) -> dict[str, Any]:
    """逐行解析 frontmatter 块。认不出的行跳过。"""
    out: dict[str, Any] = {}
    lines = fm.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()
        # 空行与注释行跳过
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        m = _KV_RE.match(line)
        if not m:
            i += 1
            continue
        key, raw = m.group(1), m.group(2).strip()

        if raw:
            out[key] = _parse_value(raw)
            i += 1
            continue

        # 值为空 → 可能跟着块列表
        items: list[str] = []
        j = i + 1
        while j < len(lines):
            s = lines[j]
            if not s.strip():
                j += 1
                continue
            im = _ITEM_RE.match(s)
            if not im:
                break
            item = _unquote(im.group(1).strip())
            if item:
                items.append(item)
            j += 1
        if items:
            out[key] = items
            i = j
        else:
            out[key] = ""
            i += 1
    return out


def _parse_value(raw: str) -> Any:
    """一个值：内联列表 / 标量。"""
    if raw.startswith("[") and raw.endswith("]"):
        return _split_inline(raw[1:-1])
    return _unquote(raw)


def _split_inline(s: str) -> list[str]:
    """切内联列表，**跳过引号内的逗号**。

    ⚠️ 不能直接 `s.split(",")`：`tags: ["a, b", c]` 会被切成三项。
    """
    parts: list[str] = []
    buf: list[str] = []
    quote = ""
    for ch in s:
        if quote:
            if ch == quote:
                quote = ""
            else:
                buf.append(ch)
            continue
        if ch in "\"'":
            quote = ch
            continue
        if ch == ",":
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _unquote(s: str) -> str:
    """剥掉成对的引号（单或双）。"""
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    return s


def as_list(value: Any) -> list[str]:
    """把字段值统一成**字符串列表**。

    实测库里同一个字段有时写标量、有时写列表；下游一律按列表用，
    免得每个调用点都写一遍 `isinstance` 判断。
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(x).strip() for x in value if str(x).strip()]
    s = str(value).strip()
    return [s] if s else []


# ---------------------------------------------------------------------------
# 写
# ---------------------------------------------------------------------------


def render(meta: dict[str, Any], body: str = "") -> str:
    """把字段字典 + 正文拼成一整篇笔记。

    输出格式是**冻结的**（与库里实测写法逐字对齐，见 `docs/knowledge-design.md` §3.3）：
    首行 `---`、每行 `key: value`、末行 `---`、空一行后接正文。
    字段顺序 = 传入顺序（Python 3.7+ dict 保序），所以调用方决定顺序。
    """
    lines = [_DELIM]
    for key, val in meta.items():
        lines.append(f"{key}: {_render_value(val)}")
    lines.append(_DELIM)
    text = "\n".join(lines)
    if body and body.strip():
        text += "\n\n" + body.lstrip("\n")
    return text if text.endswith("\n") else text + "\n"


def _render_value(val: Any) -> str:
    """渲染一个值。列表走内联写法；标量按需加引号。"""
    if isinstance(val, (list, tuple)):
        return "[" + ", ".join(_render_scalar(str(x)) for x in val) + "]"
    return _render_scalar("" if val is None else str(val))


def _render_scalar(s: str) -> str:
    """渲染一个**标量**。键值与列表项**共用**它 —— 两者必须同一套规则，否则往返不对称。

    ★ 加引号的判据比"看起来该加"更多，两条都是被真实 bug 逼出来的：

    1. **换行**：不加引号时后续行会被 `_parse_block` 当成新字段解析 ——
       实测 `note="第一行\\n第二行\\ntags: [X]"` 输出三行，
       读回来 `note` 带残余引号、**原 `tags` 被顶掉**（YAML 注入）。
    2. **逗号**：在**内联列表**里会被 `_split_inline` 切错 ——
       实测 `aliases=["缓存, 内存"]` 往返后变成两条别名。
    """
    if not s:
        return '""'
    if (
        any(c in s for c in _NEED_QUOTE)
        or "," in s
        or "\n" in s
        or "\r" in s
        or s != s.strip()
        or s.startswith("-")
    ):
        # 换行折成空格：本项目的字段（note / source）本来就该是单行，
        # 折成空格既不破坏结构、也不丢信息量。
        flat = s.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
        return '"' + flat.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s
