"""知识库技能的**会话侧门面** —— 把"取数"变成给用户看的回复文本。

`conversation.py` 只做分派（保持薄，见 `AGENTS.md` 的代码风格约定），
取数、文案、模型调用都在本模块。

---

## 四条产品纪律（来自 `docs/knowledge-design.md` §1）

1. **库里没有 → 建议开新主题，但绝不自己建**（建目录必须用户点头）
2. **列表要短**：微信里刷屏没人看，最多列 3 篇
3. **没配路径 = 明确说"没配"**，不是默默失败
4. **只讲笔记里有的**：模型回答必须以笔记正文为据，不编；笔记没写的就直说"你笔记里没这块"

## 本模块与 W3 的分工

| 波 | 做什么 |
|---|---|
| **W2（本模块现有）** | `learn` 列相关笔记 → `pick` 选定一篇 → `answer` 基于笔记正文回答 |
| W3 | 拆题、逐节生成新笔记、预览、落盘（`generate.py` / `write.py`） |
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from ... import config

from . import catalog, retrieval
from .catalog import NoteMeta

#: 微信里一次最多列几篇笔记（纪律 2）。
MAX_LIST = 3

#: 「你挑一个」时最多列几个领域 —— 微信里超过 5 行就没人看了。
MAX_DOMAINS = 5

#: 未配置路径时的提示（纪律 3）。
#: ⚠️ 这里**不能写死任何具体路径** —— 它是给用户看的操作指引，
#: 写一行作者本机的路径，别人照着填只会填错。
NOT_CONFIGURED = (
    "知识库还没接上 —— 没配路径。\n"
    "在项目根目录的 .env 里加一行：\n"
    "    KNOWLEDGE_VAULT_DIR=<你的 Obsidian 仓库绝对路径>\n"
    "重启服务就生效。"
)


# ---------------------------------------------------------------------------
# 配置与取数
# ---------------------------------------------------------------------------


def vault_dir() -> str:
    """知识库根目录（来自 `config`）。**空串 = 未配置**。"""
    return (config.KNOWLEDGE_VAULT_DIR or "").strip()


def is_configured() -> bool:
    return bool(vault_dir())


def load_catalog() -> Optional[catalog.Catalog]:
    """扫库。未配置或目录**不存在**时返回 `None`（**不抛异常**）。

    ⚠️ 目录不存在**必须判**（审查抓到的静默错，2026-10-06）：`.env` 里路径写错时
    （Windows 上反斜杠被吃掉是高发场景 —— `config.py` 的注释自己就警告过），
    `catalog.load` 会返回一个**空** Catalog → 用户看到的是
    「库里没有讲缓存的笔记…要不要开一个新主题？」—— 完全不像配置错误，
    还会**引导用户去建一个错的目录**。
    """
    if not is_configured():
        return None
    root = Path(vault_dir())
    if not root.is_dir():
        return None
    try:
        return catalog.load(root)
    except Exception:  # noqa: BLE001 —— 磁盘/权限问题不该把整条会话打死
        return None


def find_by_name(cat: catalog.Catalog, name: str) -> Optional[NoteMeta]:
    """按笔记名找（先精确、再去空白比对）。"""
    key = (name or "").strip()
    if not key:
        return None
    for n in cat.notes:
        if n.name == key:
            return n
    squashed = key.replace(" ", "")
    for n in cat.notes:
        if n.name.replace(" ", "") == squashed:
            return n
    return None


# ---------------------------------------------------------------------------
# 「我想学 X」
# ---------------------------------------------------------------------------


def reply_learn(text: str) -> tuple[str, list[str], str]:
    """处理「我想学 X」。

    返回 `(回复文本, 候选笔记名列表, 建议的新领域名)`：

    - 候选交给会话层存进 state，用户下一步回序号时用（`resolve_pick`）
    - **建议的新领域名**：库里没有这个主题时非空，交给会话层存进
      `pending_domain`，用户回「开」时才真的建目录（纪律 1：**必须用户点头**）。
      这一项是审查补的 —— 原来回复里承诺了"你说『开』我就建"，
      但程序**根本没记住**要建什么，那个承诺是假的。
    """
    cat = load_catalog()
    if cat is None:
        return NOT_CONFIGURED, [], ""

    topic = retrieval.normalize_topic(text) or text.strip()
    hits = retrieval.find_related(cat, text, limit=MAX_LIST)
    if hits:
        return _format_hits(topic, hits), [h.note.name for h in hits], ""
    return _suggest_new_topic(topic, cat), [], topic


def _format_hits(topic: str, hits: list[retrieval.Hit]) -> str:
    lines = [f"你库里讲「{topic}」的有 {len(hits)} 篇：", ""]
    for i, h in enumerate(hits, 1):
        n = h.note
        lines.append(f"{i}️⃣ {n.name}")
        if n.summary:
            lines.append(f"    {_clip(n.summary, 60)}")
        lines.append("")
    lines.append("想聊哪篇？回个序号就行。")
    return "\n".join(lines)


def _suggest_new_topic(topic: str, cat: catalog.Catalog) -> str:
    """库里没有这个主题 → **建议**开新领域，但不建（纪律 1）。"""
    dirs = cat.domain_dirs()
    existing = "、".join(d.split("/")[-1] for d in dirs) if dirs else "（空）"
    return (
        f"库里没有讲「{topic}」的笔记。\n\n"
        f"你的库现在有这几个领域：{existing}\n\n"
        f"要不要开一个新主题？我建议放在 `{catalog.DOMAIN_ROOT}/{topic}/` 下。\n"
        f"你说「开」我就建目录（**我不会自己动手建**）。"
    )


def create_domain(topic: str) -> str:
    """**用户点头后**建一个新领域目录。返回给用户看的回复文本。

    ⚠️ 只在用户明确回「开」时才走到这里 —— 纪律 1（`docs/knowledge-design.md` §2）：
    绝不自己建目录。这条路径以前**在回复里承诺了却没有实现**，用户回「开」
    只会拿到帮助菜单（审查抓到的功能缺口）。

    三道校验（任一不过就明确报错，不猜）：
    1. 主题清洗后为空 → 不建
    2. `领域/<名字>` 已经存在 → 不重复建，直接告诉他
    3. 只建**一级**（`write.py` 的硬闸6）
    """
    from . import write

    if not is_configured():
        return NOT_CONFIGURED

    name = catalog.sanitize_name(topic)
    if not name:
        return "你想开什么主题？把名字说给我，比如「我想学量子力学」。"

    # ★ **主题名里带路径分隔符或 `..` → 直接拒绝，不静默改名**。
    #   `sanitize_name` 会把 `../../evil` 洗成 `evil`（那样确实不会越界），
    #   但用户说的是"../../evil"、我们建出的是 `evil` —— **静默改了用户输入**，
    #   而"我以为是 A 结果建了 B"正是最该被拒绝的那类情况。
    raw = (topic or "").strip()
    if "/" in raw or "\\" in raw or ".." in raw or ":" in raw:
        return (
            f"主题名里不能带路径符号（你给的是 {topic!r}）。\n"
            f"换个简单的名字，比如「{name}」。"
        )

    dir_rel = f"{catalog.DOMAIN_ROOT}/{name}"
    target = Path(vault_dir()) / dir_rel

    try:
        # ★ 越界守卫先跑（`write.py` 的同一道闸）—— `name` 已清洗过，
        #   但多一层校验不花钱，而这是**建目录**，比写文件更敏感。
        root = Path(vault_dir())
        if not write.within(root, target):
            return f"这个主题名不能用（会写到知识库外面）：{topic!r}"
        if target.exists():
            return f"`{dir_rel}` 已经在了，直接说「我想学 {topic}」就能往里写。"
        target.mkdir(parents=False, exist_ok=False)
    except write.WriteError as exc:
        return f"建不了：{exc}"
    except OSError as exc:
        return f"建目录失败：{exc}"

    # 让下一次 `load()` 立刻看见这个新目录（空目录对旧指纹不可见）
    catalog.reset_cache()
    return (
        f"建好了：`{dir_rel}/`\n\n"
        f"现在可以「我想学 {topic}」开聊；"
        f"聊完说「总结一下」我就能往这个目录里写第一篇。"
    )


# ---------------------------------------------------------------------------
# 「你挑一个」/ 领域总览
# ---------------------------------------------------------------------------


def reply_browse() -> tuple[str, list[str]]:
    """「随便挑一个」→ 列领域（附每个领域最近的一篇作示例）。"""
    cat = load_catalog()
    if cat is None:
        return NOT_CONFIGURED, []

    rows = retrieval.domain_overview(cat)
    rows = [r for r in rows if r[1] > 0]
    if not rows:
        return "你的知识库里还没有可聊的笔记（只有地图页和索引）。", []

    # 纪律 2：微信里超过 5 行就没人看了
    rows = rows[:MAX_DOMAINS]
    lines = ["你库里这几个领域：", ""]
    candidates: list[str] = []
    for i, (dir_rel, count, tag) in enumerate(rows, 1):
        lines.append(f"{i}️⃣ {dir_rel.split('/')[-1]}（{count} 篇）")
        latest = retrieval.recent_in_dir(cat, dir_rel, limit=1)
        if latest:
            lines.append(f"    最近更新：{latest[0].name}")
            candidates.append(latest[0].name)
        lines.append("")
    lines.append("回个序号，我陪你过一遍那篇。")
    return "\n".join(lines), candidates


# ---------------------------------------------------------------------------
# 选定一篇（序号 / 名字）
# ---------------------------------------------------------------------------


def resolve_pick(
    text: str,
    candidates: list[str],
    cat: catalog.Catalog,
) -> Optional[NoteMeta]:
    """把用户的一句话解析成"要聊的那篇笔记"。

    认三种说法：**序号**（"1" / "第2" / "二"）、**候选里的名字**（含前缀）、
    以及**全库里的名字**。
    """
    t = (text or "").strip()
    if not t:
        return None

    idx = _parse_index(t, len(candidates))
    if idx is not None:
        return find_by_name(cat, candidates[idx])

    for name in candidates:
        if name and (name in t or t in name):
            return find_by_name(cat, name)

    return find_by_name(cat, t)


#: 「1」「第2」「二」—— 与穿搭那边的序号规则保持一致（用户不需要学两套）。
#:
#: ⚠️ 范围开到 **9**（不是 5）：领域数量会长 —— `browse` 一次最多列 5 个领域，
#: 但真到那时候序号还得认得出，写死 5 会让"第 6 个领域"永远选不中。
_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9}

_INDEX_RE = re.compile(r"^\s*第?\s*([1-9一二两三四五六七八九])\s*[篇个]?\s*$")


def _parse_index(text: str, count: int) -> Optional[int]:
    """解析序号 → **0-based 下标**。解不出或越界返回 `None`。"""
    m = _INDEX_RE.match(text or "")
    if not m or count <= 0:
        return None
    raw = m.group(1)
    n = int(raw) if raw.isdigit() else _CN_NUM.get(raw)
    if n is None or not (1 <= n <= count):
        return None
    return n - 1


def reply_focus(note: NoteMeta) -> str:
    """选定某篇之后的回复：亮出身份 + 给出这篇的定位，邀请提问。"""
    lines = [f"好，我们就聊这篇：", f"《{note.name}》"]
    if note.dir_rel:
        lines.append(f"（{note.dir_rel}）")
    if note.summary:
        lines.append("")
        lines.append(_clip(note.summary, 120))
    lines.append("")
    lines.append("关于这篇的任何问题都可以问我；想让我讲哪一段，直接说。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 基于笔记正文回答
# ---------------------------------------------------------------------------


#: 讨论用的系统提示。
#:
#: ⚠️ 三条写死的约束（对应纪律 4）：
#: 1. **只依据给的笔记正文**，不许拿"通用知识"补 —— 用户要的是"复习他自己的东西"，
#:    模型自由发挥会把他的笔记和网上泛泛而谈混在一起，反而污染认知。
#: 2. 笔记没写的，**明说没写**（这本身就是有价值的信息：提示他该补哪块）。
#: 3. 用口语，别写成教科书（他要的是"陪读"，不是又一篇文档）。
_DISCUSS_SYSTEM = (
    "你在陪用户复习他自己写的知识笔记。规则：\n"
    "1. 只依据下面给你的【笔记正文】回答，不要引入笔记之外的知识；\n"
    "2. 笔记里没有提到的，直接说「这篇里没写到这块」——这对他有用；\n"
    "3. 中文口语，像朋友聊天，别写成教科书；\n"
    "4. 他问得含糊时，可以指出笔记里最相关的段落，别硬猜。\n"
    "5. 控制在 300 字以内。"
)


def reply_answer(note: NoteMeta, question: str) -> str:
    """基于某篇笔记的正文回答用户的问题。**读不到正文就如实说**。"""
    vault = vault_dir()
    body = retrieval.read_body(vault, note, max_chars=6000)
    if not body:
        return f"这篇读不出来（文件可能坏了）：{note.name}"

    try:
        from ..llm.client import LLMError, get_client
    except Exception:  # noqa: BLE001
        return "模型模块没接上，暂时聊不了。"

    messages = [
        {"role": "system", "content": _DISCUSS_SYSTEM},
        {"role": "user", "content": f"【笔记正文】\n{body}\n\n【我的问题】\n{question.strip()}"},
    ]
    try:
        answer = get_client().chat_text(messages, temperature=0.3)
    except LLMError as exc:
        return f"模型没接上，这次答不了：{exc}"
    answer = (answer or "").strip()
    if not answer:
        return "模型这次没给出内容，再问一次试试？"
    return answer


# ---------------------------------------------------------------------------
# 生成新笔记（W3）：生成 → 预览；落盘交给 write.py
# ---------------------------------------------------------------------------

#: 喂给"拆题"的相关笔记正文，每篇最多取多少字符（防止把 prompt 撑爆）。
CONTEXT_PER_NOTE = 1500


def generate_and_preview(
    *,
    topic: str,
    focus: Optional[NoteMeta] = None,
    client: Any = None,
) -> tuple[str, Optional[dict[str, Any]]]:
    """生成笔记并返回 `(预览文本, 待落盘数据)`；失败时返回 `(错误提示, None)`。

    **这一步不写任何文件** —— 落盘必须等用户回「落」（需求 5）。
    """
    from ..llm.client import LLMError

    from . import generate

    cat = load_catalog()
    if cat is None:
        return NOT_CONFIGURED, None

    # ---- 材料：聚焦的那篇 + 检索到的相关篇 ----
    picks: list[NoteMeta] = []
    if focus is not None:
        picks.append(focus)
    for h in retrieval.find_related(cat, topic, limit=MAX_LIST):
        if all(p.name != h.note.name for p in picks):
            picks.append(h.note)
    if not picks:
        return (
            f"库里没有跟「{topic}」相关的笔记，我不想凭空编一篇。\n"
            f"要不换个主题？",
            None,
        )

    context = "\n\n".join(
        f"### {p.name}\n{retrieval.read_body(vault_dir(), p, max_chars=CONTEXT_PER_NOTE)}"
        for p in picks[:3]
    )

    try:
        plans = generate.plan_notes(topic, context=context, client=client)
        if not plans:
            return "模型这次没给出可用的提纲，再说一次试试？", None
        for plan in plans:
            generate.fill_bodies(plan, client=client)
            generate.finish_note(plan, topic=topic, client=client)
    except LLMError as exc:
        return f"模型没接上，这次生成不了：{exc}", None

    # ---- 落点：跟着聚焦的那篇走（同主题的兄弟该放一处）----
    anchor = focus or picks[0]
    dir_rel = anchor.dir_rel or (cat.domain_dirs()[0] if cat.domain_dirs() else "")
    if not dir_rel:
        return "库里还没有领域目录，先建一个再让我写吧。", None
    if not catalog.is_domain_dir(cat, dir_rel):
        # ⚠️ 焦点可能落在 `inbox/` 这种**非领域目录**（收件箱、索引也能被
        # `find_by_name` 选中），而写盘只允许 `领域/` 之下 ——
        # 不先校验就会出现「**预览说能写、落盘被拒**」，用户白等十几秒模型调用。
        dir_rel = next(
            (p.dir_rel for p in picks[1:] if catalog.is_domain_dir(cat, p.dir_rel)), ""
        )
        if not dir_rel:
            return (
                "这个话题还不属于任何一个领域，我不确定该写去哪。\n"
                "你先说说它算哪一块（或者我先给你开一个新领域），我再写。"
            ), None
    domain_tag = cat.vote_domain_tag(dir_rel) or f"{catalog.DOMAIN_PREFIX}{dir_rel.split('/')[-1]}"
    # ★ 旧式领域（`Web开发` / `自我与职业`，共 12 篇旧笔记）要**沿用旧字段**，
    #   否则新笔记孤零零挂一套新字段，库里按 `category` 筛的查询面板看不到它。
    header_style = cat.header_schema(dir_rel)
    old_category = cat.vote_category(dir_rel) if header_style == "old" else ""

    known = cat.names()
    # ★ "相邻"用**真的相关**的笔记（焦点那篇 + 检索命中的），不能由 assemble 瞎挑
    related = [p.name for p in picks[:2]]
    notes: list[dict[str, Any]] = []
    filled_plans = []
    for plan in plans:
        if not plan.filled:
            continue                      # 有节没写出来 → 这篇不交，别交半成品
        meta, body, _dropped = generate.assemble(
            plan, domain_tag=domain_tag, known_names=known, related=related,
            domain_dir=dir_rel.split("/")[-1],
            header_style=header_style, old_category=old_category, topic=topic,
        )
        notes.append({"filename": plan.filename, "meta": meta, "body": body})
        filled_plans.append(plan)
    if not notes:
        return "模型这次每一节都没写全，我没敢交给你。再说一次试试？", None

    preview = generate.render_preview(
        filled_plans, dir_rel=dir_rel, domain_tag=domain_tag, related=related
    )
    pending = {
        "dir": dir_rel,
        "domain_tag": domain_tag,
        "related": related,
        "notes": notes,
        "preview": preview,
        "at": _now_iso(),
    }
    return preview, pending


def commit_pending(pending: dict[str, Any]) -> tuple[str, int]:
    """把预览过的东西真正写进库。返回 `(回复文本, 成功写入的篇数)`。

    ⚠️ 只新建、绝不覆盖（`write.py` 的六条硬闸）—— 这里不开任何例外。

    ★ **必须返回成功篇数**（审查抓到的静默丢数据）：一篇都没写成时
      （X: 盘掉线、目录被 Obsidian 改名、落点不合法…），
      调用方**不能**把 `pending_note` 清掉 —— 那是十几次模型调用的产物，
      清了就**永久丢失、而且没有重试入口**。
    """
    from . import write

    vault = vault_dir()
    written: list[str] = []
    failed: list[str] = []
    for item in pending.get("notes") or []:
        try:
            path = write.write_note(
                vault,
                pending.get("dir") or "",
                item.get("filename") or "",
                item.get("meta") or {},
                item.get("body") or "",
            )
            written.append(path.name)
        except write.WriteError as exc:
            failed.append(f"{item.get('filename')}（{exc}）")

    lines: list[str] = []
    if written:
        lines.append("写好了：")
        for name in written:
            lines.append(f"· {pending.get('dir')}/{name}")
    if failed:
        if lines:
            lines.append("")
        lines.append("这几篇没写成：")
        for f in failed:
            lines.append(f"· {f}")
    if written:
        lines.append("")
        lines.append("想让它们也出现在领域地图里的话，把下面这些行粘进去：")
        for name in written:
            lines.append(write.map_line(_stem(name)))
    return "\n".join(lines), len(written)


def _stem(filename: str) -> str:
    return filename[:-3] if filename.endswith(".md") else filename


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------


def _clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"
