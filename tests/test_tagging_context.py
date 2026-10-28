"""§12：双图请求、目标定位、冲突保护与原图临时文件清理。"""
import base64
import json

import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.main import app
from app.models import ItemCreate
from app.services import images, items, tagging
from app.services.llm.client import LLMError

PNG = b"\x89PNG\r\n\x1a\ncropped-image"
ORIGINAL = b"\xff\xd8\xfforiginal-with-description"
RECT = {"x": .1, "y": .2, "w": .3, "h": .4}


@pytest.fixture
def context_client(monkeypatch, fake_llm, tmp_path):
    monkeypatch.setattr(config, "VISION_READY", True)
    # 主图与参考图都落在可观察的系统临时目录里，确认异常路径也会清理。
    monkeypatch.setattr(images.tempfile, "tempdir", str(tmp_path))
    model = fake_llm([{"type": "tshirt", "material": "cotton", "crop": RECT}])
    monkeypatch.setattr(tagging, "get_client", lambda **_: model)
    with db.conn() as c:
        before = {row["id"] for row in c.execute("SELECT id FROM clothing_items")}
    try:
        with TestClient(app) as client:
            yield client, model, tmp_path
    finally:
        with db.conn() as c:
            added = [row["id"] for row in c.execute("SELECT id FROM clothing_items") if row["id"] not in before]
        for item_id in added:
            items.delete(item_id)


def files(reference=ORIGINAL):
    return {"file": ("crop.png", PNG), "reference_image": ("arbitrary-name.png", reference)}


def image_bytes(model):
    content = model.calls[-1]["messages"][1]["content"]
    return [base64.b64decode(p["image_url"]["url"].split(",", 1)[1]) for p in content if p["type"] == "image_url"]


def test_preview_preserves_two_images_and_target_rect(context_client):
    client, model, temporary = context_client
    response = client.post("/tagging/preview", files=files(), data={"reference_crop": json.dumps(RECT)})
    assert response.status_code == 200, response.text
    assert image_bytes(model) == [PNG, ORIGINAL]
    content = model.calls[0]["messages"][1]["content"]
    assert "图2" in content[0]["text"] and '"x": 0.1' in content[0]["text"]
    assert content[-1]["image_url"]["url"].startswith("data:image/jpeg;")
    assert response.json()["crop"] is None  # 参考原图的框不能再次套用到裁剪图
    assert not list(temporary.iterdir())


def test_single_image_remains_compatible(context_client):
    client, model, temporary = context_client
    response = client.post("/tagging/preview", files={"file": ("photo.png", PNG)})
    assert response.status_code == 200
    assert image_bytes(model) == [PNG]
    assert response.json()["crop"] == RECT
    assert not list(temporary.iterdir())


@pytest.mark.parametrize("reference", [b"", b"not an image"])
def test_bad_reference_is_400_and_cleans_main_image(context_client, reference):
    client, model, temporary = context_client
    response = client.post("/tagging/preview", files=files(reference))
    assert response.status_code == 400
    assert not model.calls
    assert not list(temporary.iterdir())


@pytest.mark.parametrize("rect", ["null", "[]", "oops", '{"x":NaN,"y":0,"w":1,"h":1}', '{"x":true,"y":0,"w":1,"h":1}', '{"x":0.9,"y":0,"w":0.5,"h":1}'])
def test_bad_reference_rect_is_rejected_before_model(context_client, rect):
    client, model, temporary = context_client
    response = client.post("/tagging/preview", files=files(), data={"reference_crop": rect})
    assert response.status_code == 400
    assert not model.calls
    assert not list(temporary.iterdir())


def test_reference_rect_requires_reference_image(context_client):
    client, model, _ = context_client
    response = client.post("/tagging/preview", files={"file": ("crop.png", PNG)}, data={"reference_crop": json.dumps(RECT)})
    assert response.status_code == 400
    assert not model.calls


def test_large_reference_is_400(context_client, monkeypatch):
    client, model, temporary = context_client
    monkeypatch.setattr(images, "MAX_IMAGE_BYTES", 64)
    response = client.post("/tagging/preview", files=files(ORIGINAL * 20))
    assert response.status_code == 400
    assert "参考原图" in response.json()["detail"]
    assert not model.calls
    assert not list(temporary.iterdir())


def test_save_uses_both_images_but_persists_crop_only(context_client, uid):
    client, model, temporary = context_client
    item = items.create(uid, ItemCreate(type="other", name="双图识别"))
    response = client.post(f"/items/{item['id']}/image", files=files(), data={"reference_crop": json.dumps(RECT)})
    assert response.status_code == 200, response.text
    assert image_bytes(model) == [PNG, ORIGINAL]
    result = response.json()
    assert images.read_image(result["image_path"]).read_bytes() == PNG
    assert result["item"]["material"] == "cotton"
    assert not list(temporary.iterdir())


def test_bad_reference_cannot_replace_existing_photo(context_client, uid):
    client, model, temporary = context_client
    item = items.create(uid, ItemCreate(type="tshirt"))
    saved = images.save_image(uid, item["id"], PNG)
    response = client.post(f"/items/{item['id']}/image", files={"file": ("new.jpg", ORIGINAL), "reference_image": ("bad", b"bad")})
    assert response.status_code == 400
    assert images.read_image(saved["image_path"]).read_bytes() == PNG
    assert not model.calls
    assert not list(temporary.iterdir())


def test_preview_model_failure_cleans_both_images(context_client):
    client, model, temporary = context_client
    model.error = LLMError("mock failure")
    response = client.post("/tagging/preview", files=files())
    assert response.status_code == 503
    assert not list(temporary.iterdir())


def test_save_model_failure_preserves_saved_photo(context_client, uid):
    client, model, temporary = context_client
    model.error = LLMError("mock failure")
    item = items.create(uid, ItemCreate(type="other"))
    response = client.post(f"/items/{item['id']}/image", files=files())
    assert response.status_code == 200
    assert response.json()["tagging"]["status"] == "failed"
    assert images.read_image(response.json()["image_path"]).read_bytes() == PNG
    assert not list(temporary.iterdir())


def test_conflicts_are_not_applied_and_are_reported(context_client, uid):
    client, model, _ = context_client
    model.responses = [{"type": "shirt", "colors": ["black"], "material": "silk", "conflicts": {"primary_color": "图中黑色，描述写白色", "material": "描述与图片不能对应", "id": "非法字段"}}]
    item = items.create(uid, ItemCreate(type="other"))
    response = client.post(f"/items/{item['id']}/image", files=files())
    result = response.json()
    assert result["item"]["type"] == "shirt"
    assert result["item"]["colors"] == []
    assert not result["item"]["primary_color"]
    assert not result["item"]["material"]
    assert set(result["tagging"]["conflicts"]) == {"primary_color", "material"}


def test_all_conflicting_tags_still_return_confirmation(context_client, uid):
    client, model, _ = context_client
    model.responses = [{"type": "shirt", "conflicts": {"type": "目标不明确"}}]
    item = items.create(uid, ItemCreate(type="other"))
    response = client.post(f"/items/{item['id']}/image", files=files())
    result = response.json()["tagging"]
    assert result["status"] == "empty"
    assert result["conflicts"]["type"] == "目标不明确"
    assert not result["tags"]


def test_manual_attributes_survive_context_recognition(context_client, uid):
    client, model, _ = context_client
    model.responses = [{"type": "shirt", "material": "silk"}]
    item = items.create(uid, ItemCreate(type="tshirt", material="cotton"))
    result = client.post(f"/items/{item['id']}/image", files=files()).json()
    assert result["item"]["type"] == "tshirt"
    assert result["item"]["material"] == "cotton"


def test_small_user_selected_rect_is_not_filtered_as_model_suggestion():
    assert tagging.parse_reference_crop('{"x":0,"y":0,"w":0.01,"h":0.01}')["w"] == .01


def test_huge_integer_rect_is_400(context_client):
    client, model, _ = context_client
    rect = '{"x":' + "9" * 350 + ',"y":0,"w":1,"h":1}'
    assert client.post("/tagging/preview", files=files(), data={"reference_crop": rect}).status_code == 400
    assert not model.calls


def test_type_conflict_removes_dependent_length(context_client):
    client, model, _ = context_client
    model.responses = [{"type": "jeans", "length": "nine_tenth", "conflicts": {"type": "目标不明确"}}]
    result = client.post("/tagging/preview", files=files()).json()
    assert not ({"type", "slot", "length"} & result["tags"].keys())


def test_manual_tagging_reports_conflicts(context_client, uid):
    client, model, _ = context_client
    item = items.create(uid, ItemCreate(type="other"))
    images.save_image(uid, item["id"], PNG)
    model.responses = [{"type": "shirt", "conflicts": {"type": "目标不明确"}}]
    response = client.post(f"/tagging/{item['id']}", json={"apply": True})
    assert response.status_code == 200, response.text
    assert response.json()["conflicts"] == {"type": "目标不明确"}
