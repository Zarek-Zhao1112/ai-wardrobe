"""裤长（`length`）维度的回归测试。

**背景**：用户 2026-10-20 反馈「西裤和长裤不能放一个品类，长裤还分几分裤」，
拍板「单独加一个长度字段」。品类只管"是什么"，长度只管"多长"。

**为什么单独开一个文件**：这个维度横跨四层 ——
词表（`taxonomy`）→ 模型（`models` 的入口归一）→ 入库（`items` 的类型闸门）
→ 打标（`tagging`）。分在原来那几个文件里，改一层时很容易只跑自己那部分。
"""

from __future__ import annotations

import pytest

from app.models import ItemCreate, ItemUpdate
from app.services import items as item_svc
from app.services import tagging


class TestEntryNormalization:
    """★ 入口归一：同一列**不能**混着三套口径。

    三个写入入口：AI 打标（key）、粘贴解析（key）、手工 API（可能写中文）。
    不归一的话，库里会同时有 `nine_tenth` / `九分` / `9分` / `随便写`，
    统计和筛选全废 —— 而且**不报错**（Bug 猎手 2026-10-20 实测）。
    """

    @pytest.mark.parametrize(
        ("raw", "want"),
        [
            ("nine_tenth", "nine_tenth"),
            ("九分", "nine_tenth"),
            ("9分", "nine_tenth"),
            ("九分裤", "nine_tenth"),
            ("全长", "full"),
            ("7分裤", "seven_tenth"),
        ],
    )
    def test_chinese_and_numeric_forms_normalize_to_key(self, raw, want):
        it = ItemCreate(type="运动裤", name="x", length=raw)
        assert it.length == want

    def test_unknown_value_is_dropped_not_stored(self):
        """认不出的**丢掉**（宁缺勿脏），不抛 422 —— 可选字段不该挡住录入。"""
        assert ItemCreate(type="运动裤", name="x", length="随便写").length is None

    def test_empty_string_becomes_none(self):
        assert ItemCreate(type="运动裤", name="x", length="").length is None

    def test_patch_none_stays_none(self):
        """`None` 必须保留成 `None`（= 清空），不能归一成别的值。"""
        assert ItemUpdate(length=None).length is None


class TestTypeGate:
    """★ 类型闸门：只有裤装能填裤长。

    「九分」「五分」这些词在别的品类上同样成立 ——
    「九分袖衬衫」「五分袖T恤」都含它们。没有这道闸门，
    衬衫会被写上 `length=nine_tenth`，详情页显示「裤长：九分」且**不报错**。
    （2026-10-20 Bug 猎手实测复现，是本轮最严重的一条。）
    """

    @pytest.mark.parametrize("type_key", ["牛仔裤", "西裤", "休闲裤", "运动裤", "打底裤"])
    def test_trousers_keep_length(self, uid, type_key):
        it = item_svc.create(uid, ItemCreate(type=type_key, name="裤子", length="九分"))
        assert it["length"] == "nine_tenth", f"{type_key} 应该能存裤长"

    @pytest.mark.parametrize("type_key", ["T恤", "衬衫", "连衣裙", "风衣", "短裤", "半身裙"])
    def test_non_trousers_cannot_keep_length(self, uid, type_key):
        it = item_svc.create(uid, ItemCreate(type=type_key, name="不是裤子", length="九分"))
        assert it["length"] is None, f"{type_key} 不该有裤长"

    def test_update_gate_uses_the_new_type(self, uid):
        """PATCH 同时改 type 和 length 时，闸门要用**更新后的** type。"""
        created = item_svc.create(uid, ItemCreate(type="运动裤", name="裤子"))
        item_id = created["id"]

        # 改成衬衫 → length 必须被丢掉
        item_svc.update(item_id, ItemUpdate(type="衬衫", length="九分"), uid)
        assert item_svc.get(item_id, uid)["length"] is None

    def test_update_keeps_existing_type(self, uid):
        """只改 length、不改 type 时，闸门要用**库里已有的** type。"""
        created = item_svc.create(uid, ItemCreate(type="牛仔裤", name="裤子"))
        item_id = created["id"]

        item_svc.update(item_id, ItemUpdate(length="七分"), uid)
        assert item_svc.get(item_id, uid)["length"] == "seven_tenth"


class TestTaggingNormalization:
    """打标这条路的长度归一。

    ⚠️ 踩过的坑：`normalize_tags` 原本只调 `_match(LENGTHS)`，
    它只认词表的 key/label，**不认 `LENGTH_ALIASES`** ——
    于是「9分」「7分裤」「全长」在打标通路上全部进 unresolved，白给。
    """

    @pytest.mark.parametrize(
        ("raw", "want"),
        [
            ("9分", "nine_tenth"),
            ("7分裤", "seven_tenth"),
            ("九分", "nine_tenth"),
            ("全长", "full"),
            ("nine_tenth", "nine_tenth"),
        ],
    )
    def test_aliases_are_recognized(self, raw, want):
        clean, failed = tagging.normalize_tags({"type": "运动裤", "length": raw})
        assert clean.get("length") == want, f"{raw} → {clean.get('length')}"
        assert "length" not in failed, f"{raw} 不该进 unresolved"

    def test_not_reported_for_upper_body(self):
        """非裤装：**丢掉且不报** —— "T恤有裤长"本来就不该提，报出来是噪声。"""
        clean, failed = tagging.normalize_tags({"type": "衬衫", "length": "九分"})
        assert "length" not in clean
        assert "length" not in failed

    def test_unknown_length_goes_to_unresolved_for_trousers(self):
        """裤装给了个认不出的长度 → 进 unresolved，让用户手选（宁缺勿脏）。"""
        clean, failed = tagging.normalize_tags({"type": "牛仔裤", "length": "到膝盖上面"})
        assert "length" not in clean
        assert failed.get("length") == "到膝盖上面"
