"""§14：整件提交的事务、幂等重试与图片清理。"""
import json
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.models import ItemCreate
from app.services import items, photo_import

PNG = b"\x89PNG\r\n\x1a\nphoto-import-fixture"
PAYLOAD = {"type": "tshirt", "primary_color": "white", "material": "cotton"}

@pytest.fixture
def context(uid, tmp_path, monkeypatch):
    monkeypatch.setattr(db, "IMAGES_DIR", tmp_path)
    made = []
    with TestClient(app) as client:
        yield client, made
    for token in made:
        items.delete(photo_import.item_id_for(uid, token), uid)

def post(context, payload=None, data=PNG, token=None):
    client, made = context
    token = token or uuid.uuid4().hex
    if token not in made:
        made.append(token)
    return token, client.post("/items/import-photo", data={"token": token, "payload": json.dumps(PAYLOAD if payload is None else payload)}, files={"file": ("photo.png", data)})

def test_photo_and_attributes_commit_together(context, uid):
    token, response = post(context)
    assert response.status_code == 200, response.text
    item = response.json()["item"]
    assert item["type"] == "tshirt" and item["material"] == "cotton"
    assert item["name"]
    assert (db.IMAGES_DIR / item["image_path"]).read_bytes() == PNG
    assert context[0].get(f"/items/import-photo/{token}").json()["saved"]

def test_replay_returns_same_item(context, uid):
    token, response = post(context)
    _, again = post(context, token=token)
    assert again.json()["replayed"] is True
    assert response.json()["item"]["id"] == again.json()["item"]["id"]
    assert len(list(db.IMAGES_DIR.iterdir())) == 1

def test_concurrent_retries_create_one_item(context, uid):
    token = uuid.uuid4().hex
    context[1].append(token)
    payload = photo_import.parse_payload(json.dumps(PAYLOAD))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: photo_import.commit(uid, token, payload, PNG), range(4)))
    assert len({r["item"]["id"] for r in results}) == 1
    assert sum(not r["replayed"] for r in results) == 1
    assert len(list(db.IMAGES_DIR.iterdir())) == 1

def test_duplicate_photo_does_not_leave_empty_item(context, uid):
    _, first = post(context)
    token, repeated = post(context)
    assert repeated.status_code == 409
    assert repeated.json()["detail"]["code"] == "duplicate_image"
    assert items.get(photo_import.item_id_for(uid, token), uid) is None
    assert (db.IMAGES_DIR / first.json()["item"]["image_path"]).exists()

def test_changed_retry_cannot_overwrite_original(context, uid):
    token, response = post(context)
    _, changed = post(context, {"type": "hoodie"}, token=token)
    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "import_changed"
    assert items.get(response.json()["item"]["id"], uid)["type"] == "tshirt"

@pytest.mark.parametrize("data", [b"", b"bad"])
def test_invalid_photo_never_creates_record(context, uid, data):
    token, result = post(context, data=data)
    assert result.status_code == 400
    assert items.get(photo_import.item_id_for(uid, token), uid) is None
    assert not list(db.IMAGES_DIR.iterdir())

def test_disk_failure_rolls_back_item(context, uid, monkeypatch):
    def fail(*args):
        raise OSError("simulated disk failure")
    monkeypatch.setattr(Path, "replace", fail)
    token, result = post(context)
    assert result.status_code == 400
    assert items.get(photo_import.item_id_for(uid, token), uid) is None
    assert not list(db.IMAGES_DIR.iterdir())

def test_commit_failure_rolls_back_and_removes_photo(context, uid, monkeypatch):
    original = db.conn
    @contextmanager
    def fail_commit(**kwargs):
        with original(**kwargs) as connection:
            yield connection
            if kwargs.get("immediate"):
                raise sqlite3.OperationalError("simulated commit failure")
    monkeypatch.setattr(db, "conn", fail_commit)
    token, result = post(context)
    assert result.status_code == 503
    assert items.get(photo_import.item_id_for(uid, token), uid) is None
    assert not list(db.IMAGES_DIR.iterdir())


def test_failed_attempt_cleanup_cannot_delete_successful_retry(context, uid, monkeypatch):
    token = uuid.uuid4().hex
    context[1].append(token)
    payload = photo_import.parse_payload(json.dumps(PAYLOAD))
    original_conn, original_remove = db.conn, photo_import._remove
    failed = False
    retried = []

    @contextmanager
    def fail_once(**kwargs):
        nonlocal failed
        with original_conn(**kwargs) as connection:
            yield connection
            if kwargs.get("immediate") and not failed:
                failed = True
                raise sqlite3.OperationalError("simulated commit failure")

    def delayed_cleanup(path):
        # 失败事务已释放锁，刻意让同token重试先于旧尝试清理。
        if path.suffix == ".png" and not retried:
            retried.append(photo_import.commit(uid, token, payload, PNG))
        original_remove(path)

    monkeypatch.setattr(db, "conn", fail_once)
    monkeypatch.setattr(photo_import, "_remove", delayed_cleanup)
    with pytest.raises(sqlite3.OperationalError):
        photo_import.commit(uid, token, payload, PNG)
    saved = items.get(photo_import.item_id_for(uid, token), uid)
    assert saved and retried[0]["item"]["id"] == saved["id"]
    assert (db.IMAGES_DIR / saved["image_path"]).read_bytes() == PNG
    assert len(list(db.IMAGES_DIR.iterdir())) == 1


def test_imported_photo_is_removed_on_delete(context, uid):
    _, response = post(context)
    item = response.json()["item"]
    assert items.delete(item["id"], uid)
    assert not (db.IMAGES_DIR / item["image_path"]).exists()


def test_replacing_imported_photo_cleans_attempt_filename(context, uid):
    from app.services import images
    _, response = post(context)
    item = response.json()["item"]
    images.save_image(uid, item["id"], PNG + b"replacement")
    assert not (db.IMAGES_DIR / item["image_path"]).exists()
    assert len(list(db.IMAGES_DIR.iterdir())) == 1

def test_status_for_new_token_does_not_create(context, uid):
    token = uuid.uuid4().hex
    response = context[0].get(f"/items/import-photo/{token}")
    assert response.json() == {"saved": False, "item": None}

def test_invalid_token_is_400(context):
    response = context[0].post("/items/import-photo", data={"token": "../bad", "payload": json.dumps(PAYLOAD)}, files={"file": ("p.png", PNG)})
    assert response.status_code == 400

def test_client_path_and_hash_are_ignored(context):
    _, result = post(context, {**PAYLOAD, "image_path": "../bad", "image_hash": "fake"})
    item = result.json()["item"]
    assert item["image_path"].startswith("it_import_")
    assert item["image_hash"].startswith("sha256:")

def test_save_does_not_call_model(context, monkeypatch):
    from app.services import tagging
    def fail(*args, **kwargs):
        raise AssertionError("保存不应重复识别")
    monkeypatch.setattr(tagging, "tag_image", fail)
    assert post(context)[1].status_code == 200
