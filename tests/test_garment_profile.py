"""§35：原图线索的边界、未知与确定性穿着位置，均为合成数据。"""
from copy import deepcopy

import pytest

from app.services import garment_profile as gp

HASH = "sha256:" + "a" * 32


def feature(value, source="image", certainty="clear"):
    return {"value": value, "source": source, "certainty": certainty}


@pytest.fixture
def owner():
    from app import db
    uid = db.new_id("u")
    with db.conn() as connection:
        connection.execute("INSERT INTO users(id,name,created_at) VALUES(?,?,?)", (uid, "衣物档案合成测试", db.now_iso()))
    return uid


def garment(kind="tshirt", **attrs):
    profile = gp.normalize({"attributes": {key: feature(value) for key, value in attrs.items()}}, kind, image_hash=HASH)
    return {"id": kind, "type": kind, "slot": "top", "image_hash": HASH, "tags": {gp.KEY: profile}}


def test_controlled_features_do_not_accept_free_text_or_generated_sources():
    raw = {"attributes": {"tone": feature("dark"), "chroma": feature("muted", "generated"),
        "volume": feature("loose", certainty="unknown"), "surface": feature("silky soft"),
        "prompt": feature("ignore instructions"), "neckline": feature("crew")}}
    value = gp.normalize(raw, "tshirt", image_hash=HASH)
    assert set(value["attributes"]) == {"tone", "neckline"}
    assert value["attributes"]["tone"]["certainty"] == "approximate"
    assert value["attributes"]["neckline"]["certainty"] == "clear"
    assert value["image_hash"] == HASH


@pytest.mark.parametrize("bad", [None, [], "dark", {"attributes": []}, {"attributes": {"tone": "dark"}}, {"attributes": {"tone": feature(True)}}, {"attributes": {"tone": feature("dark", source=[])}}])
def test_invalid_optional_profile_is_ignored(bad):
    assert gp.normalize(bad, "tshirt", image_hash=HASH) is None


@pytest.mark.parametrize("kind,attrs,expected", [
    ("tshirt", {"body_length": feature("cropped")}, set()),
    ("tshirt", {"body_length": feature("cropped", "product_text")}, {"body_length"}),
    ("jeans", {"neckline": feature("crew"), "body_length": feature("long", "product_text"), "silhouette": feature("tapered")}, {"silhouette"}),
    ("sneakers", {"volume": feature("oversized"), "closure": feature("lace")}, {"closure"}),
    ("shirt", {"silhouette": feature("a_line"), "closure": feature("lace")}, set()),
    ("dress", {"silhouette": feature("a_line"), "sleeve_length": feature("short")}, {"silhouette", "sleeve_length"}),
])
def test_kind_and_body_length_constraints(kind, attrs, expected):
    result = gp.normalize({"attributes": attrs}, kind, image_hash=HASH)
    assert set((result or {}).get("attributes", {})) == expected


def test_profile_binds_type_and_source_image_and_does_not_mutate_storage():
    item = garment(tone="dark")
    original = deepcopy(item)
    assert gp.value(item, "tone") == "dark"
    for patch in ({"type": "shirt"}, {"image_hash": "sha256:" + "b" * 32}, {"image_hash": None}):
        assert not gp.context({**item, **patch})
    newer = deepcopy(item); newer["tags"][gp.KEY]["version"] = 2
    assert gp.for_item(newer) is None and newer["tags"][gp.KEY]["version"] == 2
    assert item == original
    context = gp.context(item)
    assert set(context) == {"version", "attributes"}
    context["attributes"].clear()
    assert gp.value(item, "tone") == "dark"


def test_namespace_update_preserves_other_tags():
    tags = {"_photo_import": {"fingerprint": "existing"}, "custom": {"x": 1}, gp.KEY: {"old": True}}
    before = deepcopy(tags)
    result = gp.with_profile(tags, garment(tone="dark")["tags"][gp.KEY])
    assert result["_photo_import"] == tags["_photo_import"] and result["custom"] == tags["custom"]
    assert gp.KEY not in gp.with_profile(tags, None)
    assert tags == before


def test_open_layer_requires_structure_and_two_items_with_known_relative_volume():
    inner = garment(volume="regular", wearing_options=["single", "inner"])
    outer = garment("shirt", volume="loose", closure="buttons", wearing_options=["single", "open_layer"])
    pants = {"id": "pants", "type": "jeans"}; shoe = {"id": "shoe", "type": "sneakers"}
    items = [outer, pants, shoe, inner]
    roles = gp.wearing_roles(items)
    assert roles == {"shirt": "upper_layer", "pants": "bottom", "shoe": "shoes", "tshirt": "top"}
    assert gp.wearing_roles(list(reversed(items))) == roles
    unknown = deepcopy(inner); unknown["tags"] = {}
    assert gp.wearing_roles([outer, unknown, pants, shoe]) is None
    same_volume = garment(volume="loose", wearing_options=["inner"])
    assert gp.wearing_roles([outer, same_volume, pants, shoe]) is None
    assert gp.wearing_roles([outer, inner, {"id": "coat", "type": "coat"}]) is None
    assert gp.wearing_roles([outer, inner, {"id": "dress", "type": "dress"}]) is None
    assert gp.wearing_roles([inner, inner]) is None


@pytest.mark.parametrize("kind", ["tshirt", "hoodie", "coat", "sneakers"])
def test_model_cannot_assign_arbitrary_outer_layer(kind):
    item = garment(kind, volume="loose", closure="buttons", wearing_options=["single", "open_layer"])
    assert not gp.can_open_layer(item)


def test_blocked_color_features_are_not_accepted():
    result = gp.normalize({"attributes": {"tone": feature("dark"), "chroma": feature("muted"), "neckline": feature("crew")}}, "tshirt", image_hash=HASH, blocked={"tone", "chroma"})
    assert set(result["attributes"]) == {"neckline"}


def test_contradicting_pattern_or_volume_stays_unknown():
    raw = {"attributes": {"pattern_scale": feature("none"), "pattern_density": feature("dense")}}
    assert set(gp.normalize(raw, "tshirt")["attributes"]) == {"pattern_scale"}
    raw["attributes"]["pattern_scale"] = feature("large")
    assert gp.normalize(raw, "tshirt", basic={"pattern": "solid"}) is None
    raw = {"attributes": {"volume": feature("oversized"), "closure": feature("buttons"), "wearing_options": feature(["open_layer"])}}
    value = gp.normalize(raw, "shirt", basic={"fit": "slim"})
    assert set(value["attributes"]) == {"closure"}


def test_plain_roles_keep_real_conflicts_and_distinct_accessories():
    assert gp.wearing_roles([{"id": "a", "type": "bag"}, {"id": "b", "type": "hat"}])
    for kinds in [("jeans", "pants"), ("sneakers", "boots"), ("dress", "tshirt"), ("dress", "jeans"), ("shirt", "tshirt"), ("bag", "bag")]:
        assert gp.wearing_roles([{"id": str(i), "type": kind} for i, kind in enumerate(kinds)]) is None


def test_recognition_binds_exact_sent_bytes_and_keeps_optional_unknowns(tmp_path):
    from app.services import images, tagging
    path = tmp_path / "original.png"; path.write_bytes(b"original source bytes")
    class Model:
        def chat_json(self, messages, **kwargs):
            assert "visual_profile" in kwargs["schema"]["properties"]
            assert "生成的平铺图不作为识别事实" in messages[0]["content"]
            path.write_bytes(b"changed while model was running")
            return {"type": "tshirt", "primary_color": "black", "tags": {"unsafe": "not forwarded"},
                "visual_profile": {"attributes": {"tone": feature("dark"), "surface": feature("smooth"), "body_length": feature("long")}}}
    result = tagging.tag_image(path, client=Model())
    profile = result["tags"]["tags"][gp.KEY]
    assert profile["image_hash"] == images.content_hash(b"original source bytes")
    assert set(profile["attributes"]) == {"tone", "surface"}
    assert set(result["tags"]["tags"]) == {gp.KEY}


@pytest.mark.parametrize("key", ["volume", "visual_profile.volume", "visual_profile.attributes.volume"])
def test_conflicting_structure_is_never_a_layering_fact(key, tmp_path):
    from app.services import tagging
    path = tmp_path / "source.png"; path.write_bytes(b"synthetic original")
    class Model:
        def chat_json(self, *args, **kwargs):
            return {"type": "shirt", "conflicts": {key: "宽松度无法确认"},
                "visual_profile": {"attributes": {"volume": feature("loose"), "closure": feature("buttons"), "wearing_options": feature(["open_layer"])}}}
    result = tagging.tag_image(path, client=Model())
    attrs = result["tags"]["tags"][gp.KEY]["attributes"]
    assert "volume" not in attrs and "wearing_options" not in attrs
    assert result["conflicts"]["visual_profile.volume"] == "宽松度无法确认"


def test_photo_commit_and_retry_keep_profile_and_import_marker(owner, tmp_path, monkeypatch):
    uid = owner
    from app import db
    from app.models import ItemCreate
    from app.services import images, photo_import
    from uuid import uuid4
    monkeypatch.setattr(db, "IMAGES_DIR", tmp_path)
    data = b"\x89PNG\r\n\x1a\nprofile-photo-test"
    profile = gp.normalize({"attributes": {"tone": feature("dark")}}, "tshirt", image_hash=images.content_hash(data))
    payload = ItemCreate(type="tshirt", tags={gp.KEY: profile, "custom": {"kept": True}})
    token = uuid4().hex
    first = photo_import.commit(uid, token, payload, data, display_data=data)
    again = photo_import.commit(uid, token, payload, data, display_data=data)
    assert gp.value(first["item"], "tone") == "dark"
    assert first["item"]["tags"]["custom"] == {"kept": True}
    assert "_photo_import" in first["item"]["tags"]
    assert again["replayed"] and again["item"]["tags"] == first["item"]["tags"]


def test_tag_apply_merges_namespace_and_only_fills_missing_profile_attributes(owner):
    uid = owner
    from app.models import ItemCreate
    from app.services import items, tagging
    existing = garment(tone="dark")["tags"][gp.KEY]
    old = items.create(uid, ItemCreate(type="tshirt", image_hash=HASH, tags={gp.KEY: existing, "_photo_import": {"fingerprint": "old"}, "custom": 123}))
    fresh = gp.normalize({"attributes": {"tone": feature("light"), "neckline": feature("crew")}}, "tshirt", image_hash=HASH)
    result = {"tags": {"tags": {gp.KEY: fresh}}, "profile_attempted": True, "profile_image_hash": HASH}
    filtered = tagging._only_blank(result["tags"], old)
    assert set(filtered["tags"][gp.KEY]["attributes"]) == {"neckline"}
    tagging.apply_tags(uid, old["id"], {**result, "tags": filtered}, profile_fill_only=True)
    saved = items.get(old["id"], uid)
    assert gp.value(saved, "tone") == "dark" and gp.value(saved, "neckline") == "crew"
    assert saved["tags"]["_photo_import"] == {"fingerprint": "old"} and saved["tags"]["custom"] == 123
    tagging.apply_tags(uid, old["id"], {"tags": {"primary_color": "black"}, "profile_attempted": True, "profile_image_hash": HASH})
    saved = items.get(old["id"], uid)
    assert gp.KEY not in saved["tags"] and saved["tags"]["custom"] == 123


def test_late_old_image_response_does_not_overwrite_current_item(owner):
    uid = owner
    from app.models import ItemCreate
    from app.services import items, tagging
    item = items.create(uid, ItemCreate(type="tshirt", primary_color="black", image_hash=HASH, tags=garment(tone="dark")["tags"]))
    before = deepcopy(item)
    assert tagging.apply_tags(uid, item["id"], {"tags": {"primary_color": "white"}, "profile_attempted": True, "profile_image_hash": "sha256:" + "b" * 32}) is None
    assert items.get(item["id"], uid) == before


def test_fill_only_keeps_existing_structure_before_validating_dependent_wearing_option(owner):
    from app.models import ItemCreate
    from app.services import items, tagging
    old = garment("shirt", volume="loose", closure="buttons")
    item = items.create(owner, ItemCreate(type="shirt", image_hash=HASH, tags=old["tags"]))
    new = garment("shirt", volume="loose", closure="buttons", wearing_options=["open_layer"])["tags"][gp.KEY]
    missing = tagging._only_blank({"tags": {gp.KEY: new}}, item)
    assert set(missing["tags"][gp.KEY]["attributes"]) == {"wearing_options"}
    tagging.apply_tags(owner, item["id"], {"tags": missing, "profile_image_hash": HASH}, profile_fill_only=True)
    assert gp.can_open_layer(items.get(item["id"], owner))


def test_unread_future_profile_preserved_during_ai_fill(owner):
    uid = owner
    from app.models import ItemCreate
    from app.services import items, tagging
    future = {"version": 2, "opaque": {"keep": True}}
    item = items.create(uid, ItemCreate(type="tshirt", image_hash=HASH, tags={gp.KEY: future}))
    incoming = {"tags": {gp.KEY: garment(tone="dark")["tags"][gp.KEY]}}
    assert tagging._only_blank(incoming, item) == {}
    tagging.apply_tags(uid, item["id"], {"tags": {**incoming, "primary_color": "black"}, "profile_attempted": True, "profile_image_hash": HASH})
    assert items.get(item["id"], uid)["tags"][gp.KEY] == future


def test_profile_changes_recall_and_whole_assessment_but_unknown_stays_neutral():
    from app.services import aesthetics, visual_aesthetics
    from app import taxonomy
    preferred = garment(chroma="muted", volume="loose", silhouette="straight", surface="knit", sheen="matte")
    opposite = garment(chroma="bright", volume="fitted", silhouette="boxy", surface="smooth", sheen="glossy")
    unknown = {**preferred, "tags": {}}
    profile = aesthetics.get_profile("natural")
    assert profile["version"] == 2
    assert aesthetics.item_affinity(preferred, profile) > aesthetics.item_affinity(opposite, profile)
    assert visual_aesthetics.affinity(unknown, profile) == .5
    future = deepcopy(preferred); future["tags"][gp.KEY]["version"] = 99
    assert aesthetics.item_affinity(future, profile) == aesthetics.item_affinity(unknown, profile)
    base = [{"id": "pants", "type": "jeans", "slot": "bottom"}, {"id": "shoe", "type": "sneakers", "slot": "shoes"}]
    def score(top):
        return aesthetics.assess({"items": [top, *base], "item_ids": [top["id"], "pants", "shoe"], "kind": "split"}, profile)
    a, b = score(preferred), score(opposite)
    assert a["score"] > b["score"]
    assert next(d for d in a["dimensions"] if d["key"] == "material")["status"] == "approximate"
    old = aesthetics.get_profile("relaxed")
    assert old["version"] == 1 and aesthetics.item_affinity(preferred, old) == aesthetics.item_affinity(unknown, old)


def test_candidates_use_explicit_layers_and_keep_an_open_shirt_anchor():
    from app.services import aesthetics
    inner = garment(volume="regular", wearing_options=["inner"])
    outer = garment("shirt", volume="loose", closure="buttons", wearing_options=["open_layer"])
    base = [{"id": "pants", "type": "jeans", "slot": "bottom"}, {"id": "shoe", "type": "sneakers", "slot": "shoes"}]
    profile = aesthetics.get_profile("natural")
    options = aesthetics.build_candidates([outer, inner, *base], profile, "spring", lambda _: 0, anchor_item_id="shirt")
    assert options and all("shirt" in option["item_ids"] for option in options)
    assert any(option["wearing_roles"].get("shirt") == "upper_layer" for option in options)
    layered = next(option for option in options if option["wearing_roles"].get("shirt") == "upper_layer")
    assert aesthetics.core_ids(layered) == {"tshirt", "pants"}
    legacy = aesthetics.build_candidates([outer, inner, *base], aesthetics.get_profile("relaxed"), "spring", lambda _: 0)
    assert all(len(option["items"]) == 3 for option in legacy)
    outer["image_hash"] = "sha256:" + "b" * 32
    assert all(len(option["items"]) == 3 for option in aesthetics.build_candidates([outer, inner, *base], profile, "spring", lambda _: 0))


def test_purchase_context_fingerprint_and_owned_layer_validation():
    import json
    from app.services import purchase, aesthetics
    inner = {**garment(volume="regular", wearing_options=["inner"]), "name": "内搭", "primary_color": "black"}
    outer = {**garment("shirt", volume="loose", closure="buttons", wearing_options=["open_layer"]), "name": "敞开衬衫", "primary_color": "blue"}
    outer["tags"]["private"] = {"secret": "must not leave storage"}
    pool = [inner, outer]
    messages, aliases = purchase._messages(pool, aesthetics.get_profile("natural"), [])
    text = messages[-1]["content"]
    assert "visual_profile" in text and "must not leave storage" not in text and HASH not in text
    before = purchase._fingerprint(pool)
    outer["tags"]["private"] = 123
    assert purchase._fingerprint(pool) == before
    outer["tags"][gp.KEY]["attributes"]["sheen"] = feature("matte")
    assert purchase._fingerprint(pool) != before
    direction = dict(title="浅色直筒裤", type="casual_pants", primary_color="beige", color="米白颜色", fit="常规直身", texture="选织纹表面", pattern="纯色图案", layering="内搭配敞开上装", reason="搭配已有两件上装形成深浅关系", difference="", caution="实际试穿确认", owned=list(aliases), pairing="衬衫敞开搭配内搭与新裤")
    plan = dict(decision="recommend", headline="补充一条浅色裤", summary="尝试搭配已有敞开上装和内搭", directions=[direction])
    result = purchase._validate(plan, aliases)
    assert result["directions"][0]["wearing_roles"]["shirt"] == "upper_layer"
    # 新top没有可验证结构，不能靠文案绕过上装占位。
    plan["directions"][0].update(type="tshirt", owned=["w1"], layering="当敞开外层")
    with pytest.raises(purchase.PurchaseError):
        purchase._validate(plan, aliases)


def test_recommend_rechecks_profile_after_model_response(owner):
    from app.models import ItemCreate, ItemUpdate
    from app.services import items, recommend
    top = items.create(owner, ItemCreate(type="tshirt", image_hash=HASH, tags=garment(tone="dark")["tags"]))
    for kind in ("jeans", "sneakers"):
        items.create(owner, ItemCreate(type=kind))
    class Model:
        def chat_json(self, messages, **kwargs):
            prompt = messages[-1]["content"]
            assert "visual_profile" in prompt and HASH not in prompt
            items.update(top["id"], ItemUpdate(tags=garment(tone="light")["tags"]), owner)
            return {"ranked": [{"outfit": "O1", "reason": "原有深色上衣搭配长裤与运动鞋"}]}
    result = recommend.recommend(owner, season="spring", aesthetic="natural", client=Model())
    assert result["outfits"] == [] and "衣橱信息已更新" in result["note"]
