"""**说什么**：把衣橱裁剪成紧凑行，并做短别名映射。

解决两个具体问题（设计文档 §10）：

1. **prompt 长度**：几百件衣服不可能全塞进去。所以进来的 `items` 应该**已经**
   被调用方（`recommend`）用 SQL 硬过滤 + 本地打分筛过，只剩 K≈25 件。
   这一层**不做筛选** —— 选哪些是业务决策，不是文本组装。

2. **省 token + 降抄错率**：不塞真实 `item_id`（`it_45375f2663fc44fe` 这种 19 字符），
   而是分配短别名 `A1..A30`。模型返回别名，我们再映射回真 id。
   模型抄错 `A7`/`A9` 的概率，远低于抄错一长串十六进制。

---

✅ **已完成**（W2）。契约是 W0 冻结的，实现见下。
这一层是纯字符串处理，**不碰网络、不碰数据库**，所以它的测试是完全确定的。
"""

from __future__ import annotations

from typing import Any, Iterable

from ... import taxonomy


def _label(table: dict[str, str], value: Any) -> str:
    """取受控词表的中文标签。查不到就原样返回 —— 让模型看到原始值，
    总好过看到"未知"两个字白占 token。"""
    if value is None or value == "":
        return ""
    text = str(value)
    return table.get(text, text)


class AliasMap:
    """一次调用的别名 ↔ 真实 item_id 映射。**不要跨请求复用**。

    紧凑行格式（W0 冻结，W5 与实现对齐）：

        A1|上装|黑|棉|宽松|休闲|夏,秋

    依次是：别名 | **槽位中文标签** | 主色 | 材质 | 版型 | 正式度 | 季节。
    缺失的字段留空（`A3|下装|蓝|||休闲|`），**不要填"未知"** —— 那会白占 token。

    ⚠️ 槽位用**中文标签**（`上装`）而不是 key（`top`）：整行都是中文时模型更不容易读串，
    也和后面几列的口径一致。原契约写的是 `top`、实现给的是中文 ——
    W4 规格核对发现这处不一致，裁决为**保留实现、改契约**。

    为什么不带 `item_id`：那是上面说的第 2 点，省 token 且降低抄错率。
    """

    def __init__(self) -> None:
        self._to_id: dict[str, str] = {}
        self._to_alias: dict[str, str] = {}

    # -- 组装 ---------------------------------------------------------------

    def _row(self, alias: str, item: dict[str, Any]) -> str:
        seasons = item.get("season") or []
        season_text = ",".join(
            t for t in (_label(taxonomy.SEASONS, s) for s in seasons) if t
        )
        cells = [
            alias,
            _label(taxonomy.SLOTS, item.get("slot")),
            _label(taxonomy.COLORS, item.get("primary_color")),
            _label(taxonomy.MATERIALS, item.get("material")),
            _label(taxonomy.FITS, item.get("fit")),
            _label(taxonomy.FORMALITIES, item.get("formality")),
            season_text,
        ]
        return "|".join(cells)

    def build(self, items: list[dict[str, Any]]) -> str:
        """给这批单品分配别名，返回可直接嵌进 prompt 的多行文本。

        契约：
        - 别名从 `A1` 起递增，顺序与传入顺序一致（方便排查）
        - 超过 99 件也没关系（`A100`），但调用方本来就不该传这么多
        - 同一实例重复调用会**累积**映射；要重新开始就新建一个实例
        - 传入空列表 → 返回空串（调用方据此判断"没有候选"）
        """
        lines: list[str] = []
        for item in items:
            item_id = item.get("id")
            if not item_id:
                continue
            alias = f"A{len(self._to_id) + 1}"
            self._to_id[alias] = str(item_id)
            self._to_alias[str(item_id)] = alias
            lines.append(self._row(alias, item))
        return "\n".join(lines)

    # -- 解析 ---------------------------------------------------------------

    def resolve(self, alias: str) -> str | None:
        """别名 → 真实 `item_id`。**未知别名返回 `None`。**

        ⚠️ 调用方**必须**处理 `None`：那说明模型编了一个不存在的别名（幻觉）。
        不要在这里抛异常，因为一个坏别名不该让整套搭配作废 —— 丢弃它、继续处理其余的。

        大小写与空白会被容错（模型有时返回 `a1` 或 ` A1 `）——
        这种程度的宽容是安全的，因为它不可能把 A1 映射成 A2。
        """
        if not alias:
            return None
        return self._to_id.get(str(alias).strip().upper())

    def resolve_many(self, aliases: Iterable[str]) -> tuple[list[str], list[str]]:
        """批量解析。返回 `(有效的真实 id 列表, 无法解析的别名列表)`。

        这是防幻觉的第一层：`recommend` 拿到 id 后还要去数据库反查归属（第二层），
        而结构性根治是"只让模型在这些别名里挑"（第三层）。
        """
        ok: list[str] = []
        bad: list[str] = []
        for alias in aliases:
            resolved = self.resolve(alias)
            if resolved is None:
                bad.append(alias)
            elif resolved not in ok:
                ok.append(resolved)
        return ok, bad

    def aliases(self) -> list[str]:
        """当前已分配的别名（原顺序）。"""
        return list(self._to_id.keys())

    def alias_of(self, item_id: str) -> str | None:
        """反向查：真实 id → 别名。"""
        if not item_id:
            return None
        return self._to_alias.get(str(item_id))
