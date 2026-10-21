"""品牌受控词表（**打标专用**）。

为什么单独一个文件、不塞进 `taxonomy.py`：

1. `taxonomy.py` 在 `AGENTS.md` 的 Do Not Modify 清单里（"品类词表是缺口分析 /
   组合数 / 打标的地基"），改它需要统一决策，不是加一行就能走的。
2. `taxonomy.VOCABULARIES` 会经 `GET /taxonomy` **原样吐给前端**去渲染下拉框和
   勾选框。品牌是**开放集**（新牌子层出不穷，而且没人愿意维护一份全量品牌表），
   塞进去等于顺手改掉前端契约 —— 而前端的品牌是一个自由文本框，消费不了词表。

所以品牌词表只被 `services/tagging.py` 消费：**AI 侧受控，手填侧仍是自由文本**
（用户自己打的新牌子不会被拦，见下方"新牌子进不来"）。

---

取值口径（`tagging._match` 的四级匹配会用到）：

- `key` —— 规范化的英文名/拼音，**只做归一入口**，不进库
- `label` —— **真正写进库里的值**，用中文用户最习惯的写法（国产品牌用中文，
  国际品牌用英文原名）

为什么存 `label` 而不是 `key`：`brand` 在表里是自由 `TEXT` 列
（`schema.py:78`），用户手填时写的就是「维动」。如果 AI 存 `veidoorn`、
用户手填「维动」，同一个牌子在库里就是两种写法 —— 按品牌检索/统计直接分裂。
存 `label` 让 AI 和手填落在同一个字符串上。

`_match` 的四级匹配在这里很关键，模型可能返回：
`"维动"` / `"veidoorn维动"` / `"维动 veidoorn"` / `"VEIDOORN"`
全部落到同一个 `veidoorn`，写出同一个「维动」。

⚠️ **加牌子只改本文件**。prompt 里的品牌选项由 `tagging._vocab_lines()`
从本表自动生成，不需要同步改文案。
"""

import re

#: 品牌 `key -> 写库用的展示名`
BRANDS: dict[str, str] = {
    # -- 国产运动户外（用户主力品类；veidoorn 来自 2026-10-21 维动订单截图实测）--
    "veidoorn": "维动",
    "lining": "李宁",
    "anta": "安踏",
    "xtep": "特步",
    "erke": "鸿星尔克",
    "peak": "匹克",
    "361": "361°",
    "warrior": "回力",
    "feiyue": "飞跃",
    "qiaodan": "乔丹体育",
    # -- 国际运动 --
    "nike": "Nike",
    "adidas": "Adidas",
    "puma": "Puma",
    "under_armour": "Under Armour",
    "new_balance": "New Balance",
    "asics": "ASICS",
    "converse": "Converse",
    "vans": "Vans",
    "skechers": "Skechers",
    "reebok": "Reebok",
    "fila": "FILA",
    "lululemon": "lululemon",
    "salomon": "Salomon",
    "hoka": "HOKA",
    # ⚠️ 不能写 `"on": "On"` —— 2 个字符会参与"包含匹配"，
    #    任何含 `on` 的英文串都会命中（`Columbia` 不含，但 `Champion` 含 `on`）。
    "onrunning": "On（昂跑）",
    "decathlon": "迪卡侬",
    # -- 快时尚 / 休闲 --
    "uniqlo": "优衣库",
    "muji": "无印良品",
    "zara": "ZARA",
    "hm": "H&M",
    "gap": "GAP",
    "levis": "Levi's",
    "lee": "Lee",
    "wrangler": "Wrangler",
    "dickies": "Dickies",
    "champion": "Champion",
    "baleno": "班尼路",
    "semir": "森马",
    "peacebird": "太平鸟",
    "metersbonwe": "美特斯邦威",
    "hla": "海澜之家",
    "giordano": "佐丹奴",
    "yishion": "以纯",
    "tonlion": "唐狮",
    # -- 户外 --
    "the_north_face": "北面",
    "columbia": "哥伦比亚",
    "arcteryx": "始祖鸟",
    "kailas": "凯乐石",
    "camel": "骆驼",
    "patagonia": "Patagonia",
    "mammut": "猛犸象",
    "toread": "探路者",
    # -- 男装 / 羽绒 --
    "septwolves": "七匹狼",
    "kboxing": "劲霸",
    "joeone": "九牧王",
    "romon": "罗蒙",
    "youngor": "雅戈尔",
    "hodo": "红豆",
    "bosideng": "波司登",
    "snow_flying": "雪中飞",
    # -- 内衣 / 家居 --
    "bananain": "蕉内",
    "ubars": "Ubras",
    "neiwai": "内外",
    "threegun": "三枪",
}


#: 品牌别名 → **写库用的展示名**（值必须与上面 `BRANDS` 里某个 label 字面一致）。
#
# 为什么需要这张表（2026-10-21 实测）：`BRANDS` 里 `nike` 的 label 是 `Nike`，
# 而模型经常返回中文译名「耐克」。`_match` 拿「耐克」去比 `nike` / `Nike` 全都
# 对不上（`_norm` 不做翻译）→ 直接进 `unresolved`，白丢一个已经读到的品牌。
#
# 覆盖范围只包括 **label 用了英文原名** 的品牌。label 本身是中文的
# （优衣库 / 李宁 / 始祖鸟……）不需要别名 —— `_match` 的第 3 级「中文标签全等」
# 已经能认，反过来模型给 `UNIQLO` 也能命中 key。
#
# 值写成展示名而不是 key，是为了让调用方一步到位：
#     hit = _match(raw, BRANDS)
#     if hit:            clean["brand"] = BRANDS[hit]
#     elif _match(raw, ALIASES): clean["brand"] = ALIASES[alias]
# ⚠️ 加别名的同时请确认右侧字符串**逐字等于** `BRANDS` 里的 label，
#    否则同一个牌子会写出两种写法（本表存在的意义就是防止这件事）。
ALIASES: dict[str, str] = {
    "耐克": "Nike",
    "阿迪达斯": "Adidas",
    "阿迪": "Adidas",
    "彪马": "Puma",
    "安德玛": "Under Armour",
    "新百伦": "New Balance",
    "纽巴伦": "New Balance",
    "亚瑟士": "ASICS",
    "匡威": "Converse",
    "万斯": "Vans",
    "斯凯奇": "Skechers",
    "锐步": "Reebok",
    "斐乐": "FILA",
    "露露乐蒙": "lululemon",
    "露露柠檬": "lululemon",
    "萨洛蒙": "Salomon",
    "霍卡": "HOKA",
    "昂跑": "On（昂跑）",
    "李维斯": "Levi's",
    "威格": "Wrangler",
    "迪凯斯": "Dickies",
    "冠军": "Champion",
    "巴塔哥尼亚": "Patagonia",
    "飒拉": "ZARA",
}


# ---------------------------------------------------------------------------
# 归一：把模型给的任意写法 → 落库展示名
# ---------------------------------------------------------------------------
#
# ⚠️ **为什么品牌不能用 `tagging._match()`**
#
# `_match` 的第 4 级是"最长包含匹配"（词长 ≥2 就算命中子串）。对品类/颜色
# 那种封闭短词表它是对的 —— "短袖T恤" 就该命中「短袖」。但品牌字段的输入
# 常常是**商品标题或店铺名**，子串匹配会静默写错牌子（2026-10-21 实测）：
#
#     骆驼绒大衣      → 「骆驼」     ← 材质被当成品牌
#     奥林匹克联名    → 「匹克」     ← 两个字恰好是子串
#     冠军版          → 「Champion」
#     Lee Cooper      → 「Lee」
#     红豆沙色        → 「红豆」
#
# 品牌写错**不报错、直接进库**，是这一层最坏的错误形态。所以这里走
# **token 级全等**：先把串切成「英文/数字串」和「连续中文」两种 token，
# 再用 token 去全等匹配 —— 不做任何子串包含。
#
# 这样既保住了模型常见的两种写法：
#     veidoorn维动 → ["veidoorn", "维动"] → 取最长命中 → 维动
#     维动 veidoorn → 同上
# 又不会把长中文串里的一小段当品牌。
#
# 已知残留（可接受）：`Lee Cooper` 会落到「Lee」—— 它是真实子品牌，
# 记成 Lee 不算错得离谱；而 `骆驼绒大衣` 这种会把**材质**当品牌的才是灾难。

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9'&.’]*|[\u4e00-\u9fff]+", re.IGNORECASE)

#: 店铺名/品牌名后面常跟的后缀。剥掉后再全等匹配 —— 模型从截图里读到的
#: 往往是「维动旗舰店」而不是「维动」（实测 2026-10-21）。
#:
#: ⚠️ 只能在**去后缀**这个方向上放宽，不能改成前缀匹配：
#: 那样「骆驼绒大衣」会以「骆驼」开头而误命中，正是上面要防的那类错误。
#: 顺序必须长后缀在前（「官方旗舰店」要先于「旗舰店」被剥）。
_SHOP_SUFFIXES = (
    "官方旗舰店", "旗舰店", "官方店", "专卖店", "专营店", "自营店",
    "官方", "服饰", "体育", "运动", "店",
)


def _strip_suffix(token: str) -> str:
    """去掉"旗舰店/官方店/服饰"这类尾巴。不是纯中文长串就原样返回。"""
    for suffix in _SHOP_SUFFIXES:
        if len(token) > len(suffix) and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def _flat(text: str) -> str:
    """归一：小写 + 去掉空格 / 全角空格 / 下划线 / 连字符 / 撇号。

    撇号必须去掉（`_match` 的 `_norm` 没去掉，所以 `Arc'teryx` 和
    `Levi's` 在那边永远认不出 —— 实测 2026-10-21）。
    """
    return (
        str(text).strip().lower()
        .replace(" ", "")
        .replace("\u3000", "")
        .replace("_", "")
        .replace("-", "")
        .replace("'", "")
        .replace("’", "")
    )


def _build_lookup() -> tuple[dict[str, str], dict[str, str]]:
    """拼一张「任何写法 → 落库展示名」的表。

    `BRANDS` 的 key（英文/拼音）和 label（展示名）都算可识别写法，
    `ALIASES` 的中文译名再叠一层。用 `setdefault` 保证 label 不被同值的
    key 覆盖（`lee` / `Lee` 归一后同为 `lee`，两者都指向 `Lee`，无害）。
    """
    exact: dict[str, str] = {}
    for key, label in BRANDS.items():
        exact[key] = label
        exact[label] = label
    exact.update(ALIASES)

    flat: dict[str, str] = {}
    for key, label in exact.items():
        flat.setdefault(_flat(key), label)
    return exact, flat


_ALL, _FLAT = _build_lookup()


def resolve(raw: object) -> str | None:
    """模型给的品牌串 → **写库用的展示名**；认不出返回 `None`。

    三级，从严到宽：
    1. 整串字面命中（`维动` / `Nike` / `H&M`）
    2. 整串归一后命中（`Arc'teryx` / `LI-NING` / `H & M` / `veidoorn`）
    3. 切成 token 后**全等**命中，多个命中取最长的那个
       （`veidoorn维动` / `维动 veidoorn`）—— **仍然不做子串包含**
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None

    hit = _ALL.get(text)
    if hit:
        return hit

    hit = _FLAT.get(_flat(text))
    if hit:
        return hit

    hits: list[tuple[int, str]] = []
    for token in _TOKEN_RE.findall(text):
        found = _FLAT.get(_flat(token)) or _FLAT.get(_flat(_strip_suffix(token)))
        if found:
            hits.append((len(token), found))
    if hits:
        hits.sort(reverse=True)
        return hits[0][1]
    return None
