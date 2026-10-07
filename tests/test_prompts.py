"""`AliasMap` 的单元测试。

它是纯字符串处理，不碰库也不碰网络，所以这里的断言是完全确定的 ——
正因为确定，格式一旦被改坏就必须在这里红掉（紧凑行格式是 W0 冻结的契约，
`recommend` 和模型两侧都依赖它）。
"""

from __future__ import annotations

from app import taxonomy
from app.services.llm.prompts import AliasMap


def _item(item_id: str, **overrides) -> dict:
    item = {
        "id": item_id,
        "slot": "top",
        "primary_color": "black",
        "material": "cotton",
        "fit": "loose",
        "formality": "casual",
        "season": ["summer", "autumn"],
    }
    item.update(overrides)
    return item


class TestBuild:
    def test_aliases_start_at_a1_and_follow_input_order(self):
        am = AliasMap()
        lines = am.build([_item("it_1"), _item("it_2"), _item("it_3")]).splitlines()
        assert [line.split("|")[0] for line in lines] == ["A1", "A2", "A3"]
        assert am.aliases() == ["A1", "A2", "A3"]

    def test_row_format_is_the_frozen_seven_cells(self):
        """格式：别名|槽位|主色|材质|版型|正式度|季节。改它就是改契约。"""
        cells = AliasMap().build([_item("it_1")]).split("|")
        assert cells == [
            "A1",
            taxonomy.SLOTS["top"],
            taxonomy.COLORS["black"],
            taxonomy.MATERIALS["cotton"],
            taxonomy.FITS["loose"],
            taxonomy.FORMALITIES["casual"],
            f'{taxonomy.SEASONS["summer"]},{taxonomy.SEASONS["autumn"]}',
        ]

    def test_missing_fields_stay_empty_not_placeholder(self):
        """缺失留空，不填"未知" —— 那会白占 token。"""
        assert AliasMap().build([{"id": "it_1", "slot": "bottom"}]) == "A1|下装|||||"

    def test_value_outside_vocab_is_echoed_raw(self):
        """词表里没有的值原样输出。让模型看到真实信息，好过看到"未知"。"""
        assert AliasMap().build([_item("it_1", primary_color="mystery")]).split("|")[2] == "mystery"

    def test_empty_list_returns_empty_string(self):
        assert AliasMap().build([]) == ""

    def test_items_without_id_are_skipped_without_gaps(self):
        am = AliasMap()
        text = am.build([_item("it_1"), {"slot": "top"}, _item("it_2")])
        assert len(text.splitlines()) == 2
        assert am.aliases() == ["A1", "A2"]

    def test_repeated_build_accumulates(self):
        """契约：同一实例重复调用会累积；要重新开始就新建实例。"""
        am = AliasMap()
        am.build([_item("it_1")])
        am.build([_item("it_2")])
        assert am.aliases() == ["A1", "A2"]


class TestResolve:
    def test_roundtrip(self):
        am = AliasMap()
        am.build([_item("it_abc")])
        assert am.resolve("A1") == "it_abc"
        assert am.alias_of("it_abc") == "A1"

    def test_unknown_alias_returns_none_instead_of_raising(self):
        """幻觉别名必须返回 None。一个坏别名不该让整套搭配作废。"""
        am = AliasMap()
        am.build([_item("it_1")])
        assert am.resolve("A99") is None
        assert am.resolve("") is None
        assert am.resolve(None) is None

    def test_case_and_whitespace_are_tolerated(self):
        """模型有时返回 `a1` 或 ` A1 `。这种宽容是安全的 ——
        它不可能把 A1 映射成 A2。"""
        am = AliasMap()
        am.build([_item("it_1")])
        assert am.resolve("a1") == "it_1"
        assert am.resolve(" A1 ") == "it_1"

    def test_resolve_many_splits_valid_and_bad(self):
        am = AliasMap()
        am.build([_item("it_1"), _item("it_2")])
        ok, bad = am.resolve_many(["A1", "A9", "A2", "A1"])
        assert ok == ["it_1", "it_2"], "重复别名只应解析一次"
        assert bad == ["A9"]

    def test_alias_of_unknown_returns_none(self):
        assert AliasMap().alias_of("nope") is None
        assert AliasMap().alias_of("") is None
