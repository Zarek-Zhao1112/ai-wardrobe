"""生成新笔记 —— 拆题、逐节填充、收尾、**纯 Python 拼装**、预览。

⚠️ **这一波为什么不用 `chat_json`**（2026-10-06 真模型实测）：
`llm/client.py` 的 `chat_json` 会先试 `json_schema`、再试 `json_mode`，
最后降级到"正则抽 JSON **对象**"。而实测这家供应商（`deepseek-v4-pro`）
前两种都不支持，降级到正则时模型返回的是**裸数组** `[{...}]`
—— **模型答得完全正确，却被我们自己的解析器判成失败**。
所以这里走 `chat_text` + 自己抽 JSON，两种形状都认（`parse_json_any`）。

---

## 两条不可让渡的纪律

1. **结构绝不由模型产** —— frontmatter 字段与顺序、章节标题、章节顺序、
   分隔符、`## 附：延伸学习线索` 全部由 `assemble()` 这个**纯函数**写死。
   模型只填**内容**。所以同样的输入永远得到同样的骨架，能单测、能复现。
2. **分段生成** —— `llm/client.py` 的 `MAX_OUTPUT_TOKENS = 2048` 是模块常量
   （实测教训：给上限反而更快，因为推理模型会把额度烧在 reasoning 上）。
   一次约 1400-2000 中文字，而笔记要 3-5 千字符 → 必须逐节调用。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional, Sequence

from . import catalog

#: 一次最多拆几篇（用户定调："尽量不要让一篇笔记太长"）。
MAX_NOTES = 3

#: 单节字数区间（字符）。上限受 `MAX_OUTPUT_TOKENS` 约束，留足安全余量。
SECTION_MIN = 500
SECTION_MAX = 1400

#: 每篇最多几节。
MAX_SECTIONS = 6

#: 每节重试次数（超了就把这节的失败如实上报，不硬编内容）。
SECTION_RETRY = 1

#: 判定"这一节能用"的**最低**字数。**故意小于 `SECTION_MIN`** ——
#: `SECTION_MIN` 是"合格线"，拿它当重试门槛会让 300 字的节反复重试，
#: 既慢又可能把模型预算烧光。短于这个数才说明模型压根没写东西
#: （拒答、只回了标题、或者返回了空串）。
SECTION_USABLE = 100

#: ★ **校验用的下限必须与"生成时的下限"分开**（我自己写错过一次）：
#:   校验是拿来看**已有内容是否合格**，不是催模型重写。
#:   拿 `SECTION_MIN`（500）当校验线，会让 300 字的节既被判"不合格"、
#:   又已经写不出来 —— 用户只会看到"没写全"，看不到真实原因。
#:   现实里模型给 300 字是常态，所以校验线定在 200（够一段完整论述）。
VALIDATE_MIN = 200

#: frontmatter 的 `source` —— **要写得出"具体来源"**（审查补的）。
#: 库里活笔记的 source 是事件（"与 AI 助手对话学习整理" / "Workbuddy蒸馏"），
#: 光写"AI 生成"等于没写来源 —— 半年后无法判断这篇是**聊出来的**还是**凭空生成的**。
SOURCE_TEXT = "与 AI 助手对话学习整理（{date}，主题：{topic}）"


def source_text(topic: str = "", *, today: str = "") -> str:
    """`source` 字段的实际文本。"""
    return SOURCE_TEXT.format(
        date=today or date.today().isoformat(), topic=topic or "未命名"
    )


# ---------------------------------------------------------------------------
# 数据契约
# ---------------------------------------------------------------------------


@dataclass
class Section:
    """一节。`points` 只在大纲阶段有（填充后为空）。"""

    heading: str
    body: str = ""
    points: list[str] = field(default_factory=list)

    @property
    def filled(self) -> bool:
        return bool(self.body.strip())


@dataclass
class NotePlan:
    """一篇笔记的完整蓝图。"""

    title: str
    subtitle: str = ""
    aliases: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    intro: str = ""            # 「目标 / 一句话定位」两行（含 `>` 前缀）
    intro_note: str = ""       # frontmatter 的 `note`（一句话定位）
    outro: list[str] = field(default_factory=list)   # 「延伸学习线索」条目

    @property
    def filename(self) -> str:
        """文件名（不含 `.md`）—— 跟库里习惯一致：`主标题 · 副标题`。"""
        return catalog.join_name(self.title, self.subtitle)

    @property
    def filled(self) -> bool:
        """所有节都写出来了？**没写全就不交这一篇** —— 别交半成品。"""
        return bool(self.sections) and all(s.filled for s in self.sections)

    @property
    def char_count(self) -> int:
        """正文字符数（给预览里的字数估算用）。"""
        return (
            len(self.intro)
            + sum(len(s.body) for s in self.sections)
            + sum(len(x) for x in self.outro)
        )


# ---------------------------------------------------------------------------
# 死链剥除
# ---------------------------------------------------------------------------

_LINK_RE = re.compile(r"\[\[([^\]|]+)(\|[^\]]+)?\]\]")


def strip_unknown_links(text: str, known: set[str]) -> tuple[str, list[str]]:
    """把指向**不存在**笔记的 `[[双链]]` 剥成普通文字。

    返回 `(处理后的文本, 被剥掉的目标名列表)`。

    ★ 模型很爱编链接 —— 库里没有的必须在图谱里消失，不能留死链。
    ★ 别名链接 `[[不存在|想显示的文字]]` 剥的时候**保留别名文字**：
      作者写"见 想显示的文字"本来是通顺的，退化成目标名反而语义不对。
    """
    dropped: list[str] = []

    def _sub(m: re.Match[str]) -> str:
        target = m.group(1).strip()
        if target in known:
            return m.group(0)
        dropped.append(target)
        alias = (m.group(2) or "").lstrip("|").strip()
        return alias or target

    return _LINK_RE.sub(_sub, text or ""), dropped


# ---------------------------------------------------------------------------
# 模型输出的解析（**两种 JSON 形状都认**）
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)


def parse_json_any(text: str) -> Any:
    """从模型输出里抽出 JSON。**对象、裸数组都认**。

    ⚠️ 候选必须**数组优先**（审查抓到的第二个坑）：`[{"title":"A",…}]` 里
    `find("{")` 命中的是**内层**对象 —— 把它当整个大纲，一篇笔记就被
    当成了全部大纲。所以先找 `[`，找不到再找 `{`。
    """
    t = (text or "").strip()
    if not t:
        return None
    out: list[str] = []
    for m in _FENCE_RE.finditer(t):          # ```json ... ``` 代码块最可信
        out.append(m.group(1).strip())
    for open_ch, close_ch in (("[", "]"), ("{", "}")):
        i, j = t.find(open_ch), t.rfind(close_ch)
        if 0 <= i < j:
            out.append(t[i : j + 1])
    for cand in out:
        try:
            return json.loads(cand)
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def _as_list(val: Any) -> list[str]:
    """模型会把列表写成单个字符串、写成 `None`、写成逗号分隔的串 —— 全都收下。"""
    if val is None:
        return []
    if isinstance(val, str):
        return [val.strip()] if val.strip() else []
    if isinstance(val, (list, tuple)):
        return [str(x).strip() for x in val if str(x).strip()]
    return [str(val).strip()]


def _coerce_plan(raw: Any) -> Optional[NotePlan]:
    """一个 JSON 对象 → `NotePlan`。**任何一项形状不对就返回 `None`**（宁缺毋滥）。"""
    if not isinstance(raw, dict):
        return None
    title = str(raw.get("title") or "").strip()
    if not title:
        return None

    sections: list[Section] = []
    raw_sections = raw.get("sections")
    if isinstance(raw_sections, (list, tuple)):
        for item in raw_sections:
            if isinstance(item, str):
                head = item.strip()
                if head:
                    sections.append(Section(heading=head))
                continue
            if not isinstance(item, dict):
                continue
            head = str(item.get("heading") or item.get("title") or "").strip()
            if not head:
                continue
            sections.append(
                Section(heading=head, points=_as_list(item.get("points")))
            )
    if not sections:
        return None                      # 没有章节 → 没法生成，别硬编

    return NotePlan(
        title=title,
        subtitle=str(raw.get("subtitle") or "").strip(),
        aliases=_as_list(raw.get("aliases")),
        tags=_as_list(raw.get("tags")),
        sections=sections[:MAX_SECTIONS],
    )


def parse_plans(data: Any, *, max_notes: int = MAX_NOTES) -> list[NotePlan]:
    """大纲 JSON（对象或裸数组）→ `NotePlan` 列表。"""
    if isinstance(data, list):
        data = {"notes": data}
    if not isinstance(data, dict):
        return []
    raw_notes = data.get("notes")
    if not isinstance(raw_notes, (list, tuple)):
        return []
    plans: list[NotePlan] = []
    for raw in raw_notes:
        plan = _coerce_plan(raw)
        if plan is not None:
            plans.append(plan)
        if max_notes and len(plans) >= max_notes:
            break
    return plans


def parse_outline_text(text: str, *, max_notes: int = MAX_NOTES) -> list[NotePlan]:
    """从模型返回的**文本**里解析出篇目。**对象或裸数组都认。**"""
    data = parse_json_any(text)
    if data is None:
        return []
    if isinstance(data, list):
        data = {"notes": data}
    return parse_plans(data, max_notes=max_notes)


# ---------------------------------------------------------------------------
# 清理单节
# ---------------------------------------------------------------------------

#: 模型爱在正文末尾加的客套话（会让笔记显得注水）。
_TRAILER_RE = re.compile(
    r"(希望对你有所帮助|希望这篇文章对你有帮助|如有疑问欢迎交流|"
    r"如果你觉得有用.{0,20}|以上就是全部内容|总结一下[:：].*)$",
    re.MULTILINE,
)


def _clean_section(body: str, heading: str) -> str:
    """把模型给的一节正文收拾干净。

    1. **去掉它自己又写了一遍的标题行** —— 标题由 `assemble()` 加，重复了很难看；
    2. 去掉末尾客套话；
    3. 剥掉围栏代码块的 ```` ``` ```` 标记。
    """
    text = (body or "").strip()
    if not text:
        return ""

    # 1) 开头若已是一行标题（`## xxx` 或裸 `xxx`），去掉
    first, _, rest = text.partition("\n")
    norm_head = (heading or "").strip().lstrip("#").strip()
    if norm_head and first.strip().lstrip("#").strip() == norm_head:
        text = rest.strip()
    elif first.strip().startswith("##"):
        text = text.partition("\n")[2].strip()

    text = _TRAILER_RE.sub("", text).strip()
    if text.startswith("```") and text.endswith("```") and len(text) > 6:
        text = text[3:-3].strip()
    return text


# ---------------------------------------------------------------------------
# 产出校验（规格 §6.3 —— **四项全查**，缺一项就是"能跑但不对"）
# ---------------------------------------------------------------------------


def validate_plan(plan: NotePlan) -> list[str]:
    """校验一篇填好的笔记，返回**问题列表**（空列表 = 通过）。

    ★ 四项（审查发现原来只做了 2 项）：
      1. **节数 == 大纲节数**（模型可能少写一节 —— 半成品）
      2. 每节字数在区间内
      3. 正文**结尾标点**（不以 `。！？.!?）` 收尾 = 被 `MAX_OUTPUT_TOKENS` 截断）
      4. 没有残留的围栏代码块标记
    """
    problems: list[str] = []
    if not plan.sections:
        return ["没有章节"]
    if not plan.filled:
        missing = [s.heading for s in plan.sections if not s.filled]
        problems.append(f"有 {len(missing)} 节没写出来：{'、'.join(missing[:3])}")
    for s in plan.sections:
        n = len(s.body.strip())
        if not s.filled:
            continue
        if n < VALIDATE_MIN:
            problems.append(f"「{s.heading}」只有 {n} 字（低于 {VALIDATE_MIN}）")
        elif n > SECTION_MAX * 2:
            problems.append(f"「{s.heading}」有 {n} 字（远超 {SECTION_MAX}）")
    tail = plan.sections[-1].body.strip()[-1:] if plan.filled else ""
    if tail and tail not in "。！？.!?）】":
        problems.append(f"结尾是「{tail}」，可能被输出上限截断")
    if "```" in "".join(s.body for s in plan.sections):
        problems.append("正文里有残留的代码围栏标记")
    return problems


# ---------------------------------------------------------------------------
# 拼装（**纯函数** —— 结构全部写死在这里）
# ---------------------------------------------------------------------------


def assemble(
    plan: NotePlan,
    *,
    domain_tag: str,
    known_names: set[str],
    related: Sequence[str] = (),
    domain_dir: str = "",
    header_style: str = "new",
    old_category: str = "",
    topic: str = "",
    today: str = "",
) -> tuple[dict[str, Any], str, list[str]]:
    """把 `NotePlan` 拼成 `(frontmatter 字典, 正文, 被剥掉的死链列表)`。

    **纯函数**：同样输入永远同样输出，所以能单测、能复现。

    格式与库里实测写法逐字对齐（`docs/knowledge-design.md` §3.3）：
    - 新式字段顺序 `type / tags / aliases / created / source / note`
    - **领域标签放 `tags` 最后一位**（图谱分色靠它）
    - H1 是 `# 主标题 · 副标题`（空格 + 中点 + 空格）
    - `note` 里引用其它笔记用**书名号**，不用 `[[]]`

    `header_style="old"` 时改用旧式字段（实测 `领域/Web开发`、`领域/自我与职业`
    下的 12 篇）：`type / title / aliases / category / tags / source /
    difficulty / status / related / created`。见 `catalog.header_schema()`。
    """
    created = today or date.today().isoformat()
    source = source_text(topic or plan.title, today=created)
    dropped: list[str] = []

    here = plan.filename
    # ⚠️ "相邻"必须是**真的相关**的笔记 —— 由调用方按检索结果传进来。
    # 原来这里是 `[n for n in sorted(known_names) if n != here][:2]`（按字典序取前两个），
    # 真模型跑一次就暴露了：写缓存那篇时"相邻"成了《01-前端基础地图》《02-三层全景》，
    # **跟主题毫无关系**。而且它**没过 `known_names`**：正文里的 `[[X]]` 严格校验，
    # frontmatter 的《X》却完全不校验 —— 同一模块两套标准。
    others = [n for n in (related or []) if n and n != here and n in known_names][:2]

    parts: list[str] = []

    # ★ 死链剥除**必须覆盖 intro**（审查补的）：原来只清 sections/outro，
    # 而模型写的引导段（`> 目标：…见 [[某笔记]]`）同样可能指向不存在的笔记
    # → 落盘后留下一条永远点不开的死链。
    if plan.intro.strip():
        intro, gone = strip_unknown_links(plan.intro.strip(), known_names)
        dropped.extend(gone)
        parts.append(intro)

    # 稳定双链（**由代码写死**，不靠模型给）：frontmatter 里的《》**不是链接**，
    # 只有正文里的 `[[…]]` 才进 Obsidian 图谱 —— 这几行是把新笔记和旧笔记
    # 连起来的唯一可靠方式（规格 §8.1）。
    chain: list[str] = []
    if domain_dir:
        chain.append(f"> 所属领域：[[{domain_dir}地图]]")
    if others:
        chain.append("> 相邻笔记：" + " · ".join(f"[[{n}]]" for n in others))
    if chain:
        parts.append("\n".join(chain))

    for s in plan.sections:
        body, gone = strip_unknown_links(s.body.strip(), known_names)
        dropped.extend(gone)
        parts.append(f"## {s.heading.strip()}\n\n{body}")

    if plan.outro:
        lines: list[str] = []
        for item in plan.outro:
            text, gone = strip_unknown_links(str(item).strip(), known_names)
            dropped.extend(gone)
            lines.append(text if text.startswith("-") else f"- {text}")
        parts.append("## 附：延伸学习线索\n\n" + "\n".join(lines))

    body = f"# {catalog.join_name(plan.title, plan.subtitle)}\n\n" + "\n\n".join(parts) + "\n"

    # 领域标签**必须**在最后一位（图谱分色靠它，位置错了好几个脚本会读不到）
    tags = [t for t in plan.tags if t and not t.startswith(catalog.DOMAIN_PREFIX)]
    if domain_tag:
        tags.append(domain_tag)

    note_line = plan.intro_note or (plan.subtitle or plan.title)
    if others:
        note_line += "。相邻：" + "、".join(f"《{n}》" for n in others)
    # ★ `note` 也可能有死链（模型的 `intro_note` 里带 `[[X]]`）——同样要剥。
    note_line, gone = strip_unknown_links(note_line, known_names)
    dropped.extend(gone)

    if header_style == "old":
        # ★ 旧式领域（实测 `领域/Web开发`、`领域/自我与职业` 共 12 篇）：
        #   字段名与顺序都不同，`category` / `difficulty` / `status` 是必有的。
        #   沿用它们，新笔记才能被库里既有的筛选/面板认出来（"就近对齐"）。
        meta: dict[str, Any] = {
            "type": "permanent",
            "title": catalog.join_name(plan.title, plan.subtitle),
            "aliases": [a for a in plan.aliases if a],
            "category": old_category or "学习",
            "tags": tags,
            "source": source,
            "difficulty": "入门",
            "status": "待梳理",
            # 旧式的 `related` 是**字符串**（不是列表），实测写作 `"[[Web开发地图]]"`。
            # 不手动加引号 —— `render` 会按值内容自动判断要不要加。
            "related": f"[[{domain_dir}地图]]" if domain_dir else "",
            "created": created,
        }
        if not meta["related"]:
            del meta["related"]
        return meta, body, dropped

    meta = {
        "type": "permanent",
        "tags": tags,
        "aliases": [a for a in plan.aliases if a],
        "created": created,
        "source": source,
        "note": note_line,
    }
    return meta, body, dropped


def _clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


# ---------------------------------------------------------------------------
# 预览
# ---------------------------------------------------------------------------


def render_preview(
    plans: Sequence[NotePlan],
    *,
    dir_rel: str,
    domain_tag: str,
    related: Sequence[str] = (),
    seconds_per_note: int = 60,
) -> str:
    """发给用户看的预览（**≤400 字，不发全文**）。用户回「落」才真写。"""
    lines = [f"📝 拟写 {len(plans)} 篇（预览，还没写进库）", ""]
    for i, p in enumerate(plans, 1):
        lines.append(f"{i}️⃣ {p.filename}.md")
        lines.append(f"    落点：{dir_rel}/ ｜ 标签：{domain_tag}")
        if related:
            lines.append("    关联：" + " · ".join(f"[[{n}]]" for n in related[:2]))
        lines.append("    大纲：" + " / ".join(s.heading for s in p.sections[:4]))
        lines.append("")

    total = sum(p.char_count for p in plans)
    est = max(30, len(plans) * seconds_per_note)
    # ⚠️ **按实测给分钟数**（原来拍脑袋写"约 1 分钟"，实测 148 秒 ——
    #   让你干等而告知只需 1 分钟，比慢本身更糟）。
    if est < 60:
        est_txt = f"{est} 秒"
    else:
        est_txt = f"{est // 60}–{(est + 29) // 60} 分钟"
    lines.append(f"预计写入约 {total // 1000} 千字符，需要 {est_txt}（分节多次调用模型）。")
    lines.append("")
    lines.append("回「落」写进去 · 回「算了」取消")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 与模型交互（三步）
# ---------------------------------------------------------------------------

OUTLINE_PROMPT = """用户在复习他自己的知识笔记，这次的主题是「{topic}」。

【他库里已有的相关笔记（节选）】
{context}

【要求】
1. 把这个主题拆成 {max_notes} 篇**聚焦**的笔记（宁少勿多，不要一篇包罗万象）。
2. 每篇给2-6 个章节标题，标题要具体（不要「概述」「总结」这种空标题）。
3. tags 给 2-4 个关键词（**不要**自己写 `领域/xxx`，那由程序按落点目录决定）。
4. 只输出 JSON，格式：
{{"notes":[{{"title":"主标题","subtitle":"副标题","aliases":["别名"],
"sections":[{{"heading":"一、章节名","points":["这节要讲什么"]}}]}}]}}"""

SECTION_PROMPT = """你在写一篇知识笔记的**第 {idx}/{total} 节**：{heading}。

【这一节要讲的重点】
{points}

【这篇笔记的大纲（全文）】
{outline}

【已经写过的内容（提要，勿重复）】
{written}

【全局约束】
- 只写第 {idx} 节，**不要**重复标题行，也不要写别的章节。
- 不要写"希望对你有帮助"这类客套话。
- 用中文，可用表格、代码块、`> 实例` 等形式；不要空话开场。
- 字数 {lo}-{hi} 字符。"""

FINISH_PROMPT = """这篇笔记已经写好了正文，现在补两处。

【主题】{topic}
【标题】{title} · {subtitle}
【大纲】
{outline}
【正文节选】
{excerpt}

【要求】
1. `intro`：一到两行引导，第一行 `> 目标：…`，第二行 `> **一句话定位：**…`。
2. `note`：一句话说明本文只讲什么，末尾用书名号提相邻笔记（**不要**用 [[]]）。
3. `outro`：3-5 条延伸学习线索，每条一行，以 `- ` 开头（**不要**给 [[双链]]）。
只输出 JSON：{{"intro":"...","note":"...","outro":["- ...","- ..."]}}"""

#: `chat_json` 的 schema 只作为**文档**留在那儿（这条路径已停用，见模块顶部）。
OUTLINE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "notes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "subtitle": {"type": "string"},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "sections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "heading": {"type": "string"},
                                "points": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["heading"],
                        },
                    },
                },
                "required": ["title", "sections"],
            },
        }
    },
    "required": ["notes"],
}

FINISH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intro": {"type": "string"},
        "note": {"type": "string"},
        "outro": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["intro", "note", "outro"],
}


def _client(client: Any = None) -> Any:
    from ..llm.client import get_client

    return client or get_client()


def _outline_text(plans: Sequence[NotePlan], limit: int = 6) -> str:
    out: list[str] = []
    for p in plans[:limit]:
        secs = " / ".join(s.heading for s in p.sections)
        out.append(f"- {p.filename}：{secs}")
    return "\n".join(out)


def plan_notes(
    topic: str,
    *,
    context: str = "",
    client: Any = None,
    max_notes: int = MAX_NOTES,
) -> list[NotePlan]:
    """第一步：拆题。返回 0-3 篇的蓝图（还没写正文）。"""
    cli = _client(client)
    prompt = OUTLINE_PROMPT.format(
        topic=topic, context=context or "（无）", max_notes=max_notes
    )
    text = cli.chat_text(
        [
            {"role": "system", "content": "你是帮人整理知识笔记的助手，输出结构化大纲。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
    )
    return parse_outline_text(text, max_notes=max_notes)


def fill_bodies(plan: NotePlan, *, client: Any = None) -> list[str]:
    """第二步：逐节填充正文。返回**问题列表**（空 = 全部成功）。

    ★ 每节单独调用（对抗 2048 token 上限），且每节 prompt 都带**完整大纲**
      + 已写各节 ≤200 字提要 + "本节只写第 N 节"，防止越写越跑偏。
    """
    cli = _client(client)
    outline = _outline_text([plan])
    problems: list[str] = []
    written: list[str] = []

    for i, sec in enumerate(plan.sections, 1):
        points = "\n".join(f"- {p}" for p in sec.points) or sec.heading
        done = "\n".join(written)[-200:] if written else "（无）"
        prompt = SECTION_PROMPT.format(
            idx=i,
            total=len(plan.sections),
            heading=sec.heading,
            points=points,
            outline=outline,
            written=done,
            lo=SECTION_MIN,
            hi=SECTION_MAX,
        )
        body = ""
        for attempt in range(SECTION_RETRY + 1):
            try:
                raw = cli.chat_text(
                    [
                        {"role": "system", "content": "你在写知识笔记的一节正文，输出纯正文。"},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.4,
                )
            except Exception:  # noqa: BLE001 —— 单节失败不该毁掉整篇
                raw = ""
            body = _clean_section(raw, sec.heading)
            if len(body) >= SECTION_USABLE:
                break
        if body:
            sec.body = body
            written.append(f"第{i}节已写：{_clip(body, 200)}")
        else:
            problems.append(f"「{sec.heading}」没写出来")

    return problems


def finish_note(plan: NotePlan, *, topic: str = "", client: Any = None) -> None:
    """第三步：补引导与延伸线索。**失败不致命** —— 留着空值也能交。"""
    from ..llm.client import LLMError

    cli = _client(client)
    excerpt = "\n".join(
        f"## {s.heading}\n{_clip(s.body, 200)}" for s in plan.sections
    )
    try:
        text = cli.chat_text(
            [
                {"role": "system", "content": "你是知识笔记的编辑，负责补引导与延伸线索。"},
                {
                    "role": "user",
                    "content": FINISH_PROMPT.format(
                        topic=topic or plan.title,
                        title=plan.title,
                        subtitle=plan.subtitle or "（无）",
                        outline=_outline_text([plan]),
                        excerpt=excerpt,
                    ),
                },
            ],
            temperature=0.3,
        )
    except LLMError:
        return

    data = parse_json_any(text)
    if isinstance(data, list) and data and isinstance(data[0], dict):
        data = data[0]
    if not isinstance(data, dict):
        return
    intro = str(data.get("intro") or "").strip()
    if intro:
        plan.intro = intro
    note = str(data.get("note") or "").strip()
    if note:
        plan.intro_note = note
    outro = _as_list(data.get("outro"))
    if outro:
        plan.outro = outro