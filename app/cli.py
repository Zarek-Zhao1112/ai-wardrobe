"""命令行工具：造种子数据 + 批量导入。

用法（在项目根目录执行）：

    python -m app.cli seed --count 20
    python -m app.cli import items.json

## 为什么需要这个文件

P1 阶段微信（P3）和本地网页（P2）**都还没做**，单品只能靠 `/docs` 一条条手调。
而缺口分析在**少于 20 件时没有意义** —— 三件衣服算不出「你缺哪类下装」。
所以必须有个"一次喂一批"的入口。

放在 `app/` 而不是 `scripts/`：它要 `import app.services`，放包内能用相对导入，
不用额外处理 `sys.path`（`run.py` 就是这么干的，多一个脚本就多一处重复）。

## `seed` 造的不是好看的数据，是**结构合理**的数据

覆盖各个槽位、颜色尽量分散 —— 这样缺口分析、组合数、多样性折扣、
推荐排序**都有东西可算**。随手造 20 件白 T 恤是验证不了任何东西的。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

from . import db
from .models import ItemCreate
from .services import items as item_svc

#: 种子计划：`(中文名, 主色, 季节)`。
#:
#: - 用**中文名**而不是 type key：让 `taxonomy.resolve_type` 去解析，
#:   顺带把别名表也测了一遍（真实用户输入的就是中文）
#: - 季节留 `None` 表示四季通用（不写 `season` 字段）
SEED_PLAN: list[tuple[str, str, Optional[str]]] = [
    ("白色T恤", "white", "summer"),
    ("黑色T恤", "black", "summer"),
    ("浅蓝衬衫", "light_blue", "spring"),
    ("格纹衬衫", "multi", "autumn"),
    ("灰色卫衣", "gray", "autumn"),
    ("米白针织衫", "beige", "winter"),
    ("深蓝牛仔裤", "navy", None),
    ("黑色西裤", "black", None),
    ("卡其休闲裤", "khaki", "spring"),
    ("灰色运动裤", "gray", None),
    ("黑色短裤", "black", "summer"),
    ("藏蓝连衣裙", "navy", "summer"),
    ("白色运动鞋", "white", None),
    ("黑色皮鞋", "black", None),
    ("棕色靴子", "brown", "winter"),
    ("军绿夹克", "army_green", "autumn"),
    ("黑色大衣", "black", "winter"),
    ("藏蓝西装外套", "navy", None),
    ("棕色托特包", "brown", None),
    ("黑色棒球帽", "black", None),
]


def _seed_plan(count: int) -> list[dict[str, Any]]:
    """按需循环 SEED_PLAN，超出部分给名字加序号（避免名字完全一样难以分辨）。"""
    out: list[dict[str, Any]] = []
    round_no = 0
    while len(out) < count:
        for name, color, season in SEED_PLAN:
            if len(out) >= count:
                break
            suffix = f" {round_no + 1}" if round_no else ""
            item: dict[str, Any] = {
                "type": name,
                "name": f"{name}{suffix}",
                "primary_color": color,
                "colors": [color],
            }
            if season:
                item["season"] = [season]
            out.append(item)
        round_no += 1
    return out


def _report(result: dict, *, verbose: bool = True) -> None:
    print(
        f"共 {result['total']} 件：成功 {result['created']}"
        f"，跳过 {result['skipped']}，失败 {result['failed']}"
    )
    if not verbose:
        return
    for outcome in result["outcomes"]:
        if outcome["status"] == "created":
            continue
        # 只打印没成功的 —— 成功的打印出来会淹掉真正需要看的那几行
        print(f"  [{outcome['status']}] 第 {outcome['index']} 件 {outcome['name']}：{outcome['reason']}")


def cmd_seed(args: argparse.Namespace) -> int:
    user_id = db.get_default_user_id()
    payloads = [ItemCreate(**item) for item in _seed_plan(args.count)]
    result = item_svc.create_many(user_id, payloads)
    _report(result)

    from .services import wardrobe

    stats = wardrobe.total_combinations(user_id)
    print(
        f"当前（{stats['season_label']}）可搭出 {stats['total_combinations']} 套，"
        f"上装 {stats['counts']['top']} / 下装 {stats['counts']['bottom']} / "
        f"连身 {stats['counts']['full']} / 鞋 {stats['counts']['shoes']}"
    )
    return 0 if result["failed"] == 0 else 1


def cmd_import(args: argparse.Namespace) -> int:
    path = Path(args.file)
    if not path.is_file():
        print(f"找不到文件：{path}", file=sys.stderr)
        return 2

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"JSON 解析失败：{exc}", file=sys.stderr)
        return 2

    raw_items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(raw_items, list) or not raw_items:
        print("文件里没有 items 数组（或为空）", file=sys.stderr)
        return 2

    try:
        payloads = [ItemCreate(**item) for item in raw_items]
    except Exception as exc:  # noqa: BLE001 —— 报清楚是哪条数据不合法
        print(f"数据校验失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    user_id = db.get_default_user_id()
    result = item_svc.create_many(user_id, payloads)
    _report(result)

    from .services import wardrobe

    stats = wardrobe.total_combinations(user_id)
    print(f"当前（{stats['season_label']}）可搭出 {stats['total_combinations']} 套")
    return 0 if result["failed"] == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="AI 穿搭助手的运维命令",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    seed = sub.add_parser("seed", help="造一批结构合理的种子单品")
    seed.add_argument("--count", type=int, default=20, help="要造多少件（默认 20）")
    seed.set_defaults(func=cmd_seed)

    imp = sub.add_parser("import", help="从 JSON 文件批量导入单品")
    imp.add_argument("file", help="JSON 文件路径；可以是数组，也可以是 {\"items\": [...]} ")
    imp.set_defaults(func=cmd_import)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    db.init(verbose=False)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
