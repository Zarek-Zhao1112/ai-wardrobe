"""§17：防晒衣独立品类、未知留空与旧外套兼容；模型均为假响应。"""
import pytest
from fastapi.testclient import TestClient

from app import config, db, taxonomy
from app.main import app
from app.models import ItemCreate, ItemUpdate
from app.services import images, items, tagging, wardrobe

SUN = "sun_protection_jacket"


@pytest.mark.parametrize("raw", [
    SUN, "SUN-PROTECTION-JACKET", "防晒衣", "防晒服", "防晒外套", "骆驼轻薄防晒衣",
])
def test_sun_names_resolve_to_an_outer_layer(raw):
    key = taxonomy.resolve_type(raw)
    assert key == SUN
    assert taxonomy.slot_of(key) == "outer"
    assert taxonomy.label_of(key) == "防晒衣"
    assert not taxonomy.supports_length(key)


@pytest.mark.parametrize(("raw", "expected"), [
    ("防晒T恤", "tshirt"), ("防晒帽子", "hat"), ("防晒长裤", "casual_pants"),
    ("UPF50+防晒透气夹克", "jacket"), ("防紫外线衬衫", "shirt"),
    ("冲锋衣", "jacket"), ("皮衣", "jacket"), ("防晒", "other"), ("UPF50+", "other"),
])
def test_marketing_words_do_not_turn_other_clothes_into_sun_jackets(raw, expected):
    assert taxonomy.resolve_type(raw) == expected


@pytest.fixture
def sun_context(uid, tmp_path, monkeypatch):
    monkeypatch.setattr(db, "IMAGES_DIR", tmp_path)
    monkeypatch.setattr(config, "VISION_READY", True)
    made = []

    def create(type_key, **fields):
        row = items.create(uid, ItemCreate(type=type_key, name="防晒品类回归", **fields))
        made.append(row["id"])
        return row

    with TestClient(app) as client:
        yield client, create, made
    for item_id in made:
        items.delete(item_id, uid)


@pytest.mark.parametrize("raw_type", ["", " ", None, "other", "飞行未知服"])
def test_uncertain_type_is_absent_while_known_attributes_survive(fake_llm, tmp_path, raw_type):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"\xff\xd8\xfffixture")
    raw = {"type": raw_type, "colors": ["gray"], "material": "nylon", "length": "full"}
    result = tagging.tag_image(photo, client=fake_llm([raw]))
    assert not ({"type", "slot", "length"} & result["tags"].keys())
    assert result["conflicts"]["type"]
    assert result["tags"]["colors"] == ["gray"]
    assert result["tags"]["material"] == "nylon"
    assert result["raw"] == raw


def test_missing_type_from_model_also_requests_a_manual_choice(fake_llm, tmp_path):
    photo = tmp_path / "photo.png"
    photo.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    result = tagging.tag_image(photo, client=fake_llm([{"colors": ["gray"]}]))
    assert "type" not in result["tags"]
    assert result["conflicts"]["type"]
    assert result["tags"]["primary_color"] == "gray"


def test_named_sun_jacket_is_normalized_from_dual_image_response(fake_llm, tmp_path):
    photo = tmp_path / "crop.png"
    original = tmp_path / "order.jpg"
    photo.write_bytes(b"\x89PNG\r\n\x1a\ncropped fixture")
    original.write_bytes(b"\xff\xd8\xfforiginal fixture")
    model = fake_llm([{"type": "防晒衣", "colors": ["gray"], "season": ["summer"]}])
    result = tagging.tag_image(photo, client=model, reference_image=original, reference_crop={"x": .1, "y": .2, "w": .3, "h": .4})
    assert result["tags"]["type"] == SUN
    assert result["tags"]["slot"] == "outer"
    assert result["conflicts"] == {}
    assert sum(p["type"] == "image_url" for p in model.calls[0]["messages"][1]["content"]) == 2


def test_conflicting_sun_type_is_withheld_with_its_reason(fake_llm, tmp_path):
    photo = tmp_path / "photo.png"
    photo.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    result = tagging.tag_image(photo, client=fake_llm([{
        "type": SUN, "colors": ["gray"], "conflicts": {"type": "防晒衣标题属于旁边另一件商品"},
    }]))
    assert not ({"type", "slot"} & result["tags"].keys())
    assert result["conflicts"]["type"] == "防晒衣标题属于旁边另一件商品"
    assert result["tags"]["colors"] == ["gray"]


def test_new_category_is_available_to_create_edit_and_filter(sun_context, uid):
    client, create, made = sun_context
    assert client.get("/taxonomy").json()["types"][SUN] == {"label": "防晒衣", "slot": "outer"}
    old = create("jacket")
    created = client.post("/items", json={"type": "防晒服", "name": "新防晒衣"})
    assert created.status_code == 200, created.text
    sun = created.json()["item"]
    made.append(sun["id"])
    assert (sun["type"], sun["slot"]) == (SUN, "outer")
    filtered = items.list_items(uid, type_key="防晒衣")["items"]
    assert sun["id"] in {row["id"] for row in filtered}
    assert old["id"] not in {row["id"] for row in filtered}
    changed = items.update(old["id"], ItemUpdate(type="防晒衣"), user_id=uid)
    assert (changed["type"], changed["slot"]) == (SUN, "outer")
    restored = items.update(old["id"], ItemUpdate(type="jacket"), user_id=uid)
    assert (restored["type"], restored["slot"]) == ("jacket", "outer")


@pytest.mark.parametrize("overwrite", [False, True])
def test_uncertain_recognition_does_not_erase_existing_jacket(sun_context, uid, fake_llm, overwrite):
    _, create, _ = sun_context
    row = create("jacket", material="cotton")
    images.save_image(uid, row["id"], b"\x89PNG\r\n\x1a\nold jacket")
    model = fake_llm([{"type": "", "colors": ["gray"], "conflicts": {"type": "薄外套品类看不准"}}])
    result = tagging.tag_and_apply(uid, row["id"], client=model, overwrite=overwrite)
    current = items.get(row["id"], uid)
    assert (current["type"], current["slot"], current["material"]) == ("jacket", "outer", "cotton")
    assert current["colors"] == ["gray"]
    assert result["conflicts"]["type"] == "薄外套品类看不准"


def test_sun_jacket_is_optional_in_combinations_and_included_in_item_preview(sun_context, uid):
    _, create, _ = sun_context
    base = [create(key) for key in ("tshirt", "casual_pants", "sneakers")]
    sun = create(SUN, season=["summer"])
    before = taxonomy.count_combinations(base, "summer")
    after = taxonomy.count_combinations(base + [sun], "summer")
    assert before["total_combinations"] == after["total_combinations"] == 1
    assert after["counts"]["outer"] == 1
    samples = wardrobe.sample_outfits_with(uid, sun["id"], season="summer")
    assert samples
    assert all(sun["id"] in sample["item_ids"] for sample in samples)
    assert all(len(sample["item_ids"]) == 4 for sample in samples)
