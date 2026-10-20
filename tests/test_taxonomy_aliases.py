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
#
# ★ 2026-10-20 修订（用户反馈「西裤和长裤不能放一个品类」）：
#   原来版型词 / 长度词统一归 `pants`（那时叫"西裤/长裤"），
#   结果是「运动长裤」被判成西裤，而且一个选项同时回答"什么风格 + 多长"。
#   现在拆成三个维度，这张表的期望值跟着变：
#     · 长度（长裤 / 九分 / 七分）→ 由 `LENGTH_ALIASES` 单独识别（见 TestLengthDimension）
#     · 版型（直筒 / 阔腿 / 锥形 / 喇叭）→ 由 `FITS` 识别
#     · 品类 → 这些词在品类别名表里**只作兜底**，指向中性的 `casual_pants`
#   兜底必须保留：删掉就落 `other` → 槽位变 accessory → 又静默丢数据。
NEW_ALIASES = {
    # 裤（卫裤/束脚裤属运动裤）
    "卫裤": ("sweatpants", "bottom"),
    "束脚裤": ("sweatpants", "bottom"),
    # 版型词：只描述剪裁，不说明风格 → 中性归"休闲裤"（原来是 pants / 西裤）
    "直筒裤": ("casual_pants", "bottom"),
    "阔腿裤": ("casual_pants", "bottom"),
    "锥形裤": ("casual_pants", "bottom"),
    "烟管裤": ("casual_pants", "bottom"),
    "微喇裤": ("casual_pants", "bottom"),
    "喇叭裤": ("casual_pants", "bottom"),
    # 长度词：同上，只作兜底；真正的长度由 resolve_length 识别
    "九分裤": ("casual_pants", "bottom"),
    "七分裤": ("casual_pants", "bottom"),
    "棉裤": ("casual_pants", "bottom"),
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


class TestLengthDimension:
    """裤长是**独立维度**（2026-10-20 从品类里拆出来）。

    为什么必须锁：拆之前「西裤/长裤」一个选项同时回答"什么风格"和"多长"，
    于是「运动长裤」被判成西裤 —— 用户实测反馈的原话：
    「西裤和长裤我觉得品类也不能放一起，而且长裤也分几分裤」。

    现在的关系是正交的：
        type    = 它是什么（牛仔裤 / 西裤 / 休闲裤 / 运动裤 …）
        length  = 多长（长裤 / 九分 / 七分 / 五分 / 三分）
        fit     = 什么剪裁（直筒 / 阔腿 / 锥形 / 喇叭 …）
    """

    #: 用户 2026-10-20 粘的真实淘宝标题（逐字保留）
    REAL_TITLE = (
        "维动更迭速干运动长裤男秋季新款冰丝梭织篮球跑步训练直筒九分裤"
    )

    def test_real_title_is_sweatpants_not_pants(self):
        """★ 核心回归：这是一条**运动长裤**，不是西裤。

        拆之前它会命中「长裤」（`pants` 的别名，比「运动裤」更早出现）
        → 判成西裤。现在「运动长裤」是最长命中 → sweatpants。
        """
        assert T.resolve_type(self.REAL_TITLE) == "sweatpants"
        assert T.slot_of(T.resolve_type(self.REAL_TITLE)) == "bottom"

    def test_real_title_length_is_nine_tenth(self):
        """同一条标题里「九分裤」要能单独被认出来，而且**不影响品类**。"""
        assert T.resolve_length(self.REAL_TITLE) == "nine_tenth"

    @pytest.mark.parametrize(
        ("text", "want"),
        [
            ("长裤", "full"),
            ("全长", "full"),
            ("九分裤", "nine_tenth"),
            ("九分", "nine_tenth"),
            ("七分裤", "seven_tenth"),
            ("五分裤", "five_tenth"),
            ("三分裤", "three_tenth"),
            ("9分裤", "nine_tenth"),
        ],
    )
    def test_length_aliases(self, text, want):
        assert T.resolve_length(text) == want

    def test_length_is_none_when_not_mentioned(self):
        """没提长度就是 `None`，**不许猜** —— 猜出来的长度比空着更糟。"""
        assert T.resolve_length("西裤") is None
        assert T.resolve_length("纯棉T恤") is None
        assert T.resolve_length("") is None

    def test_length_does_not_steal_the_type(self):
        """⚠️ 长度词**不能**参与品类识别，否则「九分运动裤」会被判成休闲裤。

        这正是把长度词从 `TYPE_ALIASES` 移出去的原因。
        """
        assert T.resolve_type("九分运动裤") == "sweatpants"
        assert T.resolve_length("九分运动裤") == "nine_tenth"

    def test_formal_trousers_stay_pants(self):
        """「西裤」是真正的品类，不能被长度维度吃掉。"""
        assert T.resolve_type("西裤") == "pants"

    def test_length_is_exposed_to_frontend(self):
        """长度词表必须出现在 `/taxonomy` 的 `vocabularies` 里，否则前端下拉是空的。"""
        assert "length" in T.VOCABULARIES
        assert T.VOCABULARIES["length"] == T.LENGTHS
        assert set(T.LENGTHS) == {
            "full", "nine_tenth", "seven_tenth", "five_tenth", "three_tenth"
        }

    def test_trouser_aliases_never_land_in_accessory(self):
        """兜底守卫：版型词/长度词即使只走品类，也不能掉成配饰（会掉出组合计算）。"""
        for word in ("直筒裤", "阔腿裤", "锥形裤", "喇叭裤", "九分裤", "七分裤", "长裤"):
            key = T.resolve_type(word)
            assert key != "other", f"{word} 落 other 了，槽位会变 accessory"
            assert T.slot_of(key) == "bottom", f"{word} → {key}，槽位不是下装"

    def test_fit_vocabulary_covers_trouser_shapes(self):
        """版型词要能在 `fit` 词表里被认出来（它们是剪裁，不是品类）。"""
        assert T.FITS["wide_leg"] == "阔腿"
        assert T.FITS["tapered"] == "锥形"
        assert T.FITS["flared"] == "喇叭"
        assert T.FITS["straight"] == "直筒"

    def test_suit_trousers_are_not_blazers(self):
        """★「西装裤」不能判成**西装外套**（槽位会从下装掉到外套）。

        `TYPE_ALIASES` 里原本只有「西装」→ `blazer`，而「西裤」要求两个字
        连续才命中 —— 于是「西装裤」被「西装」抢走，槽位变成 `outer`，
        一条裤子从上装×下装×鞋的组合里静默消失。
        （2026-10-20 Bug 猎手实测：西装裤 / 西装短裤 / 九分西装裤 全部判成 blazer）
        """
        for word in ("西装裤", "西服裤", "九分西装裤", "西装短裤"):
            key = T.resolve_type(word)
            assert key != "blazer", f"{word} 被判成西装外套了"
            assert T.slot_of(key) == "bottom", f"{word} → {key}，槽位不是下装"

    def test_other_trouser_words_do_not_fall_to_accessory(self):
        """同批补的「X 裤」：以前全落 `other`（= 配饰，掉出组合）。"""
        for word in ("冲锋裤", "皮裤", "校服裤", "灯芯绒裤", "瑜伽裤", "沙滩裤", "热裤"):
            key = T.resolve_type(word)
            assert key != "other", f"{word} 落 other 了"
            assert T.slot_of(key) == "bottom", f"{word} → {key}"

    def test_supports_length_only_for_trousers(self):
        """类型闸门：只有裤装该有裤长。"""
        for k in ("jeans", "pants", "casual_pants", "sweatpants", "leggings"):
            assert T.supports_length(k), f"{k} 应该能填裤长"
        for k in ("tshirt", "shirt", "dress", "shorts", "skirt", "coat", "other", None, ""):
            assert not T.supports_length(k), f"{k} 不该有裤长"
