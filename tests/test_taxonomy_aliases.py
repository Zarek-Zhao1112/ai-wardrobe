"""W0 词表缺口回归测试（2026-10-08）。

**背景**：为「粘贴商品文案 → 自动识别品类」这条新链路做前置体检时，
枚举 41 个常见口语品类词，发现 **21 个落 `other`**。

落 `other` 的真实后果不是"显示成其他"，而是**静默丢数据**：
`other` 的槽位是 `accessory` → 一条裤子被算成配饰 →
它不会出现在任何"上装 × 下装 × 鞋"的组合里，组合数与缺口分析一起错。

所以这组测试**断言的是槽位（slot）**，不只是 key —— key 对了但槽位错，
一样是静默失效。

⚠️ 这是**枚举 + 实测**得出的清单，不是照着"应该有的词"拍的。
下次再发现缺口，请同样先枚举再补，并在这里加一条。
"""

from __future__ import annotations

import pytest

from app import taxonomy as T

# 21 个补进去的别名 -> 期望的 (type_key, slot)
# ⚠️ slot 显式写出来，而不是断言"非 accessory" —— 围脖/皮带**本来就是**配饰。
# 写死真实槽位才能抓到"key 选错了但没落 other"这种错。
NEW_ALIASES = {
    # 裤（版型词统一归长裤；卫裤/束脚裤属运动裤）
    "卫裤": ("sweatpants", "bottom"),
    "束脚裤": ("sweatpants", "bottom"),
    "直筒裤": ("pants", "bottom"),
    "阔腿裤": ("pants", "bottom"),
    "锥形裤": ("pants", "bottom"),
    "烟管裤": ("pants", "bottom"),
    "微喇裤": ("pants", "bottom"),
    "喇叭裤": ("pants", "bottom"),
    "九分裤": ("pants", "bottom"),
    "七分裤": ("pants", "bottom"),
    "棉裤": ("pants", "bottom"),
    # 裙
    "包臀裙": ("skirt", "bottom"),
    "a字裙": ("skirt", "bottom"),
    "百褶裙": ("skirt", "bottom"),
    "伞裙": ("skirt", "bottom"),
    # 外套
    "冲锋衣": ("jacket", "outer"),
    "皮衣": ("jacket", "outer"),
    "棉袄": ("parka", "outer"),
    # 配饰（槽位本来就是 accessory —— 它们**不**参与组合计算，这是对的）
    "马夹": ("vest", "top"),
    "围脖": ("scarf", "accessory"),
    "皮带": ("belt", "accessory"),
}


class TestNewAliases:
    def test_all_21_resolve_to_expected_key(self):
        wrong = {
            w: T.resolve_type(w)
            for w, (want, _slot) in NEW_ALIASES.items()
            if T.resolve_type(w) != want
        }
        assert not wrong, f"这些词映射错了：{wrong}"

    def test_none_falls_back_to_other(self):
        """补丁存在的理由：这 21 个词以前**全部**落 other。"""
        still_other = [w for w in NEW_ALIASES if T.resolve_type(w) == "other"]
        assert not still_other, f"仍落 other：{still_other}"

    @pytest.mark.parametrize("word", sorted(NEW_ALIASES))
    def test_slot_matches_real_garment(self, word):
        """★ 真正会坏的地方：落 other 时槽位是 accessory。

        一条裤子被判成配饰，就从「上装 × 下装 × 鞋」的组合里**静默消失** ——
        不报错，只是组合数少算了。所以槽位必须逐个核对，不能只看 key。
        """
        want_key, want_slot = NEW_ALIASES[word]
        key = T.resolve_type(word)
        assert key == want_key
        assert T.slot_of(key) == want_slot, f"{word} 槽位应为 {want_slot}"

    def test_trousers_never_land_in_accessory(self):
        """下装类的专项守卫：它们必须能进组合计算。"""
        for word, (key, _slot) in NEW_ALIASES.items():
            if key in ("pants", "sweatpants", "skirt"):
                assert T.slot_of(T.resolve_type(word)) == "bottom", word


class TestRealWorldCases:
    """用真实商品标题里出现过的片段，防止"补了别名但真实场景仍不通"。"""

    @pytest.mark.parametrize(
        ("title", "want_key", "want_slot"),
        [
            # 2026-10-07 用户给的真实淘宝标题（第 3 条，原先认不出）
            ("CatandMouse猫与小鼠25AW重磅400G7A炽芯索罗娜磨毛直筒宽松卫裤", "sweatpants", "bottom"),
            # 同一批的第 1 条（本来就认得出，防回归）
            ('ALLFIT "2021M"原色黑牛仔裤 不褪色 宽松直筒微喇叭百搭牛仔裤男', "jeans", "bottom"),
            # 第 2 条：标题里是"裤腰带"，靠包含匹配命中；单独说"皮带"也要通
            ("皮带", "belt", "accessory"),
        ],
    )
    def test_real_title_fragments(self, title, want_key, want_slot):
        key = T.resolve_type(title)
        assert key == want_key
        assert T.slot_of(key) == want_slot


class TestNoOverBroadening:
    """补别名不能把"真的不认识"也说成认识 —— 否则会给库塞错品类。"""

    @pytest.mark.parametrize("word", ["法拉利", "螺蛳粉", "充电宝", "asdfgh"])
    def test_still_unknown(self, word):
        assert T.resolve_type(word) == "other"

    def test_bag_does_not_match_bread(self):
        """历史坑：单字"包"会命中"面包"，所以包含匹配设了最小词长。

        补别名时若把词加太短，这个保护会被绕过 —— 这条锁住它。
        """
        assert T.resolve_type("面包") == "other"


class TestExistingAliasesStillWork:
    """补丁不该动到原有词条。"""

    @pytest.mark.parametrize(
        ("word", "want"),
        [
            ("运动裤", "sweatpants"),
            ("休闲裤", "casual_pants"),
            ("工装裤", "casual_pants"),
            ("牛仔裤", "jeans"),
            ("裤子", "pants"),
            ("卫衣", "hoodie"),
            ("马甲", "vest"),
            ("腰带", "belt"),
            ("围巾", "scarf"),
            ("棉服", "parka"),
            ("风衣", "trench"),
            ("小白鞋", "sneakers"),
        ],
    )
    def test_unchanged(self, word, want):
        assert T.resolve_type(word) == want
