"""必选衣物是候选及落库的硬约束；测试不调用真实模型。"""
import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.models import ItemCreate
from app.services import aesthetics, items, recommend
from app.services.llm.client import LLMError


def new_user():
    uid = db.new_id("u")
    with db.conn() as c:
        c.execute("INSERT INTO users(id,name,created_at) VALUES(?,?,?)", (uid, "必选合成测试", db.now_iso()))
    return uid


def seed(uid, kind, **values):
    return items.create(uid, ItemCreate(type=kind, **values))


def wardrobe(uid):
    return [seed(uid, kind, name=kind, season=[]) for kind in
            ("tshirt", "shirt", "jeans", "pants", "dress", "sneakers", "boots", "jacket", "hat")]


@pytest.mark.parametrize("kind", ["tshirt", "jeans", "dress", "sneakers", "jacket", "hat"])
def test_every_candidate_and_saved_outfit_contains_anchor(kind, fake_llm):
    uid = new_user()
    pool = wardrobe(uid)
    anchor = next(row for row in pool if row["type"] == kind)
    candidates = aesthetics.build_candidates(pool, aesthetics.get_profile(), "autumn", lambda _: 0, anchor["id"])
    assert candidates and all(anchor["id"] in row["item_ids"] for row in candidates)
    model = fake_llm([{"ranked": [{"outfit": "O99999", "reason": "无效候选"}]}])
    result = recommend.recommend(uid, season="autumn", limit=5, anchor_item_id=anchor["id"], client=model)
    assert result["outfits"] and model.calls
    for row in result["outfits"]:
        assert anchor["id"] in row["item_ids"]
        assert row["aesthetic"]["anchor_item_id"] == anchor["id"]
        assert anchor["id"] in recommend.get_outfit(uid, row["id"])["item_ids"]


@pytest.mark.parametrize("kind", ["tshirt", "jacket", "hat"])
def test_low_affinity_anchor_survives_both_recall_limits(kind):
    uid = new_user()
    wardrobe(uid)
    for _ in range(35):
        seed(uid, kind, fit="loose", pattern="solid", primary_color="beige", season=[])
    anchor = seed(uid, kind, fit="slim", primary_color="red", season=[])
    result = recommend.recommend(uid, season="autumn", limit=3, use_llm=False, anchor_item_id=anchor["id"])
    assert result["outfits"] and all(anchor["id"] in row["item_ids"] for row in result["outfits"])


@pytest.mark.parametrize("state", ["missing", "foreign", "archived", "season", "home", "unsupported"])
def test_invalid_anchor_never_falls_back_or_calls_model(state, fake_llm):
    uid = new_user()
    wardrobe(uid)
    identifier = "not-found"
    if state != "missing":
        owner = new_user() if state == "foreign" else uid
        kind = {"home": "pajamas", "unsupported": "underwear"}.get(state, "tshirt")
        anchor = seed(owner, kind, season=["winter"] if state == "season" else [])
        identifier = anchor["id"]
        if state == "archived":
            with db.conn() as c:
                c.execute("UPDATE clothing_items SET is_archived=1 WHERE id=?", (identifier,))
    model = fake_llm([])
    with pytest.raises(recommend.AnchorUnavailable):
        recommend.recommend(uid, season="summer", anchor_item_id=identifier, client=model)
    assert not model.calls


def test_insufficient_anchor_wardrobe_is_empty_and_no_model(fake_llm):
    uid = new_user()
    anchor = seed(uid, "tshirt")
    model = fake_llm([])
    result = recommend.recommend(uid, anchor_item_id=anchor["id"], client=model)
    assert result["outfits"] == [] and result["candidates"] == 0
    assert "这件" in result["note"] and not model.calls


def test_model_failure_keeps_anchor(fake_llm):
    uid = new_user()
    anchor = wardrobe(uid)[0]
    result = recommend.recommend(uid, season="autumn", anchor_item_id=anchor["id"], client=fake_llm(error=LLMError("合成失败")))
    assert result["degraded"] and result["outfits"]
    assert all(anchor["id"] in row["item_ids"] for row in result["outfits"])


def test_anchor_deleted_during_model_is_not_saved(monkeypatch):
    uid = new_user()
    anchor = wardrobe(uid)[0]
    def deleted(*args, **kwargs):
        with db.conn() as c:
            c.execute("DELETE FROM clothing_items WHERE id=? AND user_id=?", (anchor["id"], uid))
        return []
    monkeypatch.setattr(recommend, "_llm_rank", deleted)
    with pytest.raises(recommend.AnchorUnavailable):
        recommend.recommend(uid, season="autumn", anchor_item_id=anchor["id"], client=object())
    assert recommend.list_outfits(uid) == []


def test_anchor_api_rejects_unknown_and_supports_ordinary_generation():
    with TestClient(app) as client:
        invalid = client.post("/outfits/recommend", params={"anchor_item_id": "does-not-exist", "use_llm": "false"})
        assert invalid.status_code == 400 and "不存在" in invalid.json()["detail"]
        assert client.post("/outfits/recommend", params={"use_llm": "false"}).status_code == 200


def test_anchor_batch_rolls_back_together(monkeypatch):
    uid = new_user()
    anchor = wardrobe(uid)[0]
    original = recommend._persist
    calls = []
    def fail_second(*args, **kwargs):
        connection = kwargs["connection"]
        assert connection.in_transaction
        calls.append(connection)
        if len(calls) == 2:
            raise RuntimeError("合成第二套保存失败")
        return original(*args, **kwargs)
    monkeypatch.setattr(recommend, "_persist", fail_second)
    with pytest.raises(RuntimeError, match="第二套"):
        recommend.recommend(uid, season="autumn", anchor_item_id=anchor["id"], use_llm=False)
    assert len(calls) == 2 and calls[0] is calls[1]
    assert recommend.list_outfits(uid) == []
    assert items.get(anchor["id"], uid)["suggestion_count"] == 0
