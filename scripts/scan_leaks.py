"""发布前闸门：**只扫 git 将要提交的那些文件**（`git ls-files`）。

为什么不能扫"整个目录"：整目录里混着已忽略的文件（`.env` / `data/` / 内部文档），
噪声会让人误判。真正要保证的是「**进仓库的那一份**是干净的」。

## 两类规则

1. **密钥形态**（内置，高置信）→ 命中即**失败**（退出码 1）。
2. **个人 needle**（外置，放在 `scripts/leak_needles.local.txt`，该文件已 gitignore）
   → 命中只**警告**，不失败。因为这类词（本机路径、真实姓名）在 `LICENSE` 等
   地方出现是正常的，需要人判断。

把个人 needle 外置的原因：这份脚本要**进公开仓库**，不能把真实姓名 / 本机路径
硬编码在里面。

## 自证

"全绿"可能是假绿 —— 所以先拿**内置的假样本**验证扫描器真的能命中，再报告结果。
（内置样本用字符串拼接生成，避免脚本自己被自己扫出命中。）

用法：
    python scripts/scan_leaks.py
    python scripts/scan_leaks.py --needles path/to/other.txt
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 高置信密钥形态：(正则, 名称)。命中即失败。
SECRET_PATTERNS: list[tuple[str, str]] = [
    (r"gh[pous]_[A-Za-z0-9]{20,}", "GitHub token"),
    (r"sk-[A-Za-z0-9]{16,}", "API key (sk-)"),
    (r"AKIA[0-9A-Z]{16}", "AWS access key id"),
    (r"xox[baprs]-[A-Za-z0-9-]{10,}", "Slack token"),
    (r"BEGIN [A-Z ]*PRIVATE KEY", "私钥"),
]
SECRET_RE = [(re.compile(p), name) for p, name in SECRET_PATTERNS]

#: 个人 needle 默认位置（已 gitignore）
DEFAULT_NEEDLES = ROOT / "scripts" / "leak_needles.local.txt"

#: 自证样本：**拼接**生成，源码里不出现完整密钥形态
SELF_TEST_SAMPLE = "a=" + "sk-" + "A" * 24 + " b=" + "ghp_" + "0" * 24


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return [ln for ln in (out.stdout or "").splitlines() if ln.strip()]


def load_needles(path: Path) -> list[str]:
    """读出个人 needle。

    ⚠️ **文件里用正斜杠写路径** —— 加载时自动补一个反斜杠变体。这样 needle 文件
    本身不含反斜杠，写文件时不会被转义悄悄搞坏（`X:\\resume` 里的 `\\r` 会变回车）。
    """
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8", errors="ignore")
    out: list[str] = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or ln.lstrip().startswith("#"):
            continue
        out.append(ln)
        if "/" in ln:
            out.append(ln.replace("/", "\\"))
    return out


def scan_text(text: str, needles: list[str]) -> tuple[list, list]:
    """返回 (密钥命中, 个人 needle 命中)，每项 = (行号, 名称, 片段)。"""
    secrets, personals = [], []
    for lineno, line in enumerate(text.splitlines(), 1):
        for rx, name in SECRET_RE:
            if rx.search(line):
                secrets.append((lineno, name, line.strip()[:90]))
        for needle in needles:
            if needle in line:
                personals.append((lineno, needle, line.strip()[:90]))
    return secrets, personals


def self_test() -> bool:
    secrets, _ = scan_text(SELF_TEST_SAMPLE, [])
    return len(secrets) >= 2


def main() -> int:
    ap = argparse.ArgumentParser(description="扫描 git 跟踪文件里的密钥 / 个人 needle")
    ap.add_argument("--needles", type=Path, default=DEFAULT_NEEDLES,
                    help=f"个人 needle 文件（默认 {DEFAULT_NEEDLES}）")
    args = ap.parse_args()

    if not self_test():
        print("[X] 自证失败：扫描器扫不出内置假样本，结论不可信", file=sys.stderr)
        return 2
    print("自证：扫描器能找到内置假样本 = True")

    needles = load_needles(args.needles)
    print(f"个人 needle：{len(needles)} 条（来自 {args.needles.name}）"
          + ("" if needles else "  ← 文件不存在或为空，只跑内置密钥扫描"))

    files = tracked_files()
    print(f"git 将提交 {len(files)} 个文件")
    print()

    secret_hits, personal_hits = [], []
    for rel in files:
        p = ROOT / rel
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        s, n = scan_text(text, needles)
        secret_hits += [(rel, *h) for h in s]
        personal_hits += [(rel, *h) for h in n]

    print("[密钥] 命中：")
    if not secret_hits:
        print("  （无）")
    for rel, lineno, name, snippet in secret_hits:
        print(f"  {rel}:{lineno}  [{name}]  {snippet}")

    print()
    print("[个人 needle] 命中（需人工判断是否可接受）：")
    if not personal_hits:
        print("  （无）")
    for rel, lineno, needle, snippet in personal_hits:
        print(f"  {rel}:{lineno}  [{needle}]  {snippet}")

    print()
    if secret_hits:
        print(f"结论：发现 {len(secret_hits)} 条密钥命中，**不要发布**。")
        return 1
    print("结论：无密钥命中，可以发布。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
