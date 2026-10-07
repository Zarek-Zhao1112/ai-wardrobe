"""按主题找相关笔记 —— **纯词法，不调模型**。

为什么不用向量检索：全库 33 篇 / 11.7 万字，词法已经够准；而向量方案要装依赖、
建索引、还要处理"换模型索引失效"。**先用最简单的，不够用再说。**

---

## 判据（两层，缺一不可）

**第一层：完整主题词命中（硬闸）**
把提问剥掉口水词后切成"主题词"，**必须至少有一个完整词**在笔记的
`文件名 / 标题 / aliases / tags / note` 里作为子串出现过。

> 这一步是关键。纯 2-gram 覆盖率会放过碎片命中 —— 实测 `abandon` 光靠
> `on`/`an` 就命中了 Tailwind 那篇（1.75 分）。而"完整词"判据下它直接出局。

**第二层：2-gram 覆盖率打分（排序用）**
通过硬闸的笔记再按 2-gram 覆盖率排序（权重见下表），并用 `top1 × RELATIVE_CUT`
切掉陪跑噪声。

| 命中位置 | 权重 | 为什么 |
|---|---|---|
| 文件名 | 4.0 | 库里的命名规范就是"文件名 = 主题"（`RSI与BIAS·振荡指标辨析`） |
| H1 标题 | 3.5 | 与文件名基本同义，略低一点防止同一个词被数两次 |
| `aliases` | 3.0 | 作者自己写的别名，专为"换个说法也能找到" |
| `tags` | 2.0 | 主题标签 |
| `note` | 1.5 | 一句话定位，含上下文但不如标题精确 |

按**覆盖率**（命中 gram ÷ 主题 gram）算而不按绝对命中数 —— 否则长文本会稳压短字段。

---

## 标定记录（2026-10-06，真库 33 篇 + 11 个查询）

```
真相关：14.0 / 9.9 / 9.4 / 8.2 / 4.7 / 3.5 / 2.5
陪跑：  1.79 / 1.50 / 0.70 / 0.44 / 0.38 / 0.21 / 0.19
无关查询（减脂 / 量子力学 / 红烧肉 / 手机 / abandon / Claude）→ 全部无命中 ✓
```

⚠️ 库长到几百篇后要**重新标定** `RELATIVE_CUT`（方法：跑一批已知相关的查询看分布）。

★ 结果按 `(-score, name)` 排序 —— 分数相同时用名字兜底，保证同一输入永远同一输出。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from . import frontmatter
from .catalog import Catalog, NoteMeta

#: 提问里的"口水词" —— 它们会让 2-gram 命中虚高（"怎么""区别"几乎跟谁都像）。
_STOP = (
    "我想学", "想学", "学一下", "讲讲", "讲一下", "了解一下", "了解下",
    "怎么", "什么", "为什么", "如何", "区别", "差异", "一下", "关于",
    "方面", "一些", "帮我", "给我", "这个", "那个", "哪个", "的话",
    "的", "了", "是", "和", "与", "跟", "在", "有", "我", "你",
    "吗", "呢", "吧", "啊", "啥", "哪些", "哪种", "请问", "想要", "需要",
)

#: ★★ 剥停用词**必须按长度降序**。
#:
#: 踩过的坑（2026-10-06 真库回归抓到）：按原顺序剥时，"什么" 会先把
#: 「黄金**为什么**涨」拆成「黄金为 涨」—— 残字「为」粘在实词上，
#: 于是查询词变成「黄金为」，在库里查不到任何东西 → **整条查询静默失效**。
#: 长的先剥（"为什么" 先于 "什么"）就没这个问题。
_STOP_SORTED = tuple(sorted(_STOP, key=len, reverse=True))

_WS_RE = re.compile(r"\s+")

#: 至少要命中几个**完整主题词**才算"库里有这个主题"。
MIN_MATCHED_TERMS = 1

#: 次选门槛 = top1 得分 × 此比例（切掉陪跑噪声）。
RELATIVE_CUT = 0.12

#: 次选的**绝对**分数下限。相对比例在 top1 分数本身很低时会失效
#: （top1=1.0 → cut=0.12 → 所有微弱命中都收进来）。实测真库分数分布：
#: 真相关 2.5–14.0、噪声 0.19–0.7，所以 1.0 是个安全的分界。
MIN_KEEP_SCORE = 1.0


@dataclass(frozen=True)
class Hit:
    """一条命中。

    `matched_terms` 记下**具体哪些词命中了** —— 方便将来排查"它为什么选中这篇"，
    也能在回复里跟用户解释（"你库里讲过『缓存』的是这几篇"）。
    """

    note: NoteMeta
    score: float
    matched_terms: tuple[str, ...] = ()


def normalize_topic(topic: str) -> str:
    """剥掉口水词，留下**实词部分**（比对检索用的原始形态）。

    ⚠️ 用 `_STOP_SORTED`（长词优先），理由见那个常量上方的注释 ——
    顺序错了会把"为什么"拆出残字粘到实词上。
    """
    t = topic or ""
    for w in _STOP_SORTED:
        t = t.replace(w, " ")
    return _WS_RE.sub(" ", t).strip()


def query_terms(topic: str) -> list[str]:
    """把提问切成**主题词**（硬闸的判据单位）。

    ★ 长查询里单字是噪声（"的""用""算"），过滤掉；
    但短查询里单字可能就是主题（"锁"），保留 —— 否则用户问单字永远无命中。
    """
    q = normalize_topic(topic)
    if not q:
        return []
    raw = [t for t in q.split() if t]
    if len(q) >= 4:
        raw = [t for t in raw if len(t) >= 2]
    return raw


def grams(text: str) -> set[str]:
    """字符 2-gram。

    中文不切词也能有不错的召回（"振荡指标" → {振荡, 荡指, 指标}），且**零依赖**
    —— 不引 jieba 之类的分词库。
    """
    s = _WS_RE.sub("", (text or "").lower())
    if len(s) < 2:
        return {s} if s else set()
    return {s[i : i + 2] for i in range(len(s) - 1)}


def haystack(n: NoteMeta) -> str:
    """一篇笔记**可被检索的全部文本**（小写）。

    顺序无关，只求覆盖：文件名 + 标题 + 别名 + 标签 + 一句话定位。
    **不含正文** —— 33 篇正文共 11.7 万字，每次都读会让检索从毫秒变秒级。
    """
    parts = [n.name, n.title, n.note, " ".join(n.aliases), " ".join(n.tags)]
    return " ".join(p for p in parts if p).lower()


def _coverage(q_grams: set[str], text: str, weight: float) -> float:
    """`text` 对主题的覆盖率 × 权重。"""
    if not text:
        return 0.0
    g = grams(text)
    if not g:
        return 0.0
    return weight * (len(q_grams & g) / max(len(q_grams), 1))


def score_note(q_grams: set[str], n: NoteMeta) -> float:
    """2-gram 覆盖率打分。抽成公开函数是为了**能单测**。"""
    s = 0.0
    s += _coverage(q_grams, n.name, 4.0)
    s += _coverage(q_grams, n.title, 3.5)
    s += max((_coverage(q_grams, a, 3.0) for a in n.aliases), default=0.0)
    s += _coverage(q_grams, n.note, 1.5)
    s += max((_coverage(q_grams, t, 2.0) for t in n.tags), default=0.0)
    return round(s, 4)


def find_related(
    cat: Catalog,
    topic: str,
    *,
    limit: int = 3,
    min_terms: int = MIN_MATCHED_TERMS,
    relative_cut: float = RELATIVE_CUT,
) -> list[Hit]:
    """找出跟 `topic` 最相关的笔记（按相关度降序，最多 `limit` 条）。

    ★★ **返回 `[]` 的语义是"库里没有这个主题"** —— 调用方据此建议开新领域。
    所以这里宁可严一点：**没有完整主题词命中就返回空**，不做"勉强像"的匹配。
    """
    q = normalize_topic(topic)
    if not q:
        return []
    q_grams = grams(q)
    if not q_grams:
        return []
    terms = query_terms(topic)
    if not terms:
        return []

    hits: list[Hit] = []
    for n in cat.real_notes():
        hay = haystack(n)
        matched = tuple(t for t in terms if t.lower() in hay)
        if len(matched) < min_terms:
            continue
        hits.append(Hit(note=n, score=score_note(q_grams, n), matched_terms=matched))
    if not hits:
        return []

    # ★ tiebreaker 必须稳定：先按分数降序，再按名字升序
    hits.sort(key=lambda h: (-h.score, h.note.name))
    # ★ `max(cut, MIN_KEEP_SCORE)`（审查加固的疑似项）：分数都是**相对**的，
    #   top1 本身很低时 `cut` 也会很低 → 相对阈值形同虚设。给一个绝对下限。
    cut = max(hits[0].score * relative_cut, MIN_KEEP_SCORE)
    kept = [h for h in hits if h.score >= cut]
    return kept[:limit] if limit > 0 else kept


def recent_in_dir(cat: Catalog, dir_rel: str, *, limit: int = 3) -> list[NoteMeta]:
    """某个领域里**最近更新**的几篇（"让助手挑一个"场景用）。

    排掉地图页与结构文件 —— 用户要学的是知识，不是目录。
    """
    items = [
        n for n in cat.in_dir(dir_rel) if n.schema != "none" and not n.name.endswith("地图")
    ]
    items.sort(key=lambda n: (-n.mtime, n.name))
    return items[:limit]


def read_body(vault: Path | str, note: NoteMeta, *, max_chars: int = 8000) -> str:
    """读一篇笔记的**正文**（已剥掉 frontmatter）。读不动返回 `""`。

    `max_chars` 是硬闸：最长的一篇 14.6k 字符，全塞进 prompt 会挤掉对话历史。
    """
    p = Path(vault) / note.rel
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    _, body = frontmatter.parse(text)
    body = body.strip()
    if len(body) <= max_chars:
        return body
    # 截断时尽量停在段落边界，别把一句话砍一半
    cut = body[:max_chars]
    for sep in ("\n\n", "\n", "。"):
        idx = cut.rfind(sep)
        if idx > max_chars * 0.6:
            return cut[: idx + len(sep)].rstrip()
    return cut.rstrip()


def domain_overview(cat: Catalog) -> list[tuple[str, int, str]]:
    """领域总览：`[(领域目录, 真笔记数, 该领域的标签)]` —— 给"你想学什么"的导航用。"""
    out: list[tuple[str, int, str]] = []
    for d in cat.domain_dirs():
        notes = [n for n in cat.in_dir(d) if n.schema != "none" and not n.name.endswith("地图")]
        out.append((d, len(notes), cat.vote_domain_tag(d)))
    return out
