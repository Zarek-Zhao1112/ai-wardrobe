"""§35：六套v2审美使用原图线索；固定分母，未知保持中性。"""
from . import garment_profile as gp

TARGETS = {
    "urban": {"chroma": {"muted", "moderate"}, "volume": {"fitted", "regular"}, "silhouette": {"straight", "tapered"}, "body_length": {"cropped", "regular"}, "pattern_scale": {"none", "small"}, "pattern_density": {"sparse"}, "surface": {"smooth", "woven"}, "sheen": {"matte", "slight"}},
    "natural": {"chroma": {"muted", "moderate"}, "volume": {"regular", "loose"}, "silhouette": {"straight", "flared"}, "body_length": {"regular"}, "pattern_scale": {"none", "small", "medium"}, "pattern_density": {"sparse", "moderate"}, "surface": {"woven", "knit", "pile", "ribbed"}, "sheen": {"matte"}},
    "heritage_v2": {"chroma": {"muted", "moderate"}, "volume": {"regular", "loose"}, "silhouette": {"straight", "boxy"}, "body_length": {"cropped", "regular"}, "pattern_scale": {"none", "small", "medium"}, "pattern_density": {"sparse", "moderate"}, "surface": {"denim", "woven", "pile", "ribbed"}, "sheen": {"matte"}},
    "street_v2": {"chroma": {"muted", "moderate", "bright"}, "volume": {"loose", "oversized"}, "silhouette": {"straight", "boxy", "flared"}, "body_length": {"regular", "long"}, "pattern_scale": {"none", "medium", "large"}, "pattern_density": {"sparse", "moderate", "dense"}, "surface": {"smooth", "denim", "knit"}, "sheen": {"matte", "slight"}},
    "technical": {"chroma": {"muted", "moderate"}, "volume": {"regular", "loose"}, "silhouette": {"straight", "tapered"}, "body_length": {"cropped", "regular"}, "pattern_scale": {"none", "small"}, "pattern_density": {"sparse"}, "surface": {"smooth", "woven", "quilted"}, "sheen": {"matte", "slight"}},
    "refined": {"chroma": {"muted", "moderate"}, "volume": {"regular", "loose"}, "silhouette": {"straight", "flared", "a_line"}, "body_length": {"cropped", "regular"}, "pattern_scale": {"none", "small"}, "pattern_density": {"sparse"}, "surface": {"smooth", "knit", "woven"}, "sheen": {"matte", "slight"}},
}
DIMENSION_FIELDS = {"color": ("chroma",), "silhouette": ("volume", "silhouette", "body_length"),
    "pattern": ("pattern_scale", "pattern_density"), "material": ("surface", "sheen")}


def active(profile):
    return profile.get("id") in TARGETS and profile.get("version", 1) >= 2


def affinity(item, profile):
    targets = TARGETS[profile["id"]]
    attrs = (gp.for_item(item) or {}).get("attributes", {})
    return sum(.5 if key not in attrs else 1. if attrs[key]["value"] in accepted else .25
        for key, accepted in targets.items()) / len(targets)


def refine(values, items, profile):
    """每维的缺失字段占中性值；不以已知项数量改变分母。"""
    targets = TARGETS[profile["id"]]
    clothing = [it for it in items if it.get("slot") in {"top", "bottom", "full", "outer"}]
    result = dict(values)
    for dimension, fields in DIMENSION_FIELDS.items():
        pool = items if dimension == "color" else clothing
        if not pool:
            continue
        scores, known, descriptions = [], [], []
        for item in pool:
            attrs = (gp.for_item(item) or {}).get("attributes", {})
            for key in fields:
                entry = attrs.get(key)
                scores.append(.5 if not entry else 1. if entry["value"] in targets[key] else .25)
                if entry:
                    known.append(entry)
                    label = gp.VOCAB[key][entry["value"]]
                    if label not in descriptions:
                        descriptions.append(label)
        baseline, status, text = values[dimension]
        score = sum(scores) / len(scores)
        if dimension == "material":
            score = score if known else None
            status = "approximate" if known else "unknown"
            text = ("可见表面线索为" + "、".join(descriptions) + "；" if known else "表面外观尚未确认；") + "成分不能确认外观，外观也不能证明成分、厚薄、触感、功能或垂坠。"
        else:
            score = .7 * (baseline if baseline is not None else .5) + .3 * score
            if known:
                status = "approximate"
                text += " 原图/商品资料的补充线索：" + "、".join(descriptions) + "；实际效果仍需确认。"
        result[dimension] = (score, status, text)
    return result
