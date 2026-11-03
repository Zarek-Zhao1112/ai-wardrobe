"""§35：与原始衣图绑定的搭配线索；未知留空，不把生成图当事实。"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import re

from .. import taxonomy

KEY = "visual_profile"
VERSION = 1
SOURCES = {"image", "product_text"}
CERTAINTIES = {"clear", "approximate"}
VOCAB = {
    "tone": {"light": "偏浅", "medium": "中等明暗", "dark": "偏深"},
    "chroma": {"muted": "偏柔和", "moderate": "鲜艳程度适中", "bright": "偏鲜艳"},
    "volume": {"fitted": "贴身", "regular": "适中余量", "loose": "偏宽松", "oversized": "大廓形"},
    "silhouette": {"straight": "直身线条", "boxy": "箱形轮廓", "tapered": "收口线条", "flared": "向下展开", "a_line": "A字轮廓"},
    "body_length": {"cropped": "短款", "regular": "常规衣长", "long": "长款"},
    "neckline": {"crew": "圆领", "v_neck": "V领", "collared": "翻领", "hooded": "连帽", "high_neck": "高领", "open_neck": "开放领口"},
    "sleeve_length": {"sleeveless": "无袖", "short": "短袖", "elbow": "及肘袖", "three_quarter": "七分袖", "long": "长袖"},
    "closure": {"pullover": "套头", "buttons": "纽扣开合", "zip": "拉链开合", "open_front": "敞开门襟", "lace": "系带"},
    "pattern_scale": {"none": "无明显图案", "small": "小面积图案/标识", "medium": "中等面积图案", "large": "大面积图案"},
    "pattern_density": {"sparse": "图案疏朗", "moderate": "图案密度适中", "dense": "图案密集"},
    "surface": {"smooth": "表面较平整", "woven": "可见织纹", "knit": "可见针织纹理", "pile": "绒面样外观", "denim": "洗水/牛仔样纹理", "ribbed": "罗纹纹理", "quilted": "绗缝纹理"},
    "sheen": {"matte": "偏哑光", "slight": "轻微光泽", "glossy": "光泽明显"},
    "wearing_options": {"single": "单穿", "inner": "作内搭", "open_layer": "可尝试敞开叠穿"},
}
UPPER = {"top", "outer", "full"}
OPEN_TYPES = {"shirt", "cardigan", "vest"}
IMAGE_APPROXIMATE = {"tone", "chroma", "volume", "silhouette", "surface", "sheen", "wearing_options"}
_HASH = re.compile(r"sha256:[a-f0-9]{32}\Z")


def schema() -> dict:
    properties = {}
    for key, vocab in VOCAB.items():
        value = {"type": "array", "items": {"type": "string", "enum": list(vocab)}, "maxItems": 3} if key == "wearing_options" else {"type": "string", "enum": list(vocab)}
        properties[key] = {"type": "object", "properties": {"value": value,
            "source": {"type": "string", "enum": sorted(SOURCES)},
            "certainty": {"type": "string", "enum": sorted(CERTAINTIES)}},
            "required": ["value", "source", "certainty"], "additionalProperties": False}
    return {"type": "object", "properties": {"attributes": {"type": "object", "properties": properties, "additionalProperties": False}},
        "required": ["attributes"], "additionalProperties": False}


def normalize(raw, kind, *, image_hash=None, blocked=(), basic=None) -> dict | None:
    """仅保留受控线索；本次识别的品类/图片指纹由调用方绑定。"""
    if not isinstance(raw, dict) or kind not in taxonomy.CLOTHING_TYPES or kind == "other":
        return None
    attrs = raw.get("attributes")
    if not isinstance(attrs, dict):
        return None
    slot = taxonomy.slot_of(kind)
    out = {}
    blocked = set(blocked)
    for key, vocab in VOCAB.items():
        entry = attrs.get(key)
        if key in blocked or not isinstance(entry, dict):
            continue
        value, source, certainty = (entry.get(k) for k in ("value", "source", "certainty"))
        if not isinstance(source, str) or source not in SOURCES or not isinstance(certainty, str) or certainty not in CERTAINTIES:
            continue
        if key == "wearing_options":
            if slot != "top" or not isinstance(value, list) or len(value) > 3 or any(not isinstance(v, str) or v not in vocab for v in value):
                continue
            value = list(dict.fromkeys(value))
            if not value:
                continue
        elif not isinstance(value, str) or value not in vocab:
            continue
        if key in {"volume", "silhouette"} and slot not in UPPER | {"bottom"}:
            continue
        if key in {"neckline", "sleeve_length"} and slot not in UPPER:
            continue
        if key == "body_length" and (slot not in {"top", "outer"} or source != "product_text"):
            continue
        if key == "closure" and (slot not in UPPER | {"shoes"} or value == "lace" and slot != "shoes"):
            continue
        if key == "silhouette" and (value == "boxy" and slot not in {"top", "outer"} or value == "tapered" and slot != "bottom" or value == "a_line" and kind not in {"skirt", "dress"}):
            continue
        if source == "image" and key in IMAGE_APPROXIMATE:
            certainty = "approximate"
        out[key] = {"value": value, "source": source, "certainty": certainty}
    if out.get("pattern_scale", {}).get("value") == "none":
        out.pop("pattern_density", None)
    if basic:
        scale = out.get("pattern_scale", {}).get("value")
        pattern = basic.get("pattern")
        if (pattern == "solid" and scale in {"medium", "large"} or
                pattern and pattern not in {"solid", "other"} and scale == "none"):
            out.pop("pattern_scale", None); out.pop("pattern_density", None)
        expected = {"slim": "fitted", "regular": "regular", "loose": "loose", "oversized": "oversized"}.get(basic.get("fit"))
        if expected and "volume" in out and out["volume"]["value"] != expected:
            out.pop("volume")
    options = out.get("wearing_options")
    if options and "open_layer" in options["value"]:
        if kind not in OPEN_TYPES or out.get("closure", {}).get("value") not in {"buttons", "zip", "open_front"} or "volume" not in out:
            options["value"].remove("open_layer")
        if not options["value"]:
            out.pop("wearing_options")
    if not out:
        return None
    result = {"version": VERSION, "item_type": kind, "attributes": out}
    fingerprint = image_hash if image_hash is not None else raw.get("image_hash")
    if isinstance(fingerprint, str) and _HASH.fullmatch(fingerprint):
        result["image_hash"] = fingerprint
    return result


def for_item(item) -> dict | None:
    tags = item.get("tags")
    raw = tags.get(KEY) if isinstance(tags, dict) else None
    if not isinstance(raw, dict) or type(raw.get("version")) is not int or raw["version"] != VERSION or raw.get("item_type") != item.get("type"):
        return None
    expected = item.get("image_hash")
    if not isinstance(expected, str) or not _HASH.fullmatch(expected) or raw.get("image_hash") != expected:
        return None
    return normalize(raw, item["type"], image_hash=expected, basic=item)


def value(item, key):
    return (for_item(item) or {}).get("attributes", {}).get(key, {}).get("value")


def context(item) -> dict:
    profile = for_item(item)
    return {"version": VERSION, "attributes": deepcopy(profile["attributes"])} if profile else {}


def with_profile(tags, profile):
    result = dict(tags) if isinstance(tags, dict) else {}
    result.pop(KEY, None)
    if profile:
        result[KEY] = deepcopy(profile)
    return result


def can_open_layer(item):
    return (item.get("type") in OPEN_TYPES and "open_layer" in (value(item, "wearing_options") or [])
        and value(item, "closure") in {"buttons", "zip", "open_front"} and value(item, "volume") in {"fitted", "regular", "loose", "oversized"})


def wearing_roles(items) -> dict | None:
    """每件占一位；明确的两件旧上装才按一内一外尝试，不猜新衣结构。"""
    if any(not isinstance(it.get("id"), str) for it in items) or len({it["id"] for it in items}) != len(items):
        return None
    slots = [taxonomy.slot_of(it.get("type")) for it in items]
    if "full" in slots and ({"top", "bottom"} & set(slots)):
        return None
    keys = [slot if slot != "accessory" else "accessory:" + it.get("type", "other") for slot, it in zip(slots, items)]
    counts = Counter(keys)
    if any(count > 1 for role, count in counts.items() if role != "top") or counts["top"] > 2:
        return None
    roles = dict(zip((it["id"] for it in items), slots))
    if counts["top"] <= 1:
        return roles
    if "outer" in slots:
        return None
    tops = [it for it, slot in zip(items, slots) if slot == "top"]
    rank = {"fitted": 0, "regular": 1, "loose": 2, "oversized": 3}
    valid = []
    for outer, inner in (tops, list(reversed(tops))):
        outer_volume, inner_volume = value(outer, "volume"), value(inner, "volume")
        if (can_open_layer(outer) and "inner" in (value(inner, "wearing_options") or [])
                and outer_volume in rank and inner_volume in rank and rank[outer_volume] > rank[inner_volume]):
            valid.append((outer, inner))
    if len(valid) != 1:
        return None
    roles[valid[0][0]["id"]] = "upper_layer"
    return roles
