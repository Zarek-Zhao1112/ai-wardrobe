"""安全写盘 —— **本技能唯一会碰用户真实知识库的地方**。

所以这里的每一条都不是"最佳实践"，而是**硬闸**（`docs/knowledge-design.md` §7）：

1. **目录只来自白名单**：必须是 `领域/<已存在的一级目录>`；或用户点头后的新目录。
   用户输入的主题**只决定文件名**，永远不参与拼目录路径。
2. `sanitize_name` 清洗文件名（去分隔符/非法字符/`..`）。
3. **路径越界断言**：落盘前 `resolve()` 必须落在 vault 内，否则拒绝。
4. **只新建不覆盖**：同名自动加 `-2 / -3` …（上限 50），**永不覆盖**。
5. **原子写**：临时文件写在**目标同目录**再 `os.replace`
   —— 跨盘（C: → X:）时不用系统临时目录，否则 `os.replace` 会因跨卷失败。
6. `mkdir` **只在校验通过后发生**，且新目录**只建一级**。

---

## 一个明确的假设

**单写者**：同一时刻只有一条会话在写。`unique_path()` 的"先查后写"之间理论上有竞态窗口，
但本项目是单用户单进程，且会话处理是串行的 —— 引锁的复杂度换不来真实收益。

另外：临时文件以 `.` 开头命名，`catalog._scan()` 会跳过它们，
所以**写到一半崩了也不会污染笔记索引**。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from . import catalog, frontmatter

#: 落点必须在这个顶层目录之下。**写死** —— 知识库的顶层结构不该由程序扩张。
ALLOWED_ROOT = catalog.DOMAIN_ROOT

#: 同名时的后缀上限。
MAX_DEDUP = 50


class WriteError(Exception):
    """写盘被拒或失败。

    ⚠️ 与 `channels.ChannelError`、`llm.LLMError` 同一套规矩：
    **不要把 `OSError` 等底层异常漏给调用方** —— 业务层只该认识这一种。
    """


# ---------------------------------------------------------------------------
# 路径守卫
# ---------------------------------------------------------------------------


def within(root: Path, target: Path) -> bool:
    """`target` 是否落在 `root` 之内（含等于 `root` 本身）。

    ⚠️ 不用 `str.startswith` 直接比：`C:\\vault2` 会以 `C:\\vault` 开头；
    ⚠️ 不用 `root in target.parents`：Windows 下 Path 比较区分大小写，
    `C:\\Vault` 与 `c:\\vault` 会判成不同。
    所以走 `normcase` 归一化 + 分隔符边界。

    ★ 归一化用 `resolve()` 而非 `abspath()`（审查补的）：`abspath` **纯字符串拼接**，
      不解析符号链接 —— 若 `领域/金融投资` 是个指向 vault 外的 junction/symlink，
      `abspath` 看着仍在 vault 内，`resolve()` 才认得出它其实指向外面。
      Windows 的目录联接最常被拿来干这事，所以这道闸必须落在 `resolve()` 上。
    `resolve()` 对**还不存在**的路径也能工作（`strict=False`，默认），所以
    新建目录/新文件不用先创建就能校验。
    """
    try:
        r = os.path.normcase(os.path.realpath(str(root)))
        t = os.path.normcase(os.path.realpath(str(target)))
    except OSError:
        return False
    return t == r or t.startswith(r + os.sep)


def _normalize_dir(dir_rel: str) -> str:
    """目录相对路径规范化：统一分隔符、逐段清洗、去掉空段与首尾斜杠。"""
    d = (dir_rel or "").replace("\\", "/").strip("/")
    parts = [p for p in (catalog.sanitize_name(x) for x in d.split("/")) if p]
    return "/".join(parts)


def unique_path(target: Path) -> Path:
    """同名则加 `-2 / -3` …；到上限仍冲突就抛 `WriteError`。**绝不覆盖。**"""
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for i in range(2, MAX_DEDUP + 1):
        cand = target.with_name(f"{stem}-{i}{suffix}")
        if not cand.exists():
            return cand
    raise WriteError(f"同名文件太多（已到 -{MAX_DEDUP}）：{target.name}")


def _atomic_write(target: Path, text: str) -> None:
    """原子写：**同目录**临时文件 → `os.replace`。

    跨盘时系统临时目录与目标不在同一卷，`os.replace` 会抛
    `OSError: [Errno 18] Invalid cross-device link` —— 所以临时文件必须写在旁边。
    """
    fd = -1
    tmp = ""
    try:
        fd, tmp = tempfile.mkstemp(
            dir=str(target.parent), prefix=".tmp-", suffix=".md"
        )
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            fd = -1  # 已交给 with 管理，别再手动 close
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except (OSError, ValueError) as e:
        # ⚠️ **必须连 `ValueError` 一起接**（2026-10-06 审查抓到）：
        # `UnicodeEncodeError` 是它的子类，由模型返回的**孤立代理字符**触发
        # （`json.loads('"\ud800"')` 会原样产出孤立代理，而 `assemble` 又把它
        # 拼进正文）。原来只接 `OSError` → 异常一路逃到 worker，
        # **整条会话炸掉，而且磁盘上留下一个 `.tmp-xxxx.md` 残留**。
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise WriteError(f"写入失败：{e}") from e


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def write_note(
    vault: Path | str,
    dir_rel: str,
    name: str,
    meta: dict[str, Any],
    body: str,
    *,
    allow_new_dir: bool = False,
    overwrite_ok: bool = False,
) -> Path:
    """写一篇**新**笔记，返回最终绝对路径。

    - `dir_rel`：相对 vault 的落点目录（如 `领域/金融投资`）。**必须在 `领域/` 之下。**
    - `name`：笔记名（**不含 `.md`**）。会被清洗；清完为空则抛 `WriteError`。
    - `allow_new_dir=True`：允许创建**一级**新目录（只在用户点头后传它）。
    - `overwrite_ok=True`：允许覆盖已存在文件。
      ⚠️ 默认 `False` —— 全项目**没有任何地方**该传 `True`，留这个参数只为将来的"更新笔记"功能显式开启。

    失败一律 `WriteError`（含越界、白名单外、同名过多、IO 失败）。
    """
    root = Path(vault)
    if not root.is_dir():
        raise WriteError(f"知识库目录不存在：{root}")

    norm_dir = _normalize_dir(dir_rel)
    parts = norm_dir.split("/") if norm_dir else []
    if not parts or parts[0] != ALLOWED_ROOT:
        raise WriteError(
            f"落点必须在 {ALLOWED_ROOT}/ 之下（收到 {dir_rel!r}）"
        )

    cat = catalog.load(root)
    exists = catalog.is_domain_dir(cat, norm_dir)
    if not exists:
        if not allow_new_dir:
            raise WriteError(
                f"目录不存在且未获授权新建：{norm_dir}（已存在的领域：{cat.domain_dirs()}）"
            )
        if len(parts) != 2:
            # 只建一级：`领域/新主题` 可以，`领域/金融投资/子目录` 不行
            raise WriteError(
                f"只允许在 {ALLOWED_ROOT}/ 下建**一级**新目录（收到 {norm_dir}）"
            )

    safe = catalog.sanitize_name(name)
    if not safe:
        raise WriteError(f"笔记名清洗后为空：{name!r}")

    target = root / norm_dir / f"{safe}.md"

    # ★ 越界断言：所有路径拼装完成后、任何 mkdir/写盘之前
    if not within(root, target):
        raise WriteError(f"目标路径越出知识库范围：{target}")

    if not overwrite_ok:
        target = unique_path(target)

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise WriteError(f"创建目录失败：{e}") from e

    _atomic_write(target, frontmatter.render(meta, body))
    return target


def map_line(name: str) -> str:
    """给用户自己粘到地图页的那一行（我们不代劳，见 `docs/knowledge-design.md` §8.3）。"""
    return f"- [[{name}]]"
