"""目录扫描与笔记索引。

职责：把 vault 里所有 `.md` 扫成 `NoteMeta` 列表，并提供
① 同目录的**领域标签多数投票**（决定新笔记该写哪个标签）
② **文件名清洗**（决定新笔记叫什么）
③ 紧凑目录（给模型挑选相关笔记用，W3）

**这一层不调模型、不写文件** —— 纯读 + 纯函数，所以能单测、能复现。

★★ **两条纪律**（都会"静默算错"，所以在这里写死）：
1. **单篇读不动不能拖垮整库**：编码错、权限错、解析异常 → 跳过该篇，继续扫。
2. **标签按实测多数投票，不按目录名**：实测库里 3 个领域的标签与目录名不符
   （`领域/编程` vs 目录"编程与系统"等，见 `docs/knowledge-design.md` §3.4）。
   照目录名拼标签会让新笔记在图谱里**孤立成一种新颜色**。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence

from . import frontmatter

#: 领域标签前缀。图谱的分色就认这个前缀。
DOMAIN_PREFIX = "领域/"

#: 领域目录的固定父目录名。新目录只允许建在它下面。
DOMAIN_ROOT = "领域"

#: 这些 `type` 值的 frontmatter 属于"结构文件"（地图 / 索引 / 收件箱），
#: **不是知识笔记** —— 检索与"就近对齐"都要排除（审查补的，见 `real_notes`）。
_STRUCTURE_TYPES = {"map", "inbox", "index", "moc"}

#: Windows 非法文件名字符 + 控制符。
_ILLEGAL_CHARS = set('\\/:*?"<>|')

#: Windows **保留设备名** —— `CON.md` / `PRN` / `AUX` / `NUL` / `COM1`… 这类名字
#: 在 NTFS 上**根本创建不出来**（会报 `OSError`），而在 POSIX 上完全正常。
#: 本机跑 Windows，但代码也要能跑测试用的临时目录，所以在这里就挡掉。
_RESERVED_NAMES = (
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

#: 文件名长度上限（不含 `.md`）。NTFS 单段上限 255，留足余量。
MAX_NAME_LEN = 80

#: H1 标题行。
_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


# ---------------------------------------------------------------------------
# 数据契约
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NoteMeta:
    """一篇笔记的元信息。**只有轻量字段** —— 正文不在这里（11.7 万字不进内存常驻）。"""

    rel: str          # 相对 vault 的路径（POSIX 风格，如 `领域/金融投资/沪深300·宽基与算法设计.md`）
    name: str         # 文件名（不含扩展名）—— 检索与双链都用它
    dir_rel: str      # 相对 vault 的目录（`领域/金融投资`；vault 根下则为 `""`）
    title: str        # H1 标题（没有则回落文件名）
    tags: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    note: str = ""    # frontmatter 的 `note` 字段（一句话定位）
    schema: str = "none"   # new（有 type）/ old（有 category）/ none（都没有）
    #: 旧式 schema 的 `category` 值（如 `学习`）。新式笔记为空。
    #: ★ 审查补的：要往旧式领域（`Web开发` / `自我与职业`）写新笔记时得**沿用旧字段**，
    #:   而"沿用什么"只能从兄弟笔记身上问出来。
    category: str = ""
    type_value: str = ""   # frontmatter 的 `type` 原始值（`permanent` / `map` / `inbox` …）
    mtime: float = 0.0
    size: int = 0

    @property
    def domain_tag(self) -> str:
        """这篇笔记自己的领域标签（可能为空）。"""
        for t in self.tags:
            if t.startswith(DOMAIN_PREFIX):
                return t
        return ""

    @property
    def summary(self) -> str:
        """一句话（给检索和目录用）：优先 `note`，否则标题。"""
        return (self.note or self.title).strip()


@dataclass
class Catalog:
    """一次扫描的结果。用 `load()` 拿，别自己 `_scan()`。"""

    vault: Path
    notes: list[NoteMeta] = field(default_factory=list)
    #: `领域/` 下**磁盘上真实存在**的一级子目录（含**空目录**）。
    #: ★ 审查补的（2026-10-06）：`notes` 只能反映"有 md 文件的目录"，
    #:   所以 `领域/量子力学/` 刚建好、还没写笔记时会被判"不存在" → 白名单拒写。
    #:   这跟"用户点头才建目录"的流程直接冲突：建完立刻不能用。
    dirs_on_disk: list[str] = field(default_factory=list)

    # ---- 查询 ----

    def in_dir(self, dir_rel: str) -> list[NoteMeta]:
        """某个目录下的所有笔记（字典序）。"""
        d = (dir_rel or "").replace("\\", "/").strip("/")
        return [n for n in self.notes if n.dir_rel == d]

    def real_notes(self) -> list[NoteMeta]:
        """**真笔记** —— 排掉地图页 / 索引 / 收件箱 / 模板这类"结构文件"。

        判据（**审查补的第 2、3 条**：原来只按路径排，真库实测
        「想学收件箱」会把 `inbox/收件箱.md` 当知识笔记列出来、
        「学习路径」会命中 `学习路径总览.md` —— 用户看到的是"你的收件箱讲…"）：

        1. 路径：`模板/` 与 `.` 开头目录
        2. **frontmatter 的 `type`**：`map` / `inbox` / `index` / `moc` 一律不是知识笔记
        3. 地图页（文件名以 `地图` 结尾）
        """
        out: list[NoteMeta] = []
        for n in self.notes:
            if n.dir_rel.startswith("模板") or n.dir_rel.startswith("."):
                continue
            if n.rel.startswith("模板/"):
                continue
            if n.type_value.lower() in _STRUCTURE_TYPES:
                continue
            if n.name.endswith("地图"):
                continue
            out.append(n)
        return out

    def names(self) -> set[str]:
        """全库笔记名集合 —— 用来校验 `[[双链]]` 的目标是否真实存在。"""
        return {n.name for n in self.notes}

    def domain_dirs(self) -> list[str]:
        """`领域/` 下的**一级**子目录（白名单来源）。

        ★ 取 `notes` 反推的目录与**磁盘上真实存在的目录**的并集（审查补的）——
        后者才包含"刚建好、还没有笔记"的空目录。
        """
        prefix = DOMAIN_ROOT + "/"
        dirs = set(self.dirs_on_disk)
        for n in self.notes:
            if n.dir_rel.startswith(prefix):
                rest = n.dir_rel[len(prefix) :]
                if rest:
                    dirs.add(prefix + rest.split("/")[0])
        return sorted(dirs)

    def vote_domain_tag(self, dir_rel: str) -> str:
        """**同目录多数投票**：这个目录的兄弟笔记最常用哪个 `领域/xxx` 标签。

        并列时取更长的（更具体），再按字典序 —— 保证结果确定、可复现。
        """
        counts: Counter[str] = Counter()
        for n in self.in_dir(dir_rel):
            tag = n.domain_tag
            if tag:
                counts[tag] += 1
        if not counts:
            return ""
        return max(counts.items(), key=lambda kv: (kv[1], len(kv[0]), kv[0]))[0]

    def header_schema(self, dir_rel: str) -> str:
        """这个目录的兄弟笔记主要用哪套 frontmatter（`new` / `old`）。

        新笔记要**就近对齐**（见 `docs/knowledge-design.md` §3.2）。
        """
        counts: Counter[str] = Counter()
        for n in self.in_dir(dir_rel):
            if n.schema in ("new", "old"):
                counts[n.schema] += 1
        if not counts:
            return "new"
        return max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]

    def vote_category(self, dir_rel: str) -> str:
        """旧式领域里 `category` 的多数值（如 `学习`）。**新式领域返回空串。**

        往旧式领域写新笔记时要沿用它 —— 库里 12 篇旧笔记都这么写，
        新笔记孤零零挂一套新字段，Obsidian 的查询面板按 `category` 筛时就漏掉。
        """
        counts: Counter[str] = Counter(
            n.category for n in self.in_dir(dir_rel) if n.category
        )
        if not counts:
            return ""
        return max(counts.items(), key=lambda kv: (kv[1], len(kv[0]), kv[0]))[0]

    def compact_lines(self, *, limit: int = 0) -> list[str]:
        """紧凑目录：每篇一行 `名字 | 目录 | 一句话`（给模型挑选用）。

        默认**跳过后缀为 `地图` 的 MOC 与无 frontmatter 的结构文件**。
        """
        out: list[str] = []
        for n in self.notes:
            if n.schema == "none" or n.name.endswith("地图"):
                continue
            out.append(f"{n.name} | {n.dir_rel} | {n.summary[:40]}")
            if limit and len(out) >= limit:
                break
        return out


# ---------------------------------------------------------------------------
# 扫描 + 缓存
# ---------------------------------------------------------------------------

#: 缓存：vault 路径 → (指纹, Catalog)。**按路径分键** → 测试用临时 vault 不会互相污染。
_CACHE: dict[str, tuple[tuple[int, float, int], Catalog]] = {}


def _stamp(vault: Path) -> tuple[int, float, int]:
    """目录指纹：`(md 文件数, 最大 mtime, 文件名集合的哈希)`。

    ★ 名字哈希是**审查补的**（2026-10-06）：原来的 `(文件数, 最大 mtime)`
      对**重命名完全不可见** —— `os.replace` 保留 mtime、文件数也不变，
      指纹一模一样 → 用户在 Obsidian 里重命名一篇笔记（**最常用的操作**）之后，
      程序仍拿着旧名字的索引，会说"库里没有这篇"。

    只 stat、不读内容 —— 33 个文件量级下几毫秒，换来"没变就不重新解析"。
    """
    count = 0
    newest = 0.0
    names: list[str] = []
    try:
        for p in vault.rglob("*.md"):
            try:
                st = p.stat()
            except OSError:
                continue
            count += 1
            newest = max(newest, st.st_mtime)
            names.append(p.name)
        # ★ 一级目录名也要进指纹（审查补的）：新建的 `领域/xxx/` 是**空目录**，
        #   既不改 md 文件数也不改 mtime → 缓存不失效 → 白名单还当它不存在。
        names.extend(_domain_dirs_on_disk(vault))
    except OSError:
        return (0, 0.0, 0)
    names.sort()
    return (count, newest, hash(tuple(names)))


def load(vault: Path | str) -> Catalog:
    """扫描（带缓存）。**唯一的入口** —— 不要自己 `_scan()`。"""
    root = Path(vault)
    key = str(root)
    stamp = _stamp(root)
    hit = _CACHE.get(key)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    cat = _scan(root)
    _CACHE[key] = (stamp, cat)
    return cat


def reset_cache() -> None:
    """清缓存。**只在测试里用**（或手动改完库想让程序立刻看见）。"""
    _CACHE.clear()


def _domain_dirs_on_disk(vault: Path) -> list[str]:
    """`领域/` 下真实存在的一级子目录（含**空目录**）。"""
    root = vault / DOMAIN_ROOT
    out: list[str] = []
    try:
        for p in root.iterdir():
            try:
                if p.is_dir() and not p.name.startswith("."):
                    out.append(f"{DOMAIN_ROOT}/{p.name}")
            except OSError:
                continue
    except OSError:
        return []
    return sorted(out)


def _scan(vault: Path) -> Catalog:
    notes: list[NoteMeta] = []
    dirs = _domain_dirs_on_disk(vault)
    try:
        paths = sorted(vault.rglob("*.md"))
    except OSError:
        return Catalog(vault=vault, notes=[], dirs_on_disk=dirs)

    for p in paths:
        try:
            if not p.is_file() or p.name.startswith("."):
                continue
            st = p.stat()
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # ★ 纪律一：单篇读不动 → 跳过，不拖垮整库
            continue
        try:
            meta, body = frontmatter.parse(text)
            notes.append(_make_meta(vault, p, st, meta, body))
        except Exception:  # noqa: BLE001 —— 兜底同上，一篇坏笔记不能毁掉整次扫描
            continue
    return Catalog(vault=vault, notes=notes, dirs_on_disk=dirs)


def _make_meta(vault: Path, p: Path, st, meta: dict, body: str) -> NoteMeta:
    rel = p.relative_to(vault).as_posix()
    parent = p.parent.relative_to(vault).as_posix()
    dir_rel = "" if parent == "." else parent
    m = _H1_RE.search(body)
    title = (m.group(1).strip() if m else "") or p.stem
    # ★ 判据是**有没有 `category`**，不是"有没有 `type`"（2026-10-06 真库实测修正）。
    #   原写法 `if "type" in meta: new / elif "category" in meta: old` 在真库上
    #   **一个旧式领域都识别不出来** —— 因为库里 12 篇旧笔记**同时**写着
    #   `type: permanent` 和 `category:`（`01-前端基础地图` 等）。
    #   `type` 两套都有，只有 `category` 是旧式**独有**的标记。
    if "category" in meta:
        schema = "old"
    elif "type" in meta:
        schema = "new"
    else:
        schema = "none"
    return NoteMeta(
        rel=rel,
        name=p.stem,
        dir_rel=dir_rel,
        title=title,
        tags=frontmatter.as_list(meta.get("tags")),
        aliases=frontmatter.as_list(meta.get("aliases")),
        note=str(meta.get("note") or "").strip(),
        schema=schema,
        type_value=str(meta.get("type") or "").strip(),
        category=str(meta.get("category") or "").strip(),
        mtime=st.st_mtime,
        size=st.st_size,
    )


# ---------------------------------------------------------------------------
# 文件名清洗
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")


def sanitize_name(topic: str, *, max_len: int = MAX_NAME_LEN) -> str:
    """把用户给的主题清成**安全文件名**（**不含 `.md`**，由调用方补）。

    做四件事：
    1. 去掉路径分隔符与 Windows 非法字符 —— **这是防目录穿越的第一道闸**
    2. 抹掉 `..`（第二道闸；第三道是写盘前的 `resolve()` 断言）
    3. 压掉连续空白、去掉首尾的点与空格（Windows 不允许文件以点或空格结尾）
    4. 截断到 `max_len`
    5. Windows 保留设备名加下划线后缀（`CON` → `CON_`）

    清完是空串时返回 `""`，由调用方决定兜底名（**不要在这里编一个** ——
    调用方更清楚业务语义）。
    """
    s = (topic or "").strip()
    s = "".join(ch for ch in s if ch not in _ILLEGAL_CHARS and ord(ch) >= 32)
    s = s.replace("..", "")
    s = _WS_RE.sub(" ", s)
    s = s.strip(" .")
    if len(s) > max_len:
        s = s[:max_len].strip(" .")
    # ★ 保留名只在**整段**等于它时才算（`CON` 是，`CONTENT` 不是；
    #   `CON.md` 传进来时后缀已被调用方剥掉）。
    if s.upper() in _RESERVED_NAMES:
        s += "_"
    return s


def join_name(prefix: str, suffix: str = "") -> str:
    """按库里的命名习惯拼笔记名：`主标题 · 副标题`（**空格 + 中点 + 空格**）。

    实测库里就是 `Git与版本控制 · 从对象模型到分布式协作` 这个写法
    （见 `docs/knowledge-design.md` §3.3）。任一段为空就只用另一段。
    """
    a, b = sanitize_name(prefix), sanitize_name(suffix)
    if a and b:
        return f"{a} · {b}"
    return a or b


def is_domain_dir(cat: Catalog, dir_rel: str) -> bool:
    """`dir_rel` 是不是一个**已存在的** `领域/<一级>/` 目录（白名单判据）。"""
    d = (dir_rel or "").replace("\\", "/").strip("/")
    return d in set(cat.domain_dirs())


def pick_domain_dir(cat: Catalog, hint: str) -> Optional[str]:
    """在**已存在的领域目录**里挑一个跟 `hint` 最像的（用于"这篇该放哪"）。

    匹配顺序：目录名完全包含 → 目录下笔记名/标签命中 → 都无则 `None`。
    **只在已存在目录里挑** —— 新建目录必须由用户点头（写盘层强制）。
    """
    h = (hint or "").strip()
    if not h:
        return None
    dirs = cat.domain_dirs()
    for d in dirs:
        if h in d or d.split("/")[-1] in h:
            return d
    for d in dirs:
        for n in cat.in_dir(d):
            hay = n.name + " " + " ".join(n.tags) + " " + n.note
            if h and h in hay:
                return d
    return None
