"""自动起名的回归测试（2026-10-20 用户拍板「C」）。

**解决什么**：纯图录入时没有商品文案，`name` 会一直是空的 ——
`view-wardrobe.js:42` 会退回显示品类名，于是衣橱里一排「T恤」分不清。

**边界**：只在用户**没填名字**时填；拼不出足够信息就不填（返回空串）。
"""

from __future__ import annotations

import pytest

from app.models import ItemCreate
from app.services import items as item_svc
from app.services.tagging import _try_auto_name


def _item(**kw) -> dict:
    """造一个 `items.get()` 形状的 dict（够 `auto_name` 用即可）。"""
    base = {"type": "tshirt", "type_label": "T恤", "primary_color": None,
            "colors": [], "material": None}
    base.update(kw)
    return base


class TestAutoName:
    def test_color_material_and_type(self):
        assert item_svc.auto_name(
            _item(primary_color="white", material="cotton")
        ) == "白色棉T恤"

    def test_color_only(self):
        assert item_svc.auto_name(_item(primary_color="black")) == "黑色T恤"

    def test_material_only(self):
        assert item_svc.auto_name(_item(material="wool")) == "羊毛T恤"

    def test_falls_back_to_first_color(self):
        """`primary_color` 空时用 `colors[0]` —— 打标有时只给 colors。"""
        assert item_svc.auto_name(_item(colors=["gray"])) == "灰色T恤"

    def test_unknown_color_is_ignored(self):
        """词表外的颜色值不该让整条名字作废 —— 材质照样要拼进去。"""
        assert item_svc.auto_name(
            _item(primary_color="__不存在__", material="cotton")
        ) == "棉T恤"

    def test_no_info_returns_empty(self):
        """只有品类名 → 返回空串，**不要**制造一个全是「T恤」的重复名字。"""
        assert item_svc.auto_name(_item()) == ""

    def test_other_type_returns_empty(self):
        assert item_svc.auto_name(_item(type="other", type_label="其他",
                                        primary_color="white")) == ""

    def test_missing_type_label_returns_empty(self):
        assert item_svc.auto_name(_item(type_label="")) == ""


class TestTryAutoName:
    """`_try_auto_name` 是打标后的填空动作，必须**绝不抛异常**。"""

    def test_fills_blank_name(self, uid):
        it = item_svc.create(uid, ItemCreate(
            type="T恤", primary_color="white", material="cotton"))
        assert it["name"] is None, "前置：创建时不该有名字"

        _try_auto_name(uid, it["id"])

        assert item_svc.get(it["id"], uid)["name"] == "白色棉T恤"

    def test_never_overwrites_user_name(self, uid):
        """用户自己起的名字是**他的词**，AI 不许改。"""
        it = item_svc.create(uid, ItemCreate(
            type="T恤", name="我的战袍", primary_color="white", material="cotton"))

        _try_auto_name(uid, it["id"])

        assert item_svc.get(it["id"], uid)["name"] == "我的战袍"

    def test_does_nothing_when_no_info(self, uid):
        it = item_svc.create(uid, ItemCreate(type="T恤"))

        _try_auto_name(uid, it["id"])

        assert item_svc.get(it["id"], uid)["name"] is None

    def test_missing_item_does_not_raise(self, uid):
        """不存在的 id 也要安静返回（不能把打标结果带崩）。"""
        _try_auto_name(uid, "it_不存在")
