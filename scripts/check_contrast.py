"""对比度门禁：按 WCAG 2.1 检查 token 色板上的**实际文字组合**。

为什么要有它：对比度不足是**只有设计审查能抓到**的一类缺陷 ——
规格核对官看条款、Bug 猎手看逻辑，都不会去看"这两个色值放在一起能不能读"。
本项目的 `--warn` 曾经是 4.40:1（低于 AA 的 4.5），`--text-faint` 只有 3.62:1，
都是 W4 的设计审查官算出来的（肉眼完全看不出）。

用法：
    .venv/Scripts/python.exe scripts/check_contrast.py
退出码非 0 = 有组合不达标，别提交。
"""
import pathlib
import re
import sys

TOKENS = pathlib.Path(__file__).resolve().parent.parent / "web/css/tokens.css"

# 判据：正文（<24px 且非 ≥19px 粗体）≥4.5:1；大字 ≥3:1。
AA_BODY = 4.5

# 每个色对 = (前景 token, 背景 token, 说明)。**这里要跟着实际使用场景维护** ——
# 加了一个新色对（比如新的提示块配色）就该补一行。
PAIRS = [
    ("--text", "--surface", "正文 / 卡片"),
    ("--text", "--bg", "正文 / 页面底"),
    ("--text-dim", "--surface", "次级文字 / 卡片（.muted、表头、卡片 meta）"),
    ("--text-dim", "--bg", "次级文字 / 页面底"),
    ("--text-dim", "--surface-2", "次级文字 / 浅灰块（chips、code）"),
    ("--text-faint", "--surface", "三级文字 / 卡片（.faint、card-foot、status）"),
    ("--text-faint", "--bg", "三级文字 / 页面底"),
    ("--text-faint", "--surface-2", "三级文字 / 浅灰块（.card-media 占位字）"),
    ("--warn", "--warn-soft", "告警文字 / 告警底（.alert、.chip.warn）"),
    ("--warn", "--surface", "告警文字 / 卡片（.state-scanned）"),
    ("--danger", "--danger-soft", "错误文字 / 错误底（.alert.error）"),
    ("--danger", "--surface", "错误文字 / 卡片"),
    ("--on-accent", "--accent", "按钮上的白字 / 强调色"),
    ("--accent", "--surface", "链接与强调文字 / 卡片"),
    ("--accent", "--accent-soft", "强调文字 / 强调浅底（.tab.active、.chip.ok）"),
]


def lum(hexs: str) -> float:
    h = hexs.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))

    def f(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def ratio(fg: str, bg: str) -> float:
    a, b = lum(fg), lum(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def load_tokens() -> dict:
    text = TOKENS.read_text(encoding="utf-8")
    out = {}
    for name, value in re.findall(r"(--[\w-]+)\s*:\s*(#[0-9a-fA-F]{3,8})\s*;", text):
        out[name] = value
    return out


def main() -> int:
    tokens = load_tokens()

    # ── 自证：换算器本身必须先能判出"不达标" ──
    # 否则"全绿"可能只是因为公式写错了（比如把比值算成常数）。
    if ratio("#86868b", "#ffffff") >= AA_BODY:
        print("★ 自证失败：换算器把已知不达标的组合（#86868b on #fff ≈3.62）判成达标了")
        return 2
    print("自证：换算器能识别已知不达标组合（#86868b on #fff）="
          f"{ratio('#86868b', '#ffffff'):.2f} < {AA_BODY} ✓")
    print()

    bad = []
    for fg_tok, bg_tok, note in PAIRS:
        fg, bg = tokens.get(fg_tok), tokens.get(bg_tok)
        if not fg or not bg:
            print(f"?  {fg_tok} 或 {bg_tok} 在 tokens.css 里找不到，跳过")
            continue
        r = ratio(fg, bg)
        flag = "OK " if r >= AA_BODY else "FAIL"
        print(f"{flag} {r:5.2f}  {fg_tok}({fg}) on {bg_tok}({bg})  — {note}")
        if r < AA_BODY:
            bad.append((fg_tok, bg_tok, r, note))

    print()
    print(f"共查 {len(PAIRS)} 组，不达标 {len(bad)} 组。")
    for fg_tok, bg_tok, r, note in bad:
        print(f"  ✗ {r:.2f} < {AA_BODY}  {fg_tok} on {bg_tok}  — {note}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
