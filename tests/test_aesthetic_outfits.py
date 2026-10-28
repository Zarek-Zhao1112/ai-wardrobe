"""§20：整套审美影响真实候选、未知信息与增量迁移。"""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db, schema
from app.main import app
from app.models import ItemCreate
from app.services import aesthetics, items, recommend


def garment(identifier, kind, **values):
    from app import taxonomy
    return {"id": identifier, "type": kind, "slot": taxonomy.CLOTHING_TYPES[kind][1],
        "colors": [], "style": [], "season": [], **values}


def fixture_items():
    return [garment("relaxed-top", "tshirt", fit="loose", primary_color="beige", pattern="solid"),
        garment("clean-top", "shirt", fit="regular", primary_color="white", pattern="solid"),
        garment("relaxed-bottom", "casual_pants", fit="wide_leg", primary_color="khaki", pattern="solid"),
        garment("clean-bottom", "pants", fit="tapered", primary_color="black", pattern="solid"),
        garment("shoe", "sneakers", primary_color="white")]


def fresh_user():
    uid = db.new_id("u")
    with db.conn() as connection:
        connection.execute("INSERT INTO users(id,name,created_at) VALUES(?,?,?)", (uid, "合成测试", db.now_iso()))
    return uid


def seed(uid):
    for item in fixture_items():
        items.create(uid, ItemCreate(**{key: value for key, value in item.items() if key not in {"id", "slot"}}))


def test_profiles_are_complete_and_copies():
    profiles = aesthetics.list_profiles()
    assert {row["id"] for row in profiles} == {"relaxed", "clean", "tonal", "heritage", "sport_mix", "street", "soft", "color_pop"}
    for row in profiles:
        assert {dimension["key"] for dimension in row["dimensions"]} == {"color", "silhouette", "material", "pattern", "layering"}
        assert row["core"] and row["flexible"] and row["sources"]
        row["dimensions"][0]["goal"] = "改写"
    assert aesthetics.get_profile()["dimensions"][0]["goal"] != "改写"


def test_method_changes_actual_selection():
    winners = []
    for method in ("relaxed", "clean"):
        profile = aesthetics.get_profile(method)
        candidates = aesthetics.build_candidates(fixture_items(), profile, "summer", lambda _: 0)
        for outfit in candidates:
            outfit["_relevance"] = aesthetics.assess(outfit, profile)["score"]
        winners.append(set(aesthetics.shortlist(candidates, 1)[0]["item_ids"]))
    assert "relaxed-top" in winners[0] and "relaxed-bottom" in winners[0]
    assert "clean-top" in winners[1] and "clean-bottom" in winners[1]
    assert winners[0] != winners[1]


def test_method_affects_recall_before_combinations():
    pool = [garment(f"t-{i}", "tshirt", fit="loose" if i < 13 else "regular") for i in range(26)]
    relaxed = aesthetics._recall(pool, aesthetics.get_profile("relaxed"), lambda _: 0)
    clean = aesthetics._recall(pool, aesthetics.get_profile("clean"), lambda _: 0)
    assert sum(it["fit"] == "loose" for it in relaxed) > sum(it["fit"] == "loose" for it in clean)
    assert len(relaxed) == len(clean) == 12


def test_original_v1_scores_and_recall_stay_stable():
    pool = [garment("0", "tshirt", fit="loose", primary_color="beige", pattern="solid"),
        garment("1", "pants", fit="tapered", primary_color="black", pattern="plaid"),
        garment("2", "sneakers", primary_color="white")]
    for key, score, order in [("relaxed", .785, ["0", "1", "2"]), ("clean", .845, ["1", "0", "2"])]:
        profile = aesthetics.get_profile(key)
        assert profile["version"] == 1
        assert aesthetics.assess({"items": pool}, profile)["score"] == pytest.approx(score)
        assert [it["id"] for it in aesthetics._recall(pool, profile, lambda _: 0)] == order


@pytest.mark.parametrize("method,preferred,other,bottom,shoe", [
    ("tonal", {"primary_color": "blue"}, {"primary_color": "red"}, {"primary_color": "navy"}, {"primary_color": "blue"}),
    ("heritage", {"primary_color": "khaki", "pattern": "plaid"}, {"primary_color": "pink", "pattern": "floral"}, {"primary_color": "brown"}, {"primary_color": "navy"}),
    ("sport_mix", {"type": "shirt"}, {"type": "hoodie"}, {"type": "sweatpants"}, {"type": "sneakers"}),
    ("street", {"fit": "oversized", "pattern": "graphic"}, {"fit": "regular"}, {}, {}),
    ("soft", {"primary_color": "pink"}, {"primary_color": "black"}, {"primary_color": "beige"}, {"primary_color": "white"}),
    ("color_pop", {"primary_color": "red"}, {"primary_color": "gray"}, {"primary_color": "black"}, {"primary_color": "white"}),
])
def test_new_methods_choose_between_real_competing_items(method, preferred, other, bottom, shoe):
    def make(identifier, default_type, values):
        values = dict(values)
        kind = values.pop("type", default_type)
        return garment(identifier, kind, **{"fit": "straight", "primary_color": "white", "pattern": "solid", **values})
    pool = [make("target", "tshirt", preferred), make("alternative", "tshirt", other),
            make("bottom", "casual_pants", bottom), make("shoe", "sneakers", shoe)]
    profile = aesthetics.get_profile(method)
    candidates = aesthetics.build_candidates(pool, profile, "all", lambda _: 0)
    for outfit in candidates:
        outfit["_relevance"] = aesthetics.assess(outfit, profile)["score"]
    assert candidates and "target" in aesthetics.shortlist(candidates, 1)[0]["item_ids"]
    scores = {next(it["id"] for it in row["items"] if it["slot"] == "top"): row["_relevance"] for row in candidates}
    assert scores["target"] > scores["alternative"]


@pytest.mark.parametrize("method,good,bad", [
    ("tonal", {"fit": "straight"}, {"fit": "slim"}),
    ("heritage", {"type": "shirt", "style": ["vintage"]}, {"type": "blouse"}),
    ("sport_mix", {"type": "shirt"}, {"type": "blouse"}),
    ("street", {"fit": "oversized"}, {"fit": "regular"}),
    ("soft", {"primary_color": "white"}, {"primary_color": "black"}),
    ("color_pop", {"fit": "straight"}, {"fit": "slim"}),
])
def test_each_new_method_changes_recall_before_combining(method, good, bad):
    pool = []
    for prefix, properties in [("target", good), ("other", bad)]:
        for index in range(14):
            values = {"fit": "straight", **properties}
            kind = values.pop("type", "tshirt")
            pool.append(garment(f"{prefix}-{index:02}", kind, **values))
    recalled = aesthetics._recall(pool, aesthetics.get_profile(method), lambda _: 0)
    assert len(recalled) == 12
    assert sum(it["id"].startswith("target") for it in recalled) >= 8


def test_sport_mix_relationship_is_not_all_sports():
    base = [garment("b", "sweatpants", fit="straight", primary_color="black", pattern="solid"),
            garment("s", "sneakers", primary_color="white")]
    profile = aesthetics.get_profile("sport_mix")
    results = []
    for kind in ["shirt", "hoodie"]:
        outfit = {"items": [garment("t", kind, fit="straight", primary_color="white", pattern="solid"), *base]}
        results.append(aesthetics.assess(outfit, profile))
    assert results[0]["score"] > results[1]["score"]
    assert "跨角色混搭线索" in results[0]["dimensions"][-1]["evidence"]


@pytest.mark.parametrize("method,ordinary,target,bottom,shoe", [
    ("tonal", "red", "blue", "navy", "blue"),
    ("color_pop", "gray", "red", "black", "white"),
])
def test_color_method_recall_preserves_unique_cross_role_match(method, ordinary, target, bottom, shoe):
    tops = [garment(f"a-{i:02}", "tshirt", fit="straight", pattern="solid", primary_color=ordinary) for i in range(13)]
    # 同分排序时，唯一目标落在旧探索名额的空档，不能靠偶然ID被保留。
    tops.insert(9, garment("a-08z", "tshirt", fit="straight", pattern="solid", primary_color=target))
    pool = tops + [garment("b", "casual_pants", primary_color=bottom), garment("s", "sneakers", primary_color=shoe)]
    candidates = aesthetics.build_candidates(pool, aesthetics.get_profile(method), "all", lambda _: 0)
    assert any("a-08z" in row["item_ids"] for row in candidates)
    recalled = aesthetics._recall(tops, aesthetics.get_profile(method), lambda _: 0, context=pool)
    assert recalled[0]["id"] == "a-08z" and len(recalled) == 12


def test_accent_is_relationship_not_color_count():
    profile = aesthetics.get_profile("color_pop")
    top = garment("t", "shirt", primary_color="red")
    base = [garment("b", "pants", primary_color="black"), garment("s", "sneakers", primary_color="white")]
    coherent = aesthetics._color_score([top, *base], profile)[0]
    scattered = aesthetics._color_score([top, *base, garment("bag", "bag", primary_color="green")], profile)[0]
    assert coherent > scattered


@pytest.mark.parametrize("method", list(aesthetics.PROFILES))
def test_all_methods_handle_unknowns_and_persist_complete_snapshots(method):
    unknown = {"items": [garment("t", "tshirt"), garment("b", "casual_pants"), garment("s", "other_shoes")]}
    result = aesthetics.assess(unknown, aesthetics.get_profile(method))
    assert result["score"] < .6
    assert all(row["status"] == "unknown" for row in result["dimensions"][:4])
    uid = fresh_user(); seed(uid)
    outfit = recommend.recommend(uid, aesthetic=method, use_llm=False, limit=1)["outfits"][0]
    assert outfit["aesthetic"]["id"] == method
    assert len(outfit["aesthetic"]["dimensions"]) == len(outfit["aesthetic"]["assessment"]["dimensions"]) == 5
    assert recommend.get_outfit(uid, outfit["id"])["aesthetic"] == outfit["aesthetic"]


def test_unknown_is_not_failed_and_material_is_not_texture():
    outfit = {"items": [garment("t", "tshirt", material="cotton"), garment("b", "jeans"), garment("s", "sneakers")]}
    result = aesthetics.assess(outfit, aesthetics.get_profile())
    rows = {row["key"]: row for row in result["dimensions"]}
    assert all(rows[key]["status"] == "unknown" for key in ("color", "silhouette", "material", "pattern"))
    assert "成分不能确认" in rows["material"]["evidence"]
    assert "实际衣长" not in rows["color"]["evidence"]
    assert result["score"] < .6


def test_four_colors_and_multiple_patterns_not_eliminated():
    pool = fixture_items()[:1] + [garment("b", "jeans", colors=["red", "green"], pattern="floral"), garment("s", "sneakers", primary_color="blue")]
    pool[0].update(primary_color="yellow", pattern="plaid")
    assert aesthetics.build_candidates(pool, aesthetics.get_profile("clean"), "summer", lambda _: 0)


def test_optional_layers_exclude_underwear_unknown_and_archived():
    pool = fixture_items() + [garment("outer", "jacket"), garment("bag", "bag"),
        garment("underwear", "underwear"), garment("unknown", "other"), garment("archived", "hat", is_archived=True)]
    candidates = aesthetics.build_candidates(pool, aesthetics.get_profile(), "summer", lambda _: 0)
    ids = [set(row["item_ids"]) for row in candidates]
    assert any("outer" in row for row in ids) and any("bag" in row for row in ids)
    assert any(not ({"bag", "outer"} & row) for row in ids)
    assert all(not ({"underwear", "unknown", "archived"} & row) for row in ids)
    assert all(len(row["item_ids"]) == len(set(row["item_ids"])) <= 5 for row in candidates)


def test_full_and_shoe_without_top_bottom():
    candidates = aesthetics.build_candidates([garment("dress", "dress"), garment("shoe", "sandals")], aesthetics.get_profile(), "summer", lambda _: 0)
    assert len(candidates) == 1 and candidates[0]["kind"] == "full"


def test_shortlist_covers_core_outfits_not_only_shoes():
    pool = [garment(f"t{i}", "tshirt") for i in range(3)] + [garment(f"b{i}", "jeans") for i in range(3)] + [garment(f"s{i}", "sneakers") for i in range(15)]
    candidates = aesthetics.build_candidates(pool, aesthetics.get_profile(), "summer", lambda _: 0)
    for row in candidates:
        row["_relevance"] = .5
    chosen = aesthetics.shortlist(candidates, 12)
    assert len({tuple(sorted(aesthetics.core_ids(row))) for row in chosen}) == 9
    assert {it["id"] for row in chosen for it in row["items"] if it["slot"] == "top"} == {"t0", "t1", "t2"}


def test_no_model_scene_ignored_and_snapshot_persists(monkeypatch):
    uid = fresh_user(); seed(uid)
    def no_network(*a, **k):
        raise AssertionError("本地生成不应调用模型")
    monkeypatch.setattr(recommend, "get_client", no_network)
    first = recommend.recommend(uid, scene="work", aesthetic="clean", limit=1, use_llm=False)["outfits"][0]
    second = recommend.recommend(uid, scene="party", aesthetic="clean", limit=1, use_llm=False)["outfits"][0]
    assert first["scene"] is second["scene"] is None
    assert first["item_ids"] == second["item_ids"]
    assert first["aesthetic"]["id"] == "clean"
    assert len(first["aesthetic"]["assessment"]["dimensions"]) == 5
    monkeypatch.setitem(aesthetics.PROFILES["clean"], "name", "新版名称")
    monkeypatch.setitem(aesthetics.PROFILES["clean"], "version", 99)
    history = recommend.get_outfit(uid, first["id"])
    assert history["aesthetic"]["name"] == "利落清爽" and history["aesthetic"]["version"] == 1


def test_model_receives_complete_method_and_real_item_properties(fake_llm):
    uid = fresh_user(); seed(uid)
    fake = fake_llm([{"ranked": [{"outfit": "O2", "reason": "参考已知配色"}]}])
    result = recommend.recommend(uid, aesthetic="clean", scene="SECRET_OCCASION", client=fake, limit=1)
    text = fake.calls[0]["messages"][1]["content"]
    assert "利落清爽" in text and "SECRET_OCCASION" not in text
    assert all(label in text for _, label in aesthetics.DIMENSIONS)
    assert "本地五维评估" in text and '"material": "unknown"' in text
    rows = [json.loads(line) for line in text.splitlines() if line.startswith('{"code":')]
    assert rows and all("pattern" in row and "fit" in row and "type" in row and "length" in row for row in rows)
    assert all("scene" not in row and "formality" not in row for row in rows)
    assert result["outfits"][0]["reason"] == "参考已知配色"


def test_scene_and_formality_not_in_preference_index(monkeypatch):
    monkeypatch.setattr(recommend.preference, "query", lambda *a, **k: [
        {"tag_type": kind, "tag_value": value, "decayed_score": 1, "confidence": 1}
        for kind, value in (("occasion", "work"), ("formality", "formal"), ("color", "blue"))])
    assert recommend.build_score_index("synthetic", "summer", None) == {("color", "blue"): 1}


@pytest.mark.parametrize("reason", ["在O1基础上搭配A2", "outfitO1", "O1look", "A2shirt"])
def test_internal_aliases_not_exposed_in_reason(fake_llm, reason):
    uid = fresh_user(); seed(uid)
    fake = fake_llm([{"ranked": [{"outfit": "O1", "reason": reason}]}])
    result = recommend.recommend(uid, aesthetic="clean", client=fake, limit=1)
    assert result["outfits"][0]["reason"] is None
    assert len(result["outfits"][0]["aesthetic"]["assessment"]["dimensions"]) == 5


def test_catalog_api_invalid_method_and_detail_keeps_snapshot():
    with TestClient(app) as c:
        catalog = c.get("/outfits/aesthetics").json()
        assert len(catalog["aesthetics"]) == 8
        assert c.post("/outfits/recommend?aesthetic=unknown&use_llm=false").status_code == 400
        uid = db.get_default_user_id(); seed(uid)
        generated = c.post("/outfits/recommend?aesthetic=clean&use_llm=false&limit=1").json()["outfits"][0]
        detail = c.get("/outfits/" + generated["id"]).json()
        assert detail["aesthetic"] == generated["aesthetic"]


def test_v2_migration_preserves_items_and_old_outfits(monkeypatch, tmp_path):
    path = tmp_path / "v2.db"
    with sqlite3.connect(path) as connection:
        for version, statements in schema.MIGRATIONS:
            if version <= 2:
                for sql in statements:
                    connection.execute(sql)
        connection.execute("INSERT INTO meta VALUES('schema_version','2')")
        connection.execute("INSERT INTO users VALUES('u','合成','2026-01-01')")
        connection.execute("INSERT INTO clothing_items(id,user_id,type,slot,created_at,updated_at) VALUES('i','u','shirt','top','2026-01-01','2026-01-01')")
        connection.execute("INSERT INTO outfits(id,user_id,item_ids,scene,created_at) VALUES('o','u','[\"i\"]','work','2026-01-01')")
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    assert db.migrate(verbose=False) == schema.SCHEMA_VERSION
    assert db.migrate(verbose=False) == schema.SCHEMA_VERSION
    with db.conn() as connection:
        assert connection.execute("SELECT COUNT(*) FROM clothing_items").fetchone()[0] == 1
        old = dict(connection.execute("SELECT * FROM outfits WHERE id='o'").fetchone())
        assert old["aesthetic_snapshot"] is None and old["scene"] == "work"
    assert recommend.get_outfit("u", "o")["aesthetic"] is None
