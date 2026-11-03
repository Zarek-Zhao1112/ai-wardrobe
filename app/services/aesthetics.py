"""§20：一套版本化审美贯穿召回、组套、关系评分与说明。"""
from __future__ import annotations

from copy import deepcopy
from itertools import product
from typing import Callable

from .. import taxonomy
from .aesthetic_catalog import ACTIVE_IDS, NEW_PROFILES
from . import garment_profile, visual_aesthetics

DEFAULT = "natural"
DIMENSIONS = (("color", "配色"), ("silhouette", "轮廓"), ("material", "材质"), ("pattern", "图案"), ("layering", "层次"))
SOURCES = [
    {"title": "LifeWear：叠穿与材质层次", "url": "https://www.uniqlo.com/au/en/contents/lifewear-magazine/the-modern-layering-guide/"},
    {"title": "Derek Guy：整体轮廓", "url": "https://putthison.com/how-to-understand-silhouettes-pt-two/"},
    {"title": "LifeWear：图案颜色呼应", "url": "https://www.uniqlo.com/cn/news/topics/2025041802/"},
    {"title": "FIT：面料与服装形态", "url": "https://exhibitions.fitnyc.edu/fabric-in-fashion/"},
]
PROFILES = {
    "relaxed": {
        "id": "relaxed", "version": 1, "name": "自然松弛",
        "summary": "舒展的轮廓与连贯配色，层次自然展开。",
        "core": "以舒展、有余量的整体轮廓为核心，配色尽量连贯。",
        "flexible": "可以上下都宽松，也可以松紧对比；颜色、材质和层数可变化。",
        "dimensions": [
            {"key": "color", "label": "配色", "goal": "优先试相近色与颜色呼应，柔和过渡的实际效果需看色调。"},
            {"key": "silhouette", "label": "轮廓", "goal": "以宽松、直筒或阔腿等舒展线条组合，保留穿着余量。"},
            {"key": "material", "label": "材质", "goal": "偏自然、柔和的表面质感；不能由成分名推断垂坠与柔软。"},
            {"key": "pattern", "label": "图案", "goal": "图案可融入整体，以纯色配合或颜色呼应建立联系。"},
            {"key": "layering", "label": "层次", "goal": "可用外套或配饰增加层次；不要求固定层数，露出方式可调整。"},
        ],
    },
    "clean": {
        "id": "clean", "version": 1, "name": "利落清爽",
        "summary": "明确的轮廓与清晰配色，细节保留重点。",
        "core": "以明确的轮廓和清晰的颜色关系为核心，减少不必要的堆叠。",
        "flexible": "可以宽松也可以修身；允许亮色和图案，不设颜色数量上限。",
        "dimensions": [
            {"key": "color", "label": "配色", "goal": "优先试清晰的深浅关系或局部颜色呼应，亮色可以成为重点。"},
            {"key": "silhouette", "label": "轮廓", "goal": "以合身、直筒等明确线条为起点，宽松款也可作为变化。"},
            {"key": "material", "label": "材质", "goal": "偏平整、简洁的表面质感；实际光泽和挺阔情况待确认。"},
            {"key": "pattern", "label": "图案", "goal": "可用纯色组合，也可保留图案重点，让其他单品形成配合。"},
            {"key": "layering", "label": "层次", "goal": "优先较少的层次，也可加一件外套或配饰，保持关系清楚。"},
        ],
    },
}


def _profile(key, name, summary, core, flexible, goals):
    return {"id": key, "version": 1, "name": name, "summary": summary,
            "core": core, "flexible": flexible,
            "dimensions": [{"key": k, "label": label, "goal": goal}
                           for (k, label), goal in zip(DIMENSIONS, goals)]}


# §20.1：编辑整理的方向；每套同时安排五维，而非单个评分开关。
PROFILES.update({
    "tonal": _profile("tonal", "同色层次", "相近颜色贯穿全身，用深浅与叠穿展开层次。",
        "以同色系的连续感为核心，让轮廓和细节承接颜色。",
        "允许不同色系的小面积变化；不要求全身完全同色，也不强制加外套。", [
            "优先同色、相近色系的呼应；标签相近不代表实际色调一致。",
            "从直筒、合身或宽松的连续线条出发，避免靠堆叠制造轮廓。",
            "可尝试不同表面质感形成层次，光泽与纹理需看实物确认。",
            "纯色便于观察颜色层次，图案也可通过共同颜色融入。",
            "可加同色系外套或配饰；层次也可以来自颜色，不强求层数。"]),
    "heritage": _profile("heritage", "复古工装", "大地色与蓝绿调相接，牛仔、夹克和靴子彼此呼应。",
        "以复古标签和工装相关品类的组合为线索，建立舒展而有结构的整体。",
        "不要求每件都是复古款，允许用简洁基础款调节；品类不能证明具体工装细节。", [
            "优先卡其、棕、米白、军绿与蓝色系，比较它们之间的联系。",
            "以直筒、宽松、阔腿等线条衔接，具体余量与衣长仍需试穿。",
            "可尝试有纹理的表面，但牛仔或棉等标注不能证明厚度、做旧与挺阔。",
            "纯色、条纹、格纹都可参与；实际图案大小与密度待确认。",
            "尝试衬衫、牛仔裤、夹克和靴等不同角色的呼应，外套与配饰可选。"]),
    "sport_mix": _profile("sport_mix", "运动混搭", "把运动元素放进利落基础款中，形成有分寸的反差。",
        "让运动元素与较利落的基础单品共同出现，而非单纯凑齐一套运动装。",
        "一双球鞋即可作为运动元素；不要求运动品牌，也不按适用场合选衣。", [
            "用重复颜色或清晰的深浅关系把不同元素连起来。",
            "合身、直筒、宽松均可起步，让上下轮廓留有余量。",
            "可尝试不同表面质感对照，不能由运动品类推断功能面料或舒适度。",
            "纯色与少量图案都可配合，视觉重点是否清楚需看实物。",
            "优先比较球鞋、卫衣、运动裤与衬衫、西裤等基础单品的跨角色组合。"]),
    "street": _profile("street", "街头廓形", "宽大轮廓与球鞋衔接，图案或配饰保留表达。",
        "以有存在感的宽大衣服和鞋服关系为核心，构成明确的整体体量。",
        "不要求每件都宽大；可用合身单品形成对比，也不强制标语或品牌图案。", [
            "可用深浅对比或局部重复颜色，把宽大单品连成整体。",
            "优先Oversize、廓形、阔腿等体量线索，实际上身体积仍需确认。",
            "表面可平整也可有纹理，厚度、硬度与支撑感不能靠成分判断。",
            "可让一件图案款成为重点，或用纯色让整体轮廓更突出。",
            "比较宽大衣服与球鞋或靴的衔接，帽子、包和外套可作变化。"]),
    "soft": _profile("soft", "柔和轻盈", "浅色相接与舒展线条，让整体保持轻快。",
        "以浅色标签、舒展线条和较少堆叠作轻盈感的近似尝试。",
        "不限制性别或裙裤；浅色不能证明低饱和度，柔软、透气与轻薄需另行确认。", [
            "优先米白、白、浅蓝、粉等浅色线索，实际明度和饱和度待确认。",
            "以合身、宽松、直筒或适度展开的线条组合，不推断人体比例。",
            "可尝试轻柔表面，不能由真丝、雪纺或针织等名称断言轻薄垂坠。",
            "纯色、印花或波点可作变化；图案是否细小轻巧需看图片确认。",
            "优先较少叠层，按衣橱选择轻快的基础组合；外套与配饰并非禁用。"]),
    "color_pop": _profile("color_pop", "撞色焦点", "让重点色与基础色对话，其他细节配合焦点。",
        "以重点色和其他颜色之间的关系为核心，不以颜色数量判断好坏。",
        "允许多个彩色单品和多图案；单品颜色面积、鲜艳度与真实焦点需看图确认。", [
            "优先基础色衬托重点色，或不同色系形成呼应；不把颜色越多当成越好。",
            "从合身、直筒等清楚线条出发，也可用宽松版型承接颜色重点。",
            "表面质感服务于颜色重点，光泽、纹理与实际色彩表现需看实物。",
            "纯色可衬托图案，多个图案也可通过共同颜色建立联系。",
            "衣服、鞋或配饰都可承担重点色；不强制增加配饰或层数。"]),
})


# §34：新生成目录与旧方案分开；不改写已有ID的含义和历史快照。
PROFILES.update(NEW_PROFILES)


def get_profile(key: str | None = None) -> dict:
    if (key or DEFAULT) not in PROFILES:
        raise ValueError("未知的审美方案，请重新选择")
    profile = deepcopy(PROFILES[key or DEFAULT])
    if profile["id"] in ACTIVE_IDS:
        profile["version"] = 2
    profile["sources"] = deepcopy(SOURCES)
    return profile


def list_profiles() -> list[dict]:
    return [get_profile(key) for key in ACTIVE_IDS]


def colors_of(item: dict) -> set[str]:
    return {color for color in [item.get("primary_color"), *(item.get("colors") or [])]
            if color in taxonomy.COLORS and color not in {"multi", "print"}}


_FAMILIES = [{"blue", "navy", "light_blue"}, {"beige", "khaki", "brown"},
             {"white", "gray", "black", "silver"}, {"red", "burgundy", "pink"},
             {"green", "army_green"}, {"yellow", "orange", "gold"}]
_FIT = {
    "relaxed": {"loose": 1., "oversized": 1., "wide_leg": 1., "boxy": .95, "straight": .85, "regular": .65, "flared": .7, "tapered": .55, "slim": .35},
    "clean": {"regular": 1., "straight": 1., "tapered": .9, "slim": .9, "boxy": .65, "loose": .6, "wide_leg": .6, "flared": .65, "oversized": .5},
    "tonal": {"straight": 1., "regular": .9, "loose": .85, "wide_leg": .8, "boxy": .75, "tapered": .75, "slim": .65, "flared": .65, "oversized": .65},
    "heritage": {"straight": 1., "loose": .95, "wide_leg": .9, "boxy": .85, "regular": .75, "oversized": .7, "tapered": .65, "flared": .65, "slim": .4},
    "sport_mix": {"straight": 1., "regular": .9, "loose": .9, "tapered": .85, "wide_leg": .8, "boxy": .75, "oversized": .65, "slim": .65, "flared": .65},
    "street": {"oversized": 1., "boxy": 1., "wide_leg": 1., "loose": .9, "straight": .7, "regular": .55, "flared": .65, "tapered": .5, "slim": .35},
    "soft": {"regular": .9, "loose": 1., "straight": .9, "flared": .9, "wide_leg": .85, "tapered": .7, "boxy": .6, "oversized": .6, "slim": .65},
    "color_pop": {"regular": 1., "straight": 1., "loose": .85, "boxy": .85, "wide_leg": .85, "tapered": .85, "flared": .8, "slim": .75, "oversized": .75},
}


def _rules(styles, patterns, types, palette, color, pattern, layer, relation, weights, legacy=False):
    return dict(styles=set(styles), patterns=set(patterns), types=set(types), palette=set(palette),
                color=color, pattern=pattern, layer=layer, relation=relation,
                weights=dict(zip(("color", "silhouette", "pattern", "layering"), weights)), legacy=legacy)


# 每套显式配置策略，无默认回落到另一套方案；旧v1保留原公式。
_RULES = {
    "relaxed": _rules(["casual", "japanese", "unisex"], ["solid"], [], [], "family", "relaxed", "optional", "none", (3, 4, 2, 1), True),
    "clean": _rules(["minimal", "unisex"], ["solid"], [], [], "contrast", "focal", "sparse", "none", (3, 4, 2, 1), True),
    "tonal": _rules(["minimal", "casual"], ["solid"], [], [], "tonal", "tonal", "optional", "none", (5, 2, 1, 2)),
    "heritage": _rules(["vintage", "american", "casual"], ["solid", "plaid", "striped"],
        ["shirt", "jeans", "casual_pants", "denim_jacket", "jacket", "boots"],
        ["beige", "khaki", "brown", "army_green", "blue", "navy"], "heritage", "heritage", "optional", "heritage", (3, 3, 1, 3)),
    "sport_mix": _rules(["sporty", "minimal", "casual"], ["solid", "striped"],
        ["hoodie", "sweatshirt", "sweatpants", "sneakers", "shirt", "pants", "blazer"], [],
        "contrast", "focal", "sparse", "sport_mix", (2, 2, 1, 5)),
    "street": _rules(["street", "american"], ["graphic", "solid"], ["hoodie", "tshirt", "jeans", "sneakers", "hat"], [],
        "contrast", "street", "optional", "street", (2, 4, 2, 2)),
    "soft": _rules(["casual", "minimal", "sweet"], ["solid", "floral", "dotted"],
        ["shirt", "blouse", "cardigan", "skirt", "dress", "sandals"], ["white", "beige", "light_blue", "pink"],
        "soft", "soft", "sparse", "none", (4, 3, 1, 2)),
    "color_pop": _rules(["minimal", "street"], ["solid", "graphic"], [], [],
        "accent", "focal", "sparse", "none", (6, 2, 1, 1)),
}
_FIT.update({
    "urban": {"regular": 1., "straight": 1., "tapered": .95, "slim": .85, "boxy": .7, "loose": .55, "wide_leg": .65, "flared": .6, "oversized": .4},
    "natural": {"loose": 1., "straight": .95, "wide_leg": .9, "regular": .75, "boxy": .75, "tapered": .65, "flared": .7, "slim": .4, "oversized": .7},
    "heritage_v2": {"straight": 1., "loose": .9, "boxy": .9, "regular": .8, "wide_leg": .8, "tapered": .65, "flared": .65, "oversized": .6, "slim": .4},
    "street_v2": {"oversized": 1., "boxy": 1., "wide_leg": 1., "loose": .9, "straight": .7, "regular": .55, "tapered": .45, "slim": .35, "flared": .7},
    "technical": {"regular": 1., "tapered": 1., "straight": .95, "loose": .8, "boxy": .65, "wide_leg": .6, "slim": .6, "oversized": .4, "flared": .4},
    "refined": {"regular": 1., "straight": .9, "tapered": .95, "wide_leg": .8, "loose": .7, "slim": .7, "flared": .8, "boxy": .5, "oversized": .35},
})
_RULES.update({
    "urban": _rules(["minimal", "unisex"], ["solid"],
        ["shirt", "polo", "sweater", "pants", "blazer", "coat", "leather_shoes", "loafers"],
        ["white", "gray", "black", "navy", "beige"], "contrast", "tonal", "sparse", "urban", (3, 4, 2, 3)),
    "natural": _rules(["casual", "japanese", "unisex"], ["solid", "striped"],
        ["tshirt", "shirt", "sweater", "cardigan", "casual_pants", "jeans", "sneakers", "loafers"],
        ["beige", "khaki", "brown", "gray", "blue", "light_blue", "green", "army_green"],
        "family", "relaxed", "optional", "natural", (3, 4, 1, 3)),
    "heritage_v2": _rules(["vintage", "american", "casual"], ["solid", "plaid", "striped"],
        ["shirt", "sweater", "jeans", "casual_pants", "denim_jacket", "jacket", "boots", "leather_shoes"],
        ["beige", "khaki", "brown", "army_green", "blue", "navy", "burgundy"],
        "heritage", "heritage", "optional", "heritage", (3, 3, 2, 4)),
    "street_v2": _rules(["street", "american", "unisex"], ["solid", "graphic"],
        ["hoodie", "tshirt", "sweatshirt", "jeans", "casual_pants", "jacket", "sneakers", "hat"], [],
        "contrast", "street", "optional", "street_v2", (2, 5, 2, 4)),
    "technical": _rules(["sporty", "minimal", "casual"], ["solid"],
        ["sun_protection_jacket", "jacket", "vest", "sweatshirt", "casual_pants", "sweatpants", "sneakers"],
        ["gray", "black", "navy", "blue", "light_blue", "army_green", "white"],
        "contrast", "focal", "sparse", "technical", (2, 3, 1, 5)),
    "refined": _rules(["minimal", "korean", "unisex"], ["solid", "striped", "dotted"],
        ["shirt", "polo", "blouse", "sweater", "cardigan", "pants", "skirt", "dress", "blazer", "loafers", "leather_shoes"],
        ["beige", "gray", "brown", "khaki", "white", "pink", "light_blue", "navy"],
        "family", "focal", "sparse", "refined", (3, 3, 2, 4)),
})
_ACCESSORIES = {"bag", "hat", "scarf", "belt", "socks", "jewelry", "glasses"}


def item_affinity(item: dict, profile: dict) -> float:
    """同一方案的召回线索；未知值中立，不通过材质猜触感。"""
    fit = _FIT[profile["id"]].get(item.get("fit"), .5)
    styles = set(item.get("style") or [])
    rules = _RULES[profile["id"]]
    style = .8 if styles & rules["styles"] else .5
    pattern = .8 if item.get("pattern") in rules["patterns"] else .5
    if rules["legacy"]:
        return .7 * fit + .15 * style + .15 * pattern
    kind = .9 if item.get("type") in rules["types"] else .5
    palette = .9 if colors_of(item) & rules["palette"] else .5
    baseline = .4 * fit + .15 * style + .15 * pattern + .15 * kind + .15 * palette
    return .75 * baseline + .25 * visual_aesthetics.affinity(item, profile) if visual_aesthetics.active(profile) else baseline


def _recall(items: list[dict], profile: dict, pref: Callable, cap: int = 12, context: list[dict] | None = None) -> list[dict]:
    """先匹配方案，再留探索名额；同分不只依赖录入先后。"""
    mode = _RULES[profile["id"]]["color"]
    color_context = sorted({(it.get("slot") or "", tuple(sorted(colors_of(it))))
                            for it in (context or []) if colors_of(it)})
    def affinity(item):
        value = item_affinity(item, profile)
        if mode not in {"tonal", "accent"} or not colors_of(item) or not color_context:
            return value
        slot = item.get("slot")
        compatible = {"top": {"bottom", "shoes"}, "bottom": {"top", "shoes"}, "full": {"shoes"},
                      "shoes": {"top", "bottom", "full"}, "outer": {"top", "bottom", "full", "shoes"},
                      "accessory": {"top", "bottom", "full", "shoes"}}.get(slot, set())
        relationships = [_color_score([item, {"colors": list(colors)}], profile)[0]
                         for role, colors in color_context if role in compatible]
        return .65 * value + .35 * sum(relationships) / len(relationships) if relationships else value
    ranked = sorted(items, key=lambda it: (-(.85 * affinity(it) + .15 * (pref(it) + 1) / 2),
        it.get("suggestion_count", 0), str(it["id"])))
    if len(ranked) <= cap:
        return ranked
    selected = ranked[:cap - 4]
    remaining = ranked[cap - 4:]
    selected += [remaining[(n * (len(remaining) - 1)) // 3] for n in range(4)]
    return selected


def build_candidates(all_items: list[dict], profile: dict, season: str, preference_score: Callable,
                     anchor_item_id: str | None = None) -> list[dict]:
    pool = [it for it in all_items if not it.get("is_archived") and it.get("type") not in taxonomy.HOME_LIKE and taxonomy.usable_in(it, season)]
    slots = {slot: _recall([it for it in pool if it.get("slot") == slot], profile, preference_score, context=pool)
             for slot in taxonomy.SLOTS}
    # 必选衣物不参与召回淘汰；基础槽直接固定，避免先截短再过滤丢失低亲和单品。
    anchor = next((it for it in pool if it["id"] == anchor_item_id), None)
    if anchor_item_id and not anchor:
        return []
    open_anchor = bool(anchor and visual_aesthetics.active(profile) and garment_profile.can_open_layer(anchor))
    if anchor and anchor["slot"] in {"top", "bottom", "full", "shoes"} and not open_anchor:
        slots[anchor["slot"]] = [anchor]
        if anchor["slot"] in {"top", "bottom"}:
            slots["full"] = []
        elif anchor["slot"] == "full":
            slots["top"] = []
    shoes = slots["shoes"]
    if not shoes:
        return []
    bases = [list(parts) for parts in product(slots["top"], slots["bottom"], shoes)]
    bases += [list(parts) for parts in product(slots["full"], shoes)]
    # 可选层不改变基础组合数，最多两件外套候选及两件明确配饰候选参与扩展。
    outers = slots["outer"][:2]
    if visual_aesthetics.active(profile):
        open_layers = _recall([it for it in pool if it.get("slot") == "top" and garment_profile.can_open_layer(it)], profile, preference_score, context=pool)[:2]
        if open_anchor and anchor not in open_layers:
            open_layers = [anchor, *open_layers[:1]]
        if open_anchor and anchor not in slots["top"]:
            slots["top"].append(anchor)
            bases += [[anchor, bottom, shoe] for bottom, shoe in product(slots["bottom"], shoes)]
        outers = [*outers, *open_layers]
    accessories = [it for it in pool if it.get("slot") == "accessory" and it.get("type") in _ACCESSORIES]
    accessories = _recall(accessories, profile, preference_score, context=pool)[:2]
    if anchor and anchor["slot"] == "outer":
        outers = [anchor]
    if anchor and anchor["slot"] == "accessory":
        if anchor["type"] not in _ACCESSORIES:
            return []
        accessories = [anchor]
    extras = [[]] + [[it] for it in outers + accessories]
    extras += [[outer, accessory] for outer, accessory in zip(outers, accessories)]
    result, seen = [], set()
    for base in bases:
        for extra in extras:
            items = base + extra
            ids = [it["id"] for it in items]
            if anchor_item_id and anchor_item_id not in ids:
                continue
            key = tuple(sorted(ids))
            if len(set(ids)) != len(ids) or key in seen:
                continue
            roles = garment_profile.wearing_roles(items) if visual_aesthetics.active(profile) else None
            if visual_aesthetics.active(profile) and roles is None:
                continue
            seen.add(key)
            result.append({"item_ids": ids, "items": items, "kind": "full" if base[0]["slot"] == "full" else "split", **({"wearing_roles": roles} if roles else {})})
    return result


def _color_score(items: list[dict], profile: dict) -> tuple[float | None, str, str]:
    sets = [colors_of(it) for it in items]
    known = [s for s in sets if s]
    if len(known) < 2:
        return None, "unknown", "颜色信息不足，暂不能比较整套配色。"
    labels = "、".join(taxonomy.COLORS[c] for c in sorted(set.union(*known)))
    mode = _RULES[profile["id"]]["color"]
    pairs = list(product(range(len(known)), repeat=2))
    matches = []
    for a, b in pairs:
        if a >= b:
            continue
        left, right = known[a], known[b]
        repeated = bool(left & right)
        same_family = any(left & family and right & family for family in _FAMILIES)
        contrast = bool((left & {"white", "beige", "light_blue"} and right & {"black", "navy", "brown", "burgundy"}) or
                        (right & {"white", "beige", "light_blue"} and left & {"black", "navy", "brown", "burgundy"}))
        if mode == "family":
            value = 1. if repeated or same_family else .65
        elif mode == "contrast":
            value = 1. if contrast or repeated else .65
        elif mode == "tonal":
            value = 1. if repeated else .95 if same_family else .45
        elif mode == "heritage":
            palette = _RULES[profile["id"]]["palette"]
            value = .95 if left & palette and right & palette else .7 if repeated or same_family else .5
        elif mode == "soft":
            palette = _RULES[profile["id"]]["palette"]
            value = .95 if left & palette and right & palette else .65 if repeated else .45
        elif mode == "accent":
            value = 1. if repeated or contrast else .65
        else:
            raise ValueError("未配置的配色策略")
        matches.append(value)
    score = sum(matches) / len(matches)
    relation_text = ""
    if mode == "accent":
        # 比较重点与底色的关系；增加颜色数量不增加得分。
        neutral = {"black", "white", "gray", "beige", "navy", "brown", "khaki", "silver"}
        accent = [colors - neutral for colors in known if colors - neutral]
        base = sum(bool(colors & neutral) and not colors - neutral for colors in known)
        coherent = bool(accent) and bool(set.intersection(*accent))
        score = .95 if base and coherent else .7 if base and accent else .5
        relation_text = "有基础色与共同重点色线索；" if base and coherent else "基础色与重点色的关系仍需调整或确认；"
    status = "unknown" if len(known) < len(items) else "matched" if score >= .8 else "approximate"
    return score, status, f"已知颜色为{labels}；{relation_text}按颜色标签比较呼应，实际明度与饱和度待确认。"


def _pattern_score(patterns: list[str], mode: str) -> float | None:
    if not patterns:
        return None
    patterned = sum(pattern != "solid" for pattern in patterns)
    if mode == "relaxed":
        return .9 if "solid" in patterns else .7
    if mode == "focal":
        return .95 if not patterned or patterned == 1 and "solid" in patterns else .65
    if mode == "tonal":
        return .95 if not patterned else .7 if "solid" in patterns else .6
    if mode == "heritage":
        return sum(.95 if p in {"solid", "plaid", "striped"} else .6 for p in patterns) / len(patterns)
    if mode == "street":
        return .98 if patterns.count("graphic") == 1 and "solid" in patterns else .8 if not patterned else .7
    if mode == "soft":
        return sum(.95 if p in {"solid", "floral", "dotted"} else .65 for p in patterns) / len(patterns)
    raise ValueError("未配置的图案策略")


def _relation(items: list[dict], mode: str) -> tuple[float | None, str]:
    if mode == "none":
        return None, ""
    if mode in {"urban", "natural", "street_v2", "technical", "refined"}:
        return _whole_relation(items, mode)
    if mode == "sport_mix":
        sporty = [it for it in items if it.get("type") in {"hoodie", "sweatpants", "sneakers"} or "sporty" in (it.get("style") or [])]
        basics = [it for it in items if it.get("type") in {"shirt", "pants", "blazer", "coat", "skirt", "dress", "loafers"} and it not in sporty]
        mixed = any(a.get("slot") != b.get("slot") for a in sporty for b in basics)
        return (1. if mixed else .45), f"运动线索{len(sporty)}件、其他基础款线索{len(basics)}件；" + ("有跨角色混搭线索。" if mixed else "暂未形成明确的跨角色混搭。")
    if mode == "heritage":
        roles = {it.get("slot") for it in items if it.get("type") in {"shirt", "jeans", "casual_pants", "denim_jacket", "jacket", "boots"} or "vintage" in (it.get("style") or [])}
        return (.95 if len(roles) >= 2 else .5), f"复古/工装相关品类或标签覆盖{len(roles)}个穿衣角色；具体口袋、做旧等设计待确认。"
    if mode == "street":
        wide = any(it.get("slot") in {"top", "bottom", "full", "outer"} and it.get("fit") in {"oversized", "boxy", "wide_leg"} for it in items)
        shoe = any(it.get("slot") == "shoes" and it.get("type") in {"sneakers", "boots"} for it in items)
        return (.95 if wide and shoe else .5), "宽大衣服与球鞋/靴" + ("有品类和版型上的衔接线索。" if wide and shoe else "的衔接线索不足，可先作近似尝试。")
    raise ValueError("未配置的整套关系策略")


def _whole_relation(items: list[dict], mode: str) -> tuple[float, str]:
    """§34：比较衣服之间的关系；品类线索不证明触感、鞋底厚度或人体比例。"""
    clothing = [it for it in items if it.get("slot") in {"top", "bottom", "full", "outer"}]
    shoes = [it for it in items if it.get("slot") == "shoes"]
    known = [it for it in clothing if it.get("fit") in taxonomy.FITS]
    if mode == "technical":
        active = [it for it in items if "sporty" in (it.get("style") or [])
                  or it.get("type") in {"sun_protection_jacket", "sweatpants"}]
        roles = {it.get("slot") for it in active if it.get("slot") in taxonomy.SLOTS}
        non_shoe = roles - {"shoes", "accessory"}
        score = .95 if non_shoe and len(roles) >= 2 else .65 if non_shoe else .5
        return score, f"明确运动标签或功能品类覆盖{len(roles)}个角色；单有球鞋不足以确认机能关系，实际功能与厚度待确认。"
    if mode == "street_v2":
        wide = [it for it in known if it["fit"] in {"oversized", "boxy", "wide_leg", "loose"}]
        paired = bool(wide) and any(it.get("type") in {"sneakers", "boots"} for it in shoes)
        return (.95 if paired else .5), "已知宽大版型与球鞋/靴" + ("有跨角色衔接线索；" if paired else "的跨角色联系不足；") + "实际鞋子体量、衣长与露出顺序待确认。"
    if mode == "natural":
        relaxed = [it for it in known if it["fit"] in {"loose", "straight", "wide_leg", "regular"}]
        roles = {it.get("slot") for it in relaxed}
        connected = {"top", "bottom"} <= roles or "full" in roles
        casual_shoe = any(it.get("type") in {"sneakers", "loafers", "sandals"} for it in shoes)
        return (.95 if connected and casual_shoe else .7 if connected else .5), f"舒展版型覆盖{len(roles)}个衣物角色；" + ("与休闲鞋类相接。" if connected and casual_shoe else "上下轮廓与鞋服的连接仍可调整。")
    if mode == "urban":
        clear = [it for it in known if it["fit"] in {"regular", "straight", "tapered", "slim"}]
        roles = {it.get("slot") for it in clear}
        connected = {"top", "bottom"} <= roles or "full" in roles
        leather = any(it.get("type") in {"leather_shoes", "loafers"} for it in shoes)
        return (.95 if connected and leather else .7 if connected else .5), f"明确版型覆盖{len(roles)}个衣物角色；" + ("衣裤与皮鞋/乐福鞋有衔接线索。" if connected and leather else "还需确认衣裤和鞋子的利落程度。")
    if mode == "refined":
        upper = any(it.get("type") in {"shirt", "polo", "blouse", "sweater", "cardigan", "dress"} for it in clothing)
        line = any(it.get("slot") in {"bottom", "full"} and it.get("fit") in {"regular", "straight", "tapered", "wide_leg", "flared"} for it in known)
        shoe = any(it.get("type") in {"loafers", "leather_shoes", "sandals"} for it in shoes)
        return (.95 if upper and line and shoe else .7 if upper and line else .5), "衬衫/针织相关品类、下装版型与鞋类" + ("有组合线索；" if upper and line and shoe else "尚可进一步衔接；") + "细腻、垂坠与光泽属于目标，实际表现待确认。"
    raise ValueError("未配置的完整审美关系")


def assess(outfit: dict, profile: dict) -> dict:
    """五维均有目标和事实说明；无法测得的材质表现不参与评分。"""
    items = outfit["items"]
    if visual_aesthetics.active(profile):
        roles = garment_profile.wearing_roles(items) or {}
        items = [{**item, "slot": "outer"} if roles.get(item["id"]) == "upper_layer" else item for item in items]
    rules = _RULES[profile["id"]]
    clothing = [it for it in items if it.get("slot") in {"top", "bottom", "full", "outer"}]
    color = _color_score(items, profile)
    fits = [it.get("fit") for it in clothing if it.get("fit") in taxonomy.FITS]
    fit_score = sum(_FIT[profile["id"]].get(fit, .5) for fit in fits) / len(fits) if fits else None
    fit_text = "已知版型为" + "、".join(taxonomy.FITS[fit] for fit in fits) + "；实际衣长、腰线与上身比例待确认。" if fits else "版型未填写，实际轮廓与上身比例待确认。"
    fit_status = "unknown" if len(fits) < len(clothing) else "matched" if fit_score >= .75 else "approximate"
    materials = sorted({taxonomy.MATERIALS[it["material"]] for it in clothing if it.get("material") in taxonomy.MATERIALS})
    material_text = ("已标注" + "、".join(materials) + "；" if materials else "材质未填写；") + "成分不能确认表面质感、挺阔或垂坠，本维度暂不评分。"
    patterns = [it.get("pattern") for it in clothing if it.get("pattern") in taxonomy.PATTERNS and it.get("pattern") != "other"]
    patterned = sum(pattern != "solid" for pattern in patterns)
    pattern_score = _pattern_score(patterns, rules["pattern"])
    pattern_text = f"已知图案中有{patterned}件非纯色、{patterns.count('solid')}件纯色；图案大小、密度和视觉强度待确认。" if patterns else "图案未填写，暂不能判断图案之间的关系。"
    pattern_status = "unknown" if len(patterns) < len(clothing) else "matched" if pattern_score >= .8 else "approximate"
    outer = sum(it.get("slot") == "outer" for it in items)
    accessory = sum(it.get("slot") == "accessory" for it in items)
    layers = outer + accessory
    if rules["layer"] == "optional":
        layer_score = .85 if layers else .65
    elif rules["layer"] == "sparse":
        layer_score = .9 if layers <= 1 else .7
    else:
        raise ValueError("未配置的层次策略")
    layer_text = f"基础套装另加{outer}件外套、{accessory}件配饰；" if layers else "使用基础套装；"
    layer_text += "内外层余量和领口袖口的实际呈现需试穿确认。"
    relation_score, relation_text = _relation(items, rules["relation"])
    if relation_score is not None:
        layer_score = .3 * layer_score + .7 * relation_score
        layer_text += relation_text
    values = {
        "color": color,
        "silhouette": (fit_score, fit_status, fit_text),
        "material": (None, "unknown", material_text),
        "pattern": (pattern_score, pattern_status, pattern_text),
        "layering": (layer_score, "matched" if layer_score >= .8 else "approximate", layer_text),
    }
    weights = rules["weights"]
    if visual_aesthetics.active(profile):
        values = visual_aesthetics.refine(values, items, profile)
        weights = {**weights, "material": 2}
        if "upper_layer" in (outfit.get("wearing_roles") or {}).values():
            score, status, text = values["layering"]
            values["layering"] = (score, "approximate", text + " 可按一内一外尝试敞开叠穿，实际余量需试穿确认。")
    # 缺失线索保持中性；不能删掉分母后让单一层次线索变成整套高匹配。
    total = sum(weight * (values[key][0] if values[key][0] is not None else .5) for key, weight in weights.items())
    weight = sum(weights.values())
    dimensions = [{"key": key, "label": label, "status": values[key][1], "evidence": values[key][2]} for key, label in DIMENSIONS]
    return {"score": total / weight if weight else .5, "dimensions": dimensions,
            "approximate": any(d["status"] == "approximate" for d in dimensions),
            "note": "依据已填写属性作搭配尝试；未核实实际上身效果。"}


def core_ids(outfit: dict) -> set[str]:
    return {it["id"] for it in outfit["items"] if it.get("slot") in {"top", "bottom", "full"}
        and outfit.get("wearing_roles", {}).get(it["id"]) != "upper_layer"}


def shortlist(candidates: list[dict], count: int) -> list[dict]:
    """先覆盖核心组合，再填变化；正式度和颜色种数不强制不同。"""
    ranked = sorted(candidates, key=lambda outfit: -outfit["_relevance"])
    distinct, remainder, seen = [], [], set()
    for outfit in ranked:
        key = tuple(sorted(core_ids(outfit)))
        if key in seen:
            remainder.append(outfit)
        else:
            seen.add(key)
            distinct.append(outfit)
    selected = []
    while distinct and len(selected) < count:
        def value(outfit):
            core = core_ids(outfit)
            overlap = max((len(core & core_ids(other)) / max(1, len(core | core_ids(other))) for other in selected), default=0.)
            return .7 * outfit["_relevance"] - .3 * overlap
        best = max(distinct, key=value)
        selected.append(best)
        distinct.remove(best)
    return selected + remainder[:max(0, count - len(selected))]
