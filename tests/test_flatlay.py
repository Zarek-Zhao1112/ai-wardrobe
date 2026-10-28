"""§23：图片版本事务、任务幂等与固定路径工具校验；不调用真实模型。"""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from app import db
from app.main import app
from app.models import ItemCreate
from app.services import images, items, photo_import, presentation
from scripts import desktop_bridge
from scripts.flatlay_bridge import FlatlayJobs, IMAGE_MODEL, hook_command

SOURCE = b"\x89PNG\r\n\x1a\nreal-source-flatlay"
ORIGINAL = b"\xff\xd8\xfforiginal-screenshot-flatlay"
DISPLAY = b"\x89PNG\r\n\x1a\nAI-display-flatlay"
TOKEN = "x" * 40


@pytest.fixture
def image_context(uid, tmp_path, monkeypatch):
    monkeypatch.setattr(db, "IMAGES_DIR", tmp_path / "images")
    made = []
    yield made
    for item in made: items.delete(item["id"], uid)


def make_item(uid, made, **kwargs):
    item = photo_import.commit(uid, uuid.uuid4().hex, ItemCreate(type="jacket", name="真实外套"), SOURCE, **kwargs)["item"]
    made.append(item)
    return item


def test_three_images_commit_without_changing_source_identity(uid, image_context):
    item = make_item(uid, image_context, original_data=ORIGINAL, display_data=DISPLAY)
    assert item["display_selected"]
    assert item["image_hash"] == images.content_hash(SOURCE) == item["display_source_hash"]
    assert images.read_image(item["image_path"]).read_bytes() == SOURCE
    assert images.read_image(item["original_image_path"]).read_bytes() == ORIGINAL
    assert images.read_image(item["display_image_path"]).read_bytes() == DISPLAY
    assert item["display_model"] == IMAGE_MODEL


def test_duplicate_source_rejected_even_if_generated_pixels_differ(uid, image_context):
    make_item(uid, image_context, display_data=DISPLAY)
    with pytest.raises(photo_import.ImportError) as error:
        make_item(uid, image_context, display_data=DISPLAY + b"different")
    assert error.value.code == "duplicate_image"
    assert len(list(db.IMAGES_DIR.iterdir())) == 2


def test_display_frozen_for_idempotent_retry(uid, image_context):
    token = uuid.uuid4().hex
    payload = ItemCreate(type="jacket")
    first = photo_import.commit(uid, token, payload, SOURCE, original_data=ORIGINAL, display_data=DISPLAY)
    image_context.append(first["item"])
    assert photo_import.commit(uid, token, payload, SOURCE, original_data=ORIGINAL, display_data=DISPLAY)["replayed"]
    with pytest.raises(photo_import.ImportError) as error:
        photo_import.commit(uid, token, payload, SOURCE, original_data=ORIGINAL, display_data=DISPLAY + b"new")
    assert error.value.code == "import_changed"
    assert len(list(db.IMAGES_DIR.iterdir())) == 3


def test_multi_file_install_rolls_back_on_second_rename(uid, image_context, monkeypatch):
    real = Path.replace
    count = 0
    def replace(path, target):
        nonlocal count
        count += 1
        if count == 2: raise OSError("simulated")
        return real(path, target)
    monkeypatch.setattr(Path, "replace", replace)
    token = uuid.uuid4().hex
    with pytest.raises(OSError): photo_import.commit(uid, token, ItemCreate(type="jacket"), SOURCE, original_data=ORIGINAL, display_data=DISPLAY)
    assert items.get(photo_import.item_id_for(uid, token), uid) is None
    assert not list(db.IMAGES_DIR.iterdir())


def test_reverting_presentation_preserves_attributes_and_all_images(uid, image_context):
    item = make_item(uid, image_context, original_data=ORIGINAL, display_data=DISPLAY)
    fresh = presentation.select(item["id"], uid, False)
    assert not fresh["display_selected"]
    for key in ("image_path", "image_hash", "original_image_path", "display_image_path", "name", "type", "tagging_status"):
        assert fresh[key] == item[key]
    assert presentation.select(item["id"], uid, True)["display_selected"]


def test_wrong_source_or_owner_cannot_attach(uid, image_context):
    item = make_item(uid, image_context)
    with pytest.raises(presentation.PresentationError) as error: presentation.attach(item["id"], uid, DISPLAY, "stale")
    assert error.value.status == 409
    with pytest.raises(presentation.PresentationError) as error: presentation.attach(item["id"], "other", DISPLAY, item["image_hash"])
    assert error.value.status == 404
    assert len(list(db.IMAGES_DIR.iterdir())) == 1


def test_batch_attach_cannot_overwrite_a_newly_selected_display(uid, image_context):
    item = make_item(uid, image_context)
    first = presentation.attach(item['id'], uid, DISPLAY, item['image_hash'], only_if_empty=True)
    with pytest.raises(presentation.PresentationError) as error:
        presentation.attach(item['id'], uid, DISPLAY + b'late batch', item['image_hash'], only_if_empty=True)
    assert error.value.status == 409
    fresh = items.get(item['id'], uid)
    assert fresh['display_image_path'] == first['display_image_path'] and fresh['display_selected']
    assert images.read_image(fresh['display_image_path']).read_bytes() == DISPLAY
    assert len(list(db.IMAGES_DIR.iterdir())) == 2


def test_api_batch_attach_conflict_keeps_existing_display(uid, image_context):
    item = make_item(uid, image_context, display_data=DISPLAY)
    with TestClient(app) as client:
        response = client.post(f'/items/{item["id"]}/presentation', data={'source_hash': item['image_hash'], 'only_if_empty': 'true'}, files={'file': ('late.png', DISPLAY + b'late')})
    assert response.status_code == 409
    assert items.get(item['id'], uid)['display_image_path'] == item['display_image_path']


def test_replacement_batch_keeps_concurrently_changed_display(uid, image_context):
    item = make_item(uid, image_context, original_data=ORIGINAL, display_data=DISPLAY)
    user_image = DISPLAY + b'user replacement'
    current = presentation.attach(item['id'], uid, user_image, item['image_hash'])
    with pytest.raises(presentation.PresentationError) as error:
        presentation.attach(item['id'], uid, DISPLAY + b'late batch', item['image_hash'], expected_display_path=item['display_image_path'])
    assert error.value.status == 409
    fresh = items.get(item['id'], uid)
    assert fresh['display_image_path'] == current['display_image_path']
    assert images.read_image(fresh['display_image_path']).read_bytes() == user_image
    assert images.read_image(fresh['original_image_path']).read_bytes() == ORIGINAL
    assert len(list(db.IMAGES_DIR.iterdir())) == 3


def test_matching_replacement_batch_preserves_source_and_attributes(uid, image_context):
    item = make_item(uid, image_context, original_data=ORIGINAL, display_data=DISPLAY)
    replacement = DISPLAY + b'white background'
    fresh = presentation.attach(item['id'], uid, replacement, item['image_hash'], expected_display_path=item['display_image_path'])
    for key in ('image_path', 'image_hash', 'original_image_path', 'name', 'type', 'tagging_status'):
        assert fresh[key] == item[key]
    assert fresh['display_image_path'] != item['display_image_path'] and fresh['display_selected']
    assert images.read_image(fresh['image_path']).read_bytes() == SOURCE
    assert images.read_image(fresh['display_image_path']).read_bytes() == replacement
    assert len(list(db.IMAGES_DIR.iterdir())) == 3


def test_api_expected_display_path_rejects_stale_batch(uid, image_context):
    item = make_item(uid, image_context, display_data=DISPLAY)
    with TestClient(app) as client:
        response = client.post(f'/items/{item["id"]}/presentation', data={'source_hash': item['image_hash'], 'expected_display_path': 'stale.png'}, files={'file': ('late.png', DISPLAY + b'late')})
        assert response.status_code == 409
        assert items.get(item['id'], uid)['display_image_path'] == item['display_image_path']
        response = client.post(f'/items/{item["id"]}/presentation', data={'source_hash': item['image_hash'], 'expected_display_path': item['display_image_path']}, files={'file': ('white.png', DISPLAY + b'white')})
        assert response.status_code == 200
        assert response.json()['display_image_path'] != item['display_image_path']


def test_reupload_failure_preserves_prior_versions_and_selected_state(uid, image_context, monkeypatch):
    item = make_item(uid, image_context, original_data=ORIGINAL, display_data=DISPLAY)
    monkeypatch.setattr(Path, "replace", lambda *args: (_ for _ in ()).throw(OSError("simulated")))
    with pytest.raises(images.ImageError): images.save_image(uid, item["id"], SOURCE + b"new")
    fresh = items.get(item["id"], uid)
    for key in ("image_hash", "image_path", "display_image_path", "original_image_path", "display_selected"):
        assert fresh[key] == item[key]
    assert len(list(db.IMAGES_DIR.iterdir())) == 3


def test_reupload_invalidates_and_removes_captured_old_versions(uid, image_context):
    item = make_item(uid, image_context, original_data=ORIGINAL, display_data=DISPLAY)
    images.save_image(uid, item["id"], SOURCE + b"new")
    fresh = items.get(item["id"], uid)
    assert not fresh["display_selected"] and fresh["display_image_path"] is None
    assert fresh["original_image_path"] == fresh["image_path"]
    assert len(list(db.IMAGES_DIR.iterdir())) == 1


def test_recrop_keeps_full_original_and_invalidates_generated_display(uid, image_context):
    item = make_item(uid, image_context, original_data=ORIGINAL, display_data=DISPLAY)
    images.save_image(uid, item["id"], SOURCE + b"recrop", original_data=ORIGINAL)
    fresh = items.get(item["id"], uid)
    assert images.read_image(fresh["original_image_path"]).read_bytes() == ORIGINAL
    assert images.read_image(fresh["image_path"]).read_bytes() == SOURCE + b"recrop"
    assert not fresh["display_selected"] and fresh["display_image_path"] is None
    assert len(list(db.IMAGES_DIR.iterdir())) == 2


def test_recrop_original_install_failure_keeps_prior_versions(uid, image_context, monkeypatch):
    item = make_item(uid, image_context, original_data=ORIGINAL, display_data=DISPLAY)
    real, count = Path.replace, 0
    def replace(path, target):
        nonlocal count
        count += 1
        if count == 2: raise OSError("simulated original install failure")
        return real(path, target)
    monkeypatch.setattr(Path, "replace", replace)
    with pytest.raises(images.ImageError): images.save_image(uid, item["id"], SOURCE + b"recrop", original_data=ORIGINAL)
    fresh = items.get(item["id"], uid)
    assert fresh["image_hash"] == item["image_hash"] and fresh["display_selected"]
    assert images.read_image(fresh["original_image_path"]).read_bytes() == ORIGINAL
    assert len(list(db.IMAGES_DIR.iterdir())) == 3


def test_late_cleanup_cannot_remove_concurrently_attached_new_display(uid, image_context, monkeypatch):
    item = make_item(uid, image_context, display_data=DISPLAY)
    real = Path.unlink
    attached = []
    def unlink(path, *args, **kwargs):
        if path.name == item["image_path"] and not attached:
            attached.append(True)
            presentation.attach(item["id"], uid, DISPLAY + b"latest", item["image_hash"])
        return real(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", unlink)
    images.save_image(uid, item["id"], SOURCE)
    fresh = items.get(item["id"], uid)
    assert fresh["display_selected"]
    assert images.read_image(fresh["display_image_path"]).read_bytes() == DISPLAY + b"latest"


def test_delete_all_owned_versions_without_glob_damage(uid, image_context):
    item = make_item(uid, image_context, original_data=ORIGINAL, display_data=DISPLAY)
    untouched = db.IMAGES_DIR / (item["id"] + ".display-not-a-valid-id.png")
    untouched.write_bytes(DISPLAY)
    assert items.delete(item["id"], uid)
    assert list(db.IMAGES_DIR.iterdir()) == [untouched]


def test_api_import_and_select_exposes_versions(uid, image_context):
    token = uuid.uuid4().hex
    with TestClient(app) as client:
        response = client.post("/items/import-photo", data={"token": token, "payload": '{"type":"jacket"}'}, files={
            "file": ("crop.png", SOURCE), "original": ("original.jpg", ORIGINAL), "display": ("display.png", DISPLAY)})
        assert response.status_code == 200, response.text
        item = response.json()["item"]; image_context.append(item)
        listed = client.get("/items").json()["items"]
        listed_item = next(x for x in listed if x["id"] == item["id"])
        assert listed_item["display_selected"]
        assert listed_item["image_hash"] == images.content_hash(SOURCE) == listed_item["display_source_hash"]
        assert client.get(f'/items/{item["id"]}').json()["image_hash"] == images.content_hash(SOURCE)
        response = client.patch(f'/items/{item["id"]}/presentation', json={"selected": False})
        assert response.status_code == 200 and not response.json()["display_selected"]


def encoded(data=SOURCE):
    return "data:image/png;base64," + base64.b64encode(data).decode()


def wait_job(manager, token):
    for _ in range(200):
        value = manager.status(token)
        if value["state"] != "running" and token not in manager.running: return value
        time.sleep(.01)
    raise AssertionError("job did not settle")


def test_job_replay_and_status_do_not_generate_again(tmp_path):
    called = []
    gate = threading.Event()
    def runner(*args): called.append(1); gate.wait(2); return DISPLAY, 5.71
    manager = FlatlayJobs(tmp_path, Path("node"), Path("cli"), threading.BoundedSemaphore(1), dict, runner)
    token = uuid.uuid4().hex; payload = {"id": token, "kind": "jacket", "image": encoded()}
    manager.start(payload); manager.start(payload)
    assert manager.status(token)["state"] == "running"
    gate.set(); result = wait_job(manager, token)
    assert result["state"] == "completed" and result["credit"] == 5.71
    assert result["source_hash"] == hashlib.sha256(SOURCE).hexdigest()
    assert base64.b64decode(result["image"].split(",", 1)[1]) == DISPLAY
    manager.start(payload)
    assert called == [1]
    assert not list((tmp_path / token).glob("input.*"))


def test_expired_job_keeps_tombstone_no_repeat_fee(tmp_path):
    called = []
    manager = FlatlayJobs(tmp_path, Path("node"), Path("cli"), threading.BoundedSemaphore(1), dict, lambda *a: (called.append(1) or DISPLAY, None))
    token = uuid.uuid4().hex; payload = {"id": token, "kind": "jacket", "image": encoded()}
    manager.start(payload); wait_job(manager, token)
    os.utime(tmp_path / token / "state.json", (0, 0))
    assert manager.status(token)["state"] == "expired"
    assert manager.start(payload)["state"] == "expired"
    assert called == [1]
    assert (tmp_path / token / "state.json").exists()
    assert not list((tmp_path / token).glob("result.*"))


def test_changed_job_source_rejected(tmp_path):
    manager = FlatlayJobs(tmp_path, Path("node"), Path("cli"), threading.BoundedSemaphore(1), dict, lambda *a: (DISPLAY, None))
    token = uuid.uuid4().hex; payload = {"id": token, "kind": "jacket", "image": encoded()}
    manager.start(payload); wait_job(manager, token)
    with pytest.raises(HTTPException) as error: manager.start({**payload, "image": encoded(SOURCE + b"other")})
    assert error.value.status_code == 409


def test_restart_marks_unknown_without_running_model(tmp_path):
    token = uuid.uuid4().hex; folder = tmp_path / token; folder.mkdir()
    (folder / "state.json").write_text(json.dumps({"id": token, "state": "running", "fingerprint": "x"}), encoding="utf-8")
    (folder / "input.png").write_bytes(SOURCE)
    called = []
    manager = FlatlayJobs(tmp_path, Path("node"), Path("cli"), threading.BoundedSemaphore(1), dict, lambda *a: called.append(1))
    assert manager.status(token)["state"] == "failed"
    assert not called and not (folder / "input.png").exists()


def test_failed_runner_releases_capacity_and_deletes_input(tmp_path):
    def fail(*args): raise OSError("simulated")
    busy = threading.BoundedSemaphore(1)
    manager = FlatlayJobs(tmp_path, Path("node"), Path("cli"), busy, dict, fail)
    token = uuid.uuid4().hex
    manager.start({"id": token, "kind": "jacket", "image": encoded()})
    assert wait_job(manager, token)["state"] == "failed"
    for _ in range(100):
        if busy.acquire(blocking=False): break
        time.sleep(.01)
    else: raise AssertionError("capacity not released")
    busy.release()
    assert not list((tmp_path / token).glob("input.*"))


@pytest.mark.parametrize("change", [{"id": []}, {"id": "../x"}, {"kind": "jacket;command"}, {"image": "https://outside/image.png"}, {"image": encoded(b"not image")}])
def test_job_rejects_bad_input(tmp_path, change):
    manager = FlatlayJobs(tmp_path, Path("node"), Path("cli"), threading.BoundedSemaphore(1), dict, lambda *a: pytest.fail("must not run"))
    with pytest.raises(HTTPException): manager.start({"id": uuid.uuid4().hex, "kind": "jacket", "image": encoded(), **change})


def test_bridge_flatlay_requires_pairing_and_offline_feature_is_honest(tmp_path):
    executable = tmp_path / "node"; executable.touch()
    client = TestClient(desktop_bridge.create_app(executable, TOKEN))
    assert client.get("/flatlay/jobs/" + "a" * 32).status_code == 403
    assert client.get("/health", headers={"Authorization": "Bearer " + TOKEN}).json()["image_edit"] is False
    assert client.post("/flatlay/jobs", headers={"Authorization": "Bearer " + TOKEN}, json={}).status_code == 503


NODE = Path(r'C:/Program Files/nodejs/node.exe')
GUARD = Path(__file__).resolve().parent.parent / "scripts/flatlay_guard.cjs"


@pytest.mark.skipif(not NODE.exists(), reason="requires Node for real hook exit-code checks")
@pytest.mark.parametrize("change", [{"image1": "wrong"}, {"output_dir": "wrong"}, {"image2": "https://outside/image"}, {"prompt": "different"}, {"size": "4096x4096"}])
def test_guard_rejects_changed_parameters_without_claim(tmp_path, change):
    expected = {"image1": str(tmp_path / "input.png"), "output_dir": str(tmp_path / "output"), "prompt": "fixed", "size": "1024x1024"}
    (tmp_path / "guard.json").write_text(json.dumps(expected), encoding="utf-8")
    result = subprocess.run([str(NODE), "-e", "try{process.exit(require(process.argv[1]).guard(process.argv[2]))}catch(e){process.exit(2)}", str(GUARD), str(tmp_path)],
        input=json.dumps({"tool_name": "ImageGen", "tool_input": {**expected, **change}}).encode(), capture_output=True)
    assert result.returncode == 2
    assert not (tmp_path / "tool-claimed").exists()


@pytest.mark.skipif(not NODE.exists(), reason="requires Node")
@pytest.mark.parametrize("revision", [False, True, None])
def test_guard_keeps_prompt_revision_disabled(tmp_path, revision):
    expected = {"image1": str(tmp_path / "input.png"), "output_dir": str(tmp_path / "output"), "prompt": "fixed", "size": "1024x1024", "revise": False}
    (tmp_path / "guard.json").write_text(json.dumps(expected), encoding="utf-8")
    supplied = dict(expected)
    if revision is None: supplied.pop('revise')
    else: supplied['revise'] = revision
    result = subprocess.run([str(NODE), "-e", "try{process.exit(require(process.argv[1]).guard(process.argv[2]))}catch(e){process.exit(2)}", str(GUARD), str(tmp_path)],
        input=json.dumps({"tool_name": "ImageGen", "tool_input": supplied}).encode(), capture_output=True)
    assert result.returncode == (0 if revision is False else 2)
    assert (tmp_path / "tool-claimed").exists() == (revision is False)


@pytest.mark.skipif(not NODE.exists(), reason="requires Node")
def test_guard_exactly_once_and_missing_module_fails_closed(tmp_path):
    expected = {"image1": str(tmp_path / "input.png"), "output_dir": str(tmp_path / "output"), "prompt": "fixed", "size": "1024x1024"}
    (tmp_path / "guard.json").write_text(json.dumps(expected), encoding="utf-8")
    expression = "try{process.exit(require(process.argv[1]).guard(process.argv[2]))}catch(e){process.exit(2)}"
    args = [str(NODE), "-e", expression, str(GUARD), str(tmp_path)]
    event = json.dumps({"tool_name": "ImageGen", "tool_input": expected}).encode()
    assert subprocess.run(args, input=event, capture_output=True).returncode == 0
    assert subprocess.run(args, input=event, capture_output=True).returncode == 2
    assert subprocess.run([str(NODE), "-e", expression, str(tmp_path / "missing.cjs"), str(tmp_path)], input=event, capture_output=True).returncode == 2


@pytest.mark.skipif(not Path('C:/Program Files/Git/bin/bash.exe').exists() or not NODE.exists(), reason="requires actual Windows hook shell")
def test_official_hook_shell_command_handles_spaces_unicode_and_closed_errors(tmp_path):
    directory = tmp_path / "中文 空格"
    directory.mkdir()
    expected = {"image1": str(directory / "input.png"), "output_dir": str(directory / "output"), "prompt": "fixed", "size": "1024x1024"}
    (directory / "guard.json").write_text(json.dumps(expected), encoding="utf-8")
    event = json.dumps({"tool_name": "ImageGen", "tool_input": expected}).encode()
    def invoke(guard):
        return subprocess.run(['C:/Program Files/Git/bin/bash.exe', '-lc', hook_command(NODE, guard, directory)], input=event, capture_output=True).returncode
    assert invoke(GUARD) == 0
    assert invoke(GUARD) == 2
    assert invoke(directory / 'missing.cjs') == 2
