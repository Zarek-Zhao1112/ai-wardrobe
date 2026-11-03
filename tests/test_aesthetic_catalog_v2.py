"""§34：新目录改变真实选择，旧快照与未知属性保持兼容。"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import taxonomy
from app.main import app
from app.services import aesthetics
from test_aesthetic_outfits import garment

ACTIVE = {"urban", "natural", "heritage_v2", "street_v2", "technical", "refined"}
LEGACY = {"relaxed", "clean", "tonal", "heritage", "sport_mix", "street", "soft", "color_pop"}


def test_active_catalog_keeps_legacy_lookup_and_three_real_assets():
    assert aesthetics.DEFAULT == "natural"
    assert {p["id"] for p in aesthetics.list_profiles()} == ACTIVE
    for key in LEGACY:
        profile = aesthetics.get_profile(key)
        assert profile["id"] == key and profile["version"] == 1
        assert not profile.get("examples")
    web = Path(__file__).resolve().parents[1] / "web"
    images = set()
    for profile in aesthetics.list_profiles():
        assert len(profile["arrangement"]) == 6
        assert len(profile["examples"]) == 3
        for row in profile["examples"]:
            assert row["kind"] == "ai_example" and row["notes"]
            assert row["image"].startswith("/reference/aesthetics/")
            assert (web / row["image"].lstrip("/")).is_file()
            images.add(row["image"])
        assert "prompt" not in json.dumps(profile)
    assert len(images) == 18


@pytest.mark.parametrize("key,good,bad,shoe", [
    ("urban", {"type": "shirt", "fit": "regular", "style": ["minimal"]}, {"type": "hoodie", "fit": "oversized"}, "leather_shoes"),
    ("natural", {"type": "tshirt", "fit": "loose", "style": ["casual"]}, {"type": "shirt", "fit": "slim"}, "sneakers"),
    ("heritage_v2", {"type": "shirt", "fit": "straight", "style": ["vintage"]}, {"type": "blouse", "fit": "slim"}, "boots"),
    ("street_v2", {"type": "hoodie", "fit": "oversized", "style": ["street"]}, {"type": "shirt", "fit": "regular"}, "sneakers"),
    ("technical", {"type": "sweatshirt", "fit": "regular", "style": ["sporty"]}, {"type": "shirt", "fit": "oversized"}, "sneakers"),
    ("refined", {"type": "polo", "fit": "regular", "style": ["minimal"]}, {"type": "hoodie", "fit": "oversized"}, "loafers"),
])
def test_each_new_method_changes_competing_wardrobe_outfits(key, good, bad, shoe):
    def top(identifier, fields):
        fields = dict(fields)
        return garment(identifier, fields.pop("type"), primary_color="gray", pattern="solid", **fields)
    pool = [top("target", good), top("alternative", bad),
            garment("bottom", "casual_pants", fit="straight", primary_color="gray", pattern="solid"),
            garment("shoe", shoe, primary_color="black")]
    profile = aesthetics.get_profile(key)
    candidates = aesthetics.build_candidates(pool, profile, "all", lambda _: 0)
    for row in candidates:
        row["_relevance"] = aesthetics.assess(row, profile)["score"]
    winner = aesthetics.shortlist(candidates, 1)[0]
    assert "target" in winner["item_ids"]
    scores = {next(it["id"] for it in row["items"] if it["slot"] == "top"): row["_relevance"] for row in candidates}
    assert scores["target"] > scores["alternative"]
    # 两组都能入选，不把不符审美当作硬淘汰。
    assert {"target", "alternative"} <= set(scores)


def test_technical_requires_cross_role_evidence_and_not_nylon_guess():
    base = [garment("t", "tshirt", fit="regular"), garment("b", "casual_pants", fit="straight"), garment("s", "sneakers")]
    simple, _ = aesthetics._whole_relation(base, "technical")
    materials_only = [{**row, "material": "nylon"} for row in base]
    assert aesthetics._whole_relation(materials_only, "technical")[0] == simple
    coherent = [{**base[0], "style": ["sporty"]}, base[1], {**base[2], "style": ["sporty"]}]
    assert aesthetics._whole_relation(coherent, "technical")[0] > simple


def test_natural_and_urban_compare_clothing_to_shoes():
    clothes = [garment("t", "shirt", fit="regular"), garment("b", "pants", fit="straight")]
    sporty = clothes + [garment("s", "sneakers")]
    leather = clothes + [garment("s", "leather_shoes")]
    assert aesthetics._whole_relation(sporty, "natural")[0] > aesthetics._whole_relation(leather, "natural")[0]
    assert aesthetics._whole_relation(leather, "urban")[0] > aesthetics._whole_relation(sporty, "urban")[0]


def test_catalog_current_season_is_server_value(monkeypatch):
    monkeypatch.setattr(taxonomy, "current_season", lambda: "winter")
    with TestClient(app) as client:
        catalog = client.get("/outfits/aesthetics").json()
    assert catalog["season"] == "winter"
    assert catalog["season_label"] == taxonomy.SEASON_LABELS["winter"]
    assert catalog["default"] == "natural"
    assert {row["id"] for row in catalog["aesthetics"]} == ACTIVE


@pytest.mark.parametrize("mode,bad_fit", [("urban", "oversized"), ("natural", "slim")])
def test_outer_does_not_replace_bottom_in_whole_silhouette(mode, bad_fit):
    shoe = "leather_shoes" if mode == "urban" else "sneakers"
    base = [garment("t", "shirt", fit="regular"), garment("b", "pants", fit=bad_fit), garment("s", shoe)]
    outer = garment("o", "coat", fit="regular")
    assert aesthetics._whole_relation([*base, outer], mode)[0] == aesthetics._whole_relation(base, mode)[0]

