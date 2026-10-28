"""品类受控词表与槽位定义 —— 整个系统的地基。

设计依据（来自 wardrowbe 源码的实证 + 我们自己的修正）：
- wardrowbe 的 `type` 是无约束字符串、零校验，导致它的 analytics 只能硬编码白名单
  （["shirt","blouse","t-shirt","top"] / ["pants","jeans","skirt","shorts"]），
  sweater/hoodie/jacket/dress 全部漏判。本模块就是修掉这个问题。
- wardrowbe 的实际 type 词域只有 17 项（从其 DEFAULT_WASH_INTERVALS 间接得出），
  且偏西方场景，中文场景（polo/开衫/羽绒服/风衣/连体裤/家居服…）大面积缺失。
- wardrowbe 把 dress 当成独立 type，而不是「上装+下装」。我们沿用这个决定，
  但引入 slot="full" 让组合数公式能同时处理「上下分体」和「连身」两类。

槽位（slot）的用途：只有槽位化之后，「组合数 = (T*B + F) * S」才可计算，
衣服缺口分析（第 9 节：判断用户缺的是哪一类下装，而不是"缺衣服"）才有输入。
"""

from __future__ import annotations

from datetime import date
from typing import Final, Optional

# ---------------------------------------------------------------------------
# 槽位：一件衣服在搭配里占哪个位置
# ---------------------------------------------------------------------------
# top      上装    ── 基础层之一
# bottom   下装    ── 基础层之一
# full     连身    ── 连衣裙/连体裤/套装，单独构成一身，与 top+bottom 互斥
# outer    外套    ── 可选层（穿/不穿都成立）
# shoes    鞋      ── 基础层之一
# accessory 配饰   ── 可选层，不参与组合数计算（否则组合数爆炸）
SLOTS: Final[dict[str, str]] = {
    "top": "上装",
    "bottom": "下装",
    "full": "连身",
    "outer": "外套",
    "shoes": "鞋",
    "accessory": "配饰",
}

# 参与组合数计算的基础槽位
BASE_SLOTS: Final[tuple[str, ...]] = ("top", "bottom", "full", "shoes")
# 可选槽位（穿/不穿都成立，只做加成不参与基础组合）
OPTIONAL_SLOTS: Final[tuple[str, ...]] = ("outer", "accessory")


# ---------------------------------------------------------------------------
# 受控品类词表：type_key -> (中文名, 所属槽位)
# ---------------------------------------------------------------------------
CLOTHING_TYPES: Final[dict[str, tuple[str, str]]] = {
    # ---- 上装 ----
    "tshirt":       ("T恤",         "top"),
    "shirt":        ("衬衫",         "top"),
    "polo":         ("Polo衫",      "top"),
    "blouse":       ("雪纺衫",       "top"),
    "sweater":      ("毛衣/针织衫",   "top"),
    "cardigan":     ("开衫",         "top"),
    "hoodie":       ("卫衣",         "top"),
    "tank":         ("背心/吊带",     "top"),
    "vest":         ("马甲",         "top"),
    "sweatshirt":   ("长袖T恤",      "top"),
    "base_layer":   ("打底衫/秋衣",   "top"),
    # ---- 下装 ----
    "jeans":        ("牛仔裤",       "bottom"),
    "pants":        ("西裤",         "bottom"),
    "casual_pants": ("休闲裤",       "bottom"),
    "sweatpants":   ("运动裤",       "bottom"),
    "shorts":       ("短裤",         "bottom"),
    "skirt":        ("半身裙",       "bottom"),
    "leggings":     ("打底裤/紧身裤",  "bottom"),
    # ---- 连身 ----
    "dress":        ("连衣裙",       "full"),
    "jumpsuit":     ("连体裤",       "full"),
    "suit":         ("套装",         "full"),
    "pajamas":      ("睡衣套装",      "full"),
    "loungewear":   ("家居服",       "full"),
    "activewear":   ("运动套装",      "full"),
    # ---- 外套 ----
    "jacket":       ("夹克",         "outer"),
    "sun_protection_jacket": ("防晒衣", "outer"),
    "denim_jacket": ("牛仔外套",      "outer"),
    "blazer":       ("西装外套",      "outer"),
    "coat":         ("大衣",         "outer"),
    "trench":       ("风衣",         "outer"),
    "down":         ("羽绒服",       "outer"),
    "parka":        ("棉服",         "outer"),
    "other_outer":  ("其他外套",      "outer"),
    # ---- 鞋 ----
    "sneakers":     ("运动鞋/球鞋",   "shoes"),
    "leather_shoes": ("皮鞋",        "shoes"),
    "boots":        ("靴子",         "shoes"),
    "loafers":      ("乐福鞋",       "shoes"),
    "sandals":      ("凉鞋",         "shoes"),
    "slippers":     ("拖鞋",         "shoes"),
    "other_shoes":  ("其他鞋",       "shoes"),
    # ---- 配饰 ----
    "bag":          ("包",           "accessory"),
    "hat":          ("帽子",         "accessory"),
    "scarf":        ("围巾",         "accessory"),
    "belt":         ("腰带",         "accessory"),
    "socks":        ("袜子",         "accessory"),
    "jewelry":      ("饰品",         "accessory"),
    "glasses":      ("眼镜",         "accessory"),
    "tie":          ("领带",         "accessory"),
    "underwear":    ("内衣",         "accessory"),
    "other_accessory": ("其他配饰",   "accessory"),
    # ---- 兜底 ----
    "other":        ("其他",         "accessory"),
}

# 中文名 -> type_key 的反查（支持用户直接说中文）
TYPE_BY_LABEL: Final[dict[str, str]] = {}
for _k, (_label, _slot) in CLOTHING_TYPES.items():
    TYPE_BY_LABEL[_label] = _k
    # 中文名里带斜杠的，把每个别名都登记一遍（例："毛衣/针织衫"）
    for _alias in _label.replace("／", "/").split("/"):
        _alias = _alias.strip()
        if _alias:
            TYPE_BY_LABEL.setdefault(_alias, _k)

# 常见口语别名 -> type_key（AI 打标和用户输入都可能出现）
TYPE_ALIASES: Final[dict[str, str]] = {
    "t恤": "tshirt", "体恤": "tshirt", "短袖": "tshirt", "tee": "tshirt",
    "衬衣": "shirt", "polo衫": "polo",
    "针织衫": "sweater", "毛衫": "sweater", "卫衣": "hoodie", "连帽衫": "hoodie",
    "吊带": "tank", "背心": "tank", "坎肩": "vest",
    "西裤": "pants", "牛仔裤": "jeans", "仔裤": "jeans",
    "休闲裤": "casual_pants", "工装裤": "casual_pants", "运动裤": "sweatpants",
    "裙": "skirt", "半裙": "skirt", "打底裤": "leggings", "紧身裤": "leggings",
    "裙装": "dress", "连身裙": "dress", "连衣裤": "jumpsuit",
    "羽绒": "down", "羽绒服": "down", "风衣": "trench", "大衣": "coat",
    "夹克衫": "jacket", "牛仔衣": "denim_jacket", "西装": "blazer",
    "球鞋": "sneakers", "运动鞋": "sneakers", "板鞋": "sneakers",
    "皮鞋": "leather_shoes", "靴": "boots", "短靴": "boots",
    "凉拖": "slippers", "背包": "bag", "帽子": "hat", "围巾": "scarf",
    # 两字词形式：包含匹配要求词长 >= 2（否则"包"会命中"面包"），
    # 所以常用的单字词必须补出两字形式，否则丢掉「黑裙子」这类输入
    "鞋子": "other_shoes", "靴子": "boots", "裙子": "skirt", "短裙": "skirt",
    "长裙": "skirt", "裤子": "pants", "棉服": "parka",
    # 口语鞋名：「鞋」是单字，被最小词长规则挡在包含匹配之外，
    # 所以「小白鞋 / 帆布鞋 / 老爹鞋」这类必须显式登记，否则一律落 other，
    # 槽位跟着变成 accessory —— 鞋就从组合计算里消失了（W2 实测漏召）
    "小白鞋": "sneakers", "帆布鞋": "sneakers", "老爹鞋": "sneakers",
    "跑鞋": "sneakers", "篮球鞋": "sneakers", "aj": "sneakers",
    "马丁靴": "boots", "长靴": "boots", "雪地靴": "boots", "雨靴": "boots",
    "高跟鞋": "other_shoes", "平底鞋": "other_shoes", "渔夫鞋": "other_shoes",
    "人字拖": "slippers", "棉拖": "slippers",
    # 英文通用词：槽位叫 shoes，但词表里没有 shoes 这个 key
    # （鞋的 key 是 sneakers/leather_shoes/other_shoes）。
    # 不登记的话，任何来源（包括做打标的模型）输出 "shoes" 都会落到 other，
    # 槽位跟着变成 accessory，鞋就从组合计算里消失了。
    "shoes": "other_shoes", "shoe": "other_shoes", "footwear": "other_shoes",
    "outerwear": "other_outer", "bag": "bag", "hat": "hat", "scarf": "scarf",
    "belt": "belt", "socks": "socks", "sunglasses": "glasses",
    # ---- W0 补（2026-10-08）：商品标题里的「版型 + 品类」复合词 ----
    # 为什么必须显式登记：这些词里都含品类字（裤 / 裙 / 衣），但包含匹配
    # 要求命中**完整词条**，而词表里只有「休闲裤 / 运动裤」这类正名
    # →「直筒裤」「卫裤」一律落 other，槽位跟着变 accessory，
    #   该单品就从组合数 / 缺口分析里**静默消失**。
    # 实测（2026-10-08）：枚举 41 个常见口语品类词，**21 个落 other**。
    #
    # ⚠️ 2026-10-20 修订（用户反馈「西裤和长裤不能放一个品类」）：
    #   当初图省事把「版型词」和「长度词」都归到 `pants`（那时叫"西裤/长裤"），
    #   结果是**用户在回答"什么风格"时被迫同时回答了"多长"**，而且
    #   「运动长裤」被判成西裤。现在拆成三个维度：
    #     · 品类(type)  = 它是什么（牛仔裤 / 西裤 / 休闲裤 / 运动裤 …）
    #     · 长度(length) = 多长（长裤 / 九分 / 七分 / 五分 / 三分）
    #     · 版型(fit)    = 什么剪裁（直筒 / 阔腿 / 锥形 / 喇叭 …）
    #   下面这些词**仍然登记**，但指向中性的 `casual_pants` 兜底 ——
    #   目的是"别掉 other"（掉 other = 掉出组合计算），而不是给它们定风格。
    #   真正的长度由 `LENGTH_ALIASES` 单独识别，版型由 `FITS` 识别。
    "卫裤": "sweatpants", "束脚裤": "sweatpants",
    "运动长裤": "sweatpants", "速干裤": "sweatpants", "篮球裤": "sweatpants",
    "跑步裤": "sweatpants", "训练裤": "sweatpants", "健身裤": "sweatpants",
    "直筒裤": "casual_pants", "阔腿裤": "casual_pants", "锥形裤": "casual_pants",
    "烟管裤": "casual_pants", "微喇裤": "casual_pants", "喇叭裤": "casual_pants",
    "长裤": "casual_pants", "九分裤": "casual_pants", "七分裤": "casual_pants",
    "五分裤": "casual_pants", "三分裤": "casual_pants", "棉裤": "casual_pants",
    # ⚠️「西装裤」必须显式登记：否则最长匹配命中的是「西装」(2字) → `blazer`
    #   → 槽位 `outer` → 一条裤子被算成外套，**从下装组合里静默消失**。
    #   （2026-10-20 Bug 猎手实测：西装裤 / 西装短裤 / 九分西装裤 全部判成 blazer）
    "西装裤": "pants", "西装短裤": "shorts", "西服裤": "pants",
    # 同批补的「X 裤」：它们都含「裤」字但词表里没有，原本一律落 other
    "冲锋裤": "casual_pants", "皮裤": "casual_pants", "校服裤": "casual_pants",
    "灯芯绒裤": "casual_pants", "瑜伽裤": "leggings", "健身长裤": "sweatpants",
    "沙滩裤": "shorts", "热裤": "shorts", "工装短裤": "shorts",
    "牛仔短裤": "shorts", "运动短裤": "shorts", "五分短裤": "shorts",
    "包臀裙": "skirt", "a字裙": "skirt", "百褶裙": "skirt", "伞裙": "skirt",
    "冲锋衣": "jacket", "皮衣": "jacket", "棉袄": "parka",
    # §17：只登记明确的衣物品类，防晒/UPF等卖点不能决定品类。
    "防晒服": "sun_protection_jacket", "防晒外套": "sun_protection_jacket",
    "马夹": "vest", "围脖": "scarf", "皮带": "belt",
}


# ---------------------------------------------------------------------------
# 属性受控词表
# ---------------------------------------------------------------------------
COLORS: Final[dict[str, str]] = {
    "black": "黑", "white": "白", "gray": "灰", "beige": "米白", "khaki": "卡其",
    "brown": "棕", "navy": "藏蓝", "blue": "蓝", "light_blue": "浅蓝",
    "green": "绿", "army_green": "军绿", "red": "红", "burgundy": "酒红",
    "pink": "粉", "purple": "紫", "yellow": "黄", "orange": "橙",
    "silver": "银", "gold": "金", "multi": "多色", "print": "花色",
}

MATERIALS: Final[dict[str, str]] = {
    "cotton": "棉", "linen": "麻", "wool": "羊毛", "cashmere": "羊绒",
    "polyester": "涤纶", "nylon": "尼龙", "silk": "真丝", "leather": "皮革",
    "denim": "牛仔", "knit": "针织", "wool_blend": "毛呢", "down": "羽绒",
    "chiffon": "雪纺", "corduroy": "灯芯绒", "spandex": "氨纶", "mixed": "混纺",
}

# 版型 —— wardrowbe 的 schema 里有 fit 字段、但无 DB 列且不在扁平化白名单，
# 属于「死字段」。我们把它做成真正可持久化、可查询的列。
FITS: Final[dict[str, str]] = {
    # `straight` 原来叫「常规」，但它和 `regular`（合身）在中文里容易混 ——
    # 实际用户说的是「直筒」。合到一条，避免打标时二选一犹豫。
    "slim": "修身", "regular": "合身", "straight": "直筒",
    "loose": "宽松", "oversized": "Oversize", "boxy": "廓形",
    # 裤装版型（2026-10-20 从品类别名挪过来的：它们是剪裁，不是品类）
    "wide_leg": "阔腿", "tapered": "锥形", "flared": "喇叭",
}

#: 裤装长度。**和品类正交** —— 「九分运动裤」= type 运动裤 + length 九分。
#:
#: 为什么单独一个维度（用户 2026-10-20 拍板）：原来「西裤/长裤」一个选项
#: 同时回答了"什么风格"和"多长"两件事，于是「运动长裤」被判成西裤。
LENGTHS: Final[dict[str, str]] = {
    "full": "长裤",
    "nine_tenth": "九分",
    "seven_tenth": "七分",
    "five_tenth": "五分",
    "three_tenth": "三分",
}

#: 长度别名。**只用于 `resolve_length`，不参与品类识别** ——
#: 否则「九分裤」会去抢品类，把一条运动裤判成休闲裤。
LENGTH_ALIASES: Final[dict[str, str]] = {
    # ⚠️ 只收**明确指裤长**的词。曾经收过「长款」「及踝」—— 但它们对
    #   风衣（长款风衣）、裙子（及踝长裙）同样成立，会把「裤长」写到上衣上
    #   （2026-10-20 Bug 猎手实测：「九分袖衬衫」被判出 length=nine_tenth）。
    #   光删词还不够，`supports_length()` 那道类型闸门也必须过。
    "长裤": "full", "全长": "full",
    "九分": "nine_tenth", "九分裤": "nine_tenth", "9分": "nine_tenth", "9分裤": "nine_tenth",
    "七分": "seven_tenth", "七分裤": "seven_tenth", "7分": "seven_tenth", "7分裤": "seven_tenth",
    "五分": "five_tenth", "五分裤": "five_tenth", "5分": "five_tenth", "5分裤": "five_tenth",
    "三分": "three_tenth", "三分裤": "three_tenth", "3分": "three_tenth", "3分裤": "three_tenth",
}

#: 能填裤长的品类。**短裤不算** —— 它本身就是"短"，再记一次长度是冗余；
#: 裙子也不算（裙长是另一套词：长裙/中长裙/短裙，混进来只会让两套语义打架）。
LENGTH_TYPES: Final[frozenset] = frozenset(
    {"jeans", "pants", "casual_pants", "sweatpants", "leggings"}
)


def supports_length(type_key: Optional[str]) -> bool:
    """这个品类该不该有「裤长」。

    ★ 这是**必要的类型闸门**，不是可选的校验：
    「九分」「五分」「长」这些词在别的品类上同样成立 ——
    「九分袖衬衫」「五分袖T恤」都含「九分 / 五分」。
    没有这道闸门，衬衫会被写上 length=nine_tenth，详情页显示「裤长：九分」，
    而且**全程不报错**（2026-10-20 Bug 猎手实测复现）。
    """
    return str(type_key or "") in LENGTH_TYPES

STYLES: Final[dict[str, str]] = {
    "minimal": "极简", "casual": "休闲", "commute": "通勤", "street": "街头",
    "sporty": "运动", "vintage": "复古", "korean": "韩系", "american": "美式",
    "business": "商务", "japanese": "日系", "sweet": "甜美", "unisex": "中性",
}

SEASONS: Final[dict[str, str]] = {
    "spring": "春", "summer": "夏", "autumn": "秋", "winter": "冬",
}

FORMALITIES: Final[dict[str, str]] = {
    "home": "家居", "casual": "休闲", "smart_casual": "通勤",
    "business": "商务正装", "formal": "正式场合", "sport": "运动",
}

SCENES: Final[dict[str, str]] = {
    "daily": "日常", "work": "通勤上班", "date": "约会", "party": "聚会",
    "sport": "运动", "home": "居家", "travel": "旅行", "formal": "正式场合",
    "school": "上课",
}

# 图案。原先临时放在 `services/tagging.py`，P1 收尾时上收到这里 ——
# 其余字段的词表都在本文件，只有它在外会形成两套口径。
PATTERNS: Final[dict[str, str]] = {
    "solid": "纯色",
    "striped": "条纹",
    "plaid": "格纹",
    "floral": "印花",
    "graphic": "图案",
    "dotted": "波点",
    "other": "其他图案",
}

# 全部受控词表，供校验和 AI 提示词使用
VOCABULARIES: Final[dict[str, dict[str, str]]] = {
    "color": COLORS,
    "material": MATERIALS,
    "fit": FITS,
    "style": STYLES,
    "season": SEASONS,
    "formality": FORMALITIES,
    "scene": SCENES,
    "pattern": PATTERNS,
    "length": LENGTHS,
}


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def slot_of(type_key: str) -> str:
    """取某个品类所属的槽位。未知品类归到 accessory（不参与组合计算）。"""
    entry = CLOTHING_TYPES.get(type_key)
    return entry[1] if entry else "accessory"


def label_of(type_key: str) -> str:
    """取某个品类的中文名。"""
    entry = CLOTHING_TYPES.get(type_key)
    return entry[0] if entry else type_key


# 包含匹配的最小词长。单字词（"包""裙""鞋"）只用于精确匹配，
# 不参与包含匹配，否则 "面包" 会被判成手提包。
_MIN_CONTAIN_LEN = 2


def _normalize(text: str) -> str:
    """归一化：小写 + 去掉空格 / 全角空格 / 下划线 / 连字符。

    ⚠️ **匹配的两边都要过这一遍**。2026-10-08 的教训：包含匹配原来只归一化
    输入（`s`），却拿**原样**的词表条目（`raw_s`）去比 —— 于是只对了两半：
    - 词表里的 `T恤`（大写 T）能命中原文的 `T恤`，但命中不了小写输入
    - 新加的 `a字裙`（小写）能命中原文的 `a字裙`，但**命中不了商品标题里的 `A字裙`**

    实测漏召：`resolve_type("黑色A字裙") -> other`（槽位变成 accessory，
    该单品从组合里静默消失），而 `resolve_type("黑色a字裙") -> skirt`。
    """
    return (
        str(text or "")
        .strip()
        .lower()
        .replace(" ", "")
        .replace("\u3000", "")
        .replace("_", "")
        .replace("-", "")
    )


def resolve_type(raw: str) -> str:
    """把用户的自由输入 / AI 输出，尽力映射到受控 type_key。

    顺序：合法 key -> 中文正名 -> 常见别名 -> 最长包含匹配 -> other

    两个已修的坑：
    - 归一化必须同时处理连字符，"t-shirt" 要能命中 "tshirt"（这正是
      设计文档 §6.5 里旧实现的白名单写法，不处理会直接落 other）
    - 包含匹配必须**取最长命中**且设最小词长，否则 "包" 命中 "面包"
    """
    if not raw:
        return "other"
    raw_s = str(raw).strip()
    s = _normalize(raw_s)

    # 1) 已经是合法 key（含带下划线/连字符的写法）
    if raw_s in CLOTHING_TYPES:
        return raw_s
    for k in CLOTHING_TYPES:
        if k.lower().replace("_", "") == s:
            return k

    # 2) 精确匹配中文正名 / 别名（此处单字词是安全的：用户明确只输入了这一个字）
    if raw_s in TYPE_BY_LABEL:
        return TYPE_BY_LABEL[raw_s]
    if raw_s in TYPE_ALIASES:
        return TYPE_ALIASES[raw_s]
    if s in TYPE_ALIASES:
        return TYPE_ALIASES[s]

    # 3) 包含匹配：只取词长 >= 2 的词，且**最长者优先**
    #    （避免 "短袖T恤" 被 "T恤" 之外的短词抢占，也避免单字词误判）
    #    ⚠️ **两边都要归一化**（见 `_normalize` 的注释）—— 原来词表侧用的是
    #   原样 `raw_s`，导致大写写法 `A字裙` 命中不了小写别名 `a字裙`。
    best_key, best_len = None, 0
    for table in (TYPE_ALIASES, TYPE_BY_LABEL):
        for word, key in table.items():
            w = _normalize(word)
            if len(w) < _MIN_CONTAIN_LEN:
                continue
            if w in s and len(w) > best_len:
                best_key, best_len = key, len(w)
    if best_key:
        return best_key

    return "other"


def resolve_length(raw: str) -> Optional[str]:
    """从自由文本里认「裤长」，返回 `LENGTHS` 的 key；认不出返回 None。

    ⚠️ 和 `resolve_type` **分开**，不让长度词进品类别名表：
    否则「九分裤」会去抢品类，把一条运动裤判成休闲裤。
    （「运动长裤」正是这么被判成西裤的 —— 用户 2026-10-20 反馈。）

    同一条文案里可能同时出现多个长度词（"九分裤…长裤"），取**最长命中**；
    等长时取**先出现**的那个 —— 用户写在前面的通常才是主语。
    """
    if not raw:
        return None

    # ① 已经是合法 key 就直接返回。
    #    ⚠️ 这一步不能省：**AI 打标返回的就是 key**（prompt 里列的是 `full(长裤)`，
    #    模型经常直接回 `nine_tenth`）。少了它，打标结果会被当成"认不出"丢掉，
    #    而且不会报错 —— 用户只会觉得"AI 有时候不填长度"。
    raw_s = str(raw).strip().lower()
    if raw_s in LENGTHS:
        return raw_s

    s = _normalize(raw_s)

    best_key: Optional[str] = None
    best_len = 0
    best_pos = len(s) + 1
    for word, key in LENGTH_ALIASES.items():
        w = _normalize(word)
        if len(w) < _MIN_CONTAIN_LEN:
            continue
        pos = s.find(w)
        if pos < 0:
            continue
        if len(w) > best_len or (len(w) == best_len and pos < best_pos):
            best_key, best_len, best_pos = key, len(w), pos
    return best_key


# ---------------------------------------------------------------------------
# 季节
# ---------------------------------------------------------------------------
# 家居/运动套装，不参与"能出门的组合"计算。
# 注意：suit（套装）**必须保留在 full 里** —— 设计文档 §3.2 明确
# {dress, jumpsuit, suit} 都属于 full，它们自带上下配对。
HOME_LIKE: Final[frozenset] = frozenset({"pajamas", "loungewear", "activewear"})

_SEASON_BY_MONTH: Final[dict] = {
    3: "spring", 4: "spring", 5: "spring",
    6: "summer", 7: "summer", 8: "summer",
    9: "autumn", 10: "autumn", 11: "autumn",
    12: "winter", 1: "winter", 2: "winter",
}

SEASON_LABELS: Final[dict] = {
    "spring": "春", "summer": "夏", "autumn": "秋", "winter": "冬",
    "all": "全部季节",
}


def current_season(d: date | None = None) -> str:
    """当前季节（北半球 / 中国口径）。"""
    return _SEASON_BY_MONTH[(d or date.today()).month]


def usable_in(item: dict, season: str) -> bool:
    """这件单品这一季能不能穿。

    - 标了季节：包含该季，或标全四季（>= 4）视为通用
    - 没标季节：**不排除**。未标注等于未知，宁可多算，
      也不能因为还没打标就把组合数误判成 0
    - 注意不能用 `len(seasons) >= 3` 判通用：一件标了「春+夏+秋」的衣服
      会被误判成四季通用而并入冬天
    """
    seasons = item.get("season") or []
    if not seasons:
        return True
    # `"all"` 既是记忆桶名、也允许当作季节参数传进来（表示"不分季节"）。
    # 不特判的话，`usable_in(item, "all")` 对**任何标了季节的单品**都返回 False，
    # 于是 `?season=all` 会静默给出 0 套组合 —— 而界面上明明写着「全部季节」。
    if season == "all":
        return True
    return season in seasons or len(seasons) >= 4


def _eligible(items: list[dict], season: str) -> list[dict]:
    out = []
    for it in items:
        if it.get("is_archived"):
            continue
        if (it.get("type") or "other") in HOME_LIKE:
            continue
        if not usable_in(it, season):
            continue
        out.append(it)
    return out


def _combos(t: int, b: int, f: int, s: int) -> int:
    """分体路径 T*B*S + 连身路径 F*S。两条路径互斥所以相加。"""
    return (t * b + f) * s


def count_combinations(items: list[dict], season: str | None = None) -> dict:
    """算「这季能搭出多少套」，并给出「该补哪个槽位」的排序。

    公式：分体 T*B*S + 连身 F*S = (T*B + F) * S
    外套/配饰是可选层，不计入基础组合数（否则数字爆炸且不可解释）。

    **关于"瓶颈"的重要说明**：
    纯乘法模型下，「某槽位少一件」的相对跌幅恒等于 1/该槽位件数，
    所以朴素瓶颈指标必然退化成"件数最少的槽位"——这不是 bug，是模型性质。
    因此这里不返回 bottleneck，改为返回两个更有用的东西：
      1) blocking_slots：组合数为 0 时缺哪些槽位（真正的"断链"）
      2) suggestions：按「绝对增量 ΔC × 多样性折扣」排序的补货建议
    """
    season = season or current_season()
    return _stats(_eligible(items, season), season)


def count_ignoring_season(items: list[dict]) -> dict:
    """不按季节过滤的组合数。

    用途：本季算出来是 0 的时候，区分"真的没衣服"和"衣服都在别的季节"。
    对一个正常衣橱在换季时显示 0 套而不给解释，用户只会以为程序坏了。
    """
    pool = [
        it
        for it in items
        if not it.get("is_archived") and (it.get("type") or "other") not in HOME_LIKE
    ]
    return _stats(pool, "all")


def _stats(pool: list[dict], season: str) -> dict:
    counts = {s: 0 for s in SLOTS}
    colors_by_slot: dict = {s: set() for s in SLOTS}
    for it in pool:
        slot = it.get("slot") or slot_of(it.get("type", "other"))
        if slot not in counts:
            continue
        counts[slot] += 1
        c = it.get("primary_color")
        if c:
            colors_by_slot[slot].add(c)

    t, b, f, s = counts["top"], counts["bottom"], counts["full"], counts["shoes"]
    total = _combos(t, b, f, s)

    # 组合数为 0 时，指出缺什么才能从 0 到 1
    blocking: list = []
    if total == 0:
        if s == 0:
            blocking.append("shoes")
        if t == 0 and f == 0:
            blocking.append("top")
        if b == 0 and f == 0:
            blocking.append("bottom")

    # 建议排序：ΔC × 多样性折扣
    suggestions = []
    for slot in ("top", "bottom", "shoes", "full"):
        trial = dict(counts)
        trial[slot] += 1
        delta = _combos(trial["top"], trial["bottom"], trial["full"], trial["shoes"]) - total
        n = counts[slot]
        # 多样性折扣 = 该槽位已有不同主色数 / 件数。
        # 已经囤了 5 条黑裤子，再买一条黑的——组合数涨了但搭不出新感觉。
        # 件数为 0 时不打折（没有可重复的东西）。
        discount = (len(colors_by_slot[slot]) / n) if n > 0 else 1.0
        suggestions.append(
            {
                "slot": slot,
                "slot_label": SLOTS[slot],
                "current_count": n,
                "delta": delta,
                "diversity_discount": round(discount, 3),
                "score": round(delta * discount, 3),
            }
        )
    suggestions.sort(key=lambda x: x["score"], reverse=True)

    return {
        "season": season,
        "season_label": SEASON_LABELS.get(season, season),
        "counts": counts,
        "total_combinations": total,
        "min_viable": total >= 1,
        "blocking_slots": blocking,
        "blocking_labels": [SLOTS[x] for x in blocking],
        "suggestions": suggestions,
    }
