"""相册直读通道（`/items/album/*`）的回归测试。

**为什么单独一个文件**（2026-10-21）：详情页的「从手机相册换图 / 裁剪」接上
之后，这条通道第一次有了**写操作**的调用方。而在此之前它是 0 覆盖
（`grep -rn "album" tests/` 是空的）—— 白名单、后缀、错误码、返回体形状
全靠人眼看过一遍。

**为什么不碰真机**：把 `ALBUM_DIRS` 指到 `tmp_path`，用 base64 造 id 就够
验证那几道闸（`_album_path` 只认"白名单目录 + 图片后缀 + 真实文件"）。
`/sdcard` 只在 Android 上存在，测试不该依赖它。
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routes import items as routes_items


def png(tag: str) -> bytes:
    """最小可用 PNG。每个用例用**不同字节** —— `image_hash` 有唯一索引。"""
    return b"\x89PNG\r\n\x1a\n" + tag.encode() + b"\x00" * 60


def album_id(path: Path | str) -> str:
    """与 `routes/items.py::_album_id` 同算法。

    这里**故意不 import 那个私有函数**：id 的编码方式是前后端约定的一部分，
    测试跟着实现走就测不出"约定被改了"。
    """
    return base64.urlsafe_b64encode(str(path).encode("utf-8")).decode("ascii").rstrip("=")


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def album(tmp_path, monkeypatch):
    """一个假相册目录：`(目录, 造照片的函数)`。"""
    monkeypatch.setattr(routes_items, "ALBUM_DIRS", (str(tmp_path),))

    def make(name: str, data: bytes) -> Path:
        p = tmp_path / name
        p.write_bytes(data)
        return p

    return tmp_path, make


def _new_item(client, name: str) -> dict:
    return client.post("/items", json={"type": "tshirt", "name": name}).json()["item"]


class TestImportFromAlbum:
    def test_response_shape_matches_upload(self, client, album):
        """两个上传入口必须回**同一套键** —— 前端 `afterImage()` 只写了一份收尾。

        少 `tagging` 的话，详情页换完图不会提示打标结果（静默），
        而这正是"照片变了、属性也悄悄变了却没人说"的来源。
        """
        _, make = album
        photo = make("shape.png", png("album-shape"))
        a = _new_item(client, "相册形状A")
        b = _new_item(client, "相册形状B")

        from_album = client.post(
            f"/items/{a['id']}/image-from-album", json={"id": album_id(photo)}
        )
        uploaded = client.post(
            f"/items/{b['id']}/image",
            files={"file": ("x.png", png("album-shape-upload"), "image/png")},
        )

        assert from_album.status_code == 200
        assert uploaded.status_code == 200
        assert set(from_album.json()) == set(uploaded.json())
        assert "tagging" in from_album.json()
        # 测试环境把 VISION_MODEL 置空了（conftest），所以打标必然 skipped
        assert from_album.json()["tagging"]["status"] == "skipped"
        assert from_album.json()["item"]["image_path"] == from_album.json()["image_path"]

    def test_same_bytes_for_another_item_is_400_without_sql_noise(self, client, album):
        """图片指纹重复 → 400，且话里**不能带 SQL 原文**。

        原来拼的是 `UNIQUE constraint failed: clothing_items.user_id, …`，
        会原样出现在手机 toast 里（两行英文 + 表名）。
        """
        _, make = album
        photo = make("dup.png", png("album-dup"))
        a = _new_item(client, "相册重复A")
        b = _new_item(client, "相册重复B")

        first = client.post(f"/items/{a['id']}/image-from-album", json={"id": album_id(photo)})
        second = client.post(f"/items/{b['id']}/image-from-album", json={"id": album_id(photo)})

        assert first.status_code == 200
        assert second.status_code == 400
        detail = second.json()["detail"]
        assert "指纹重复" in detail
        assert "UNIQUE constraint" not in detail

    def test_non_image_suffix_is_404(self, client, album):
        """白名单目录里的 pdf / txt 一律不让直取。

        `ALBUM_DIRS` 含 `/sdcard/Download`，那里什么都有；id 是路径的 base64、
        格式在列表接口里公开 —— 不挡后缀就是同网段任意文件读取。
        """
        _, make = album
        doc = make("secret.pdf", b"%PDF-1.4 nothing to see")
        item = _new_item(client, "相册PDF")

        r = client.post(f"/items/{item['id']}/image-from-album", json={"id": album_id(doc)})

        assert r.status_code == 404
        # 同一份字节换成 .png 名字也不该成功（内容不是图）→ 400，不是 500
        fake = make("fake.png", b"not a png at all")
        r2 = client.post(f"/items/{item['id']}/image-from-album", json={"id": album_id(fake)})
        assert r2.status_code == 400
        assert "认不出图片格式" in r2.json()["detail"]

    def test_outside_album_dir_is_403(self, client, album):
        """相册目录之外的文件（这里拿测试文件自己当靶子）→ 403。"""
        item = _new_item(client, "相册越界")
        outside = Path(__file__).resolve()

        r = client.post(
            f"/items/{item['id']}/image-from-album", json={"id": album_id(outside)}
        )

        assert r.status_code == 403

    def test_missing_id_is_400(self, client, album):
        item = _new_item(client, "相册坏id")
        r = client.post(f"/items/{item['id']}/image-from-album", json={})

        assert r.status_code == 400
        assert "缺少照片 id" in r.json()["detail"]

    def test_null_byte_in_path_is_400_not_500(self, client, album):
        """id 里编码进 `\\x00` → 400。

        `os.path.realpath` 遇到 NUL 抛的是 `ValueError: embedded null byte`，
        它不是 `OSError` —— 不被那个 try 接住就是 500（可构造，虽然 UI 走不到）。
        """
        item = _new_item(client, "相册NUL")
        bad = base64.urlsafe_b64encode(b"/sdcard/DCIM/a\x00.png").decode().rstrip("=")

        r = client.post(f"/items/{item['id']}/image-from-album", json={"id": bad})

        # 只锁"**不能是 500**"，不锁具体码 —— 平台行为本来就不一样：
        #   · POSIX（真实运行环境）：`posixpath.realpath` 会 lstat → ValueError → 400
        #   · Windows（开发机跑测试）：`ntpath` 不抛 → 落到白名单判定 → 403
        # 两边都是"当场拒掉"，而 500 会让排查方向歪到"代码坏了"。
        assert r.status_code in (400, 403), r.text


class TestAlbumListing:
    def test_empty_album_reports_dirs_and_zero(self, client, album):
        """读不到照片时要说清"为什么是空的" —— `dirs` 是那唯一线索。"""
        r = client.get("/items/album/photos")

        assert r.status_code == 200
        data = r.json()
        assert data["available"] is False
        assert data["total"] == 0
        assert data["dirs"]  # 临时目录存在，所以不在"目录都没找到"那一类

    def test_lists_recent_photos_and_skips_non_images(self, client, album):
        _, make = album
        make("a.png", png("list-a"))
        make("b.jpg", png("list-b"))
        make("c.pdf", b"%PDF-1.4")

        data = client.get("/items/album/photos").json()

        names = {p["name"] for p in data["photos"]}
        assert names == {"a.png", "b.jpg"}
        assert data["available"] is True
        assert data["total"] == 2
        # 每项都要有 id（前端拿它去换图），且能直接取到字节
        first = data["photos"][0]
        served = client.get(f"/items/album/photo", params={"id": first["id"]})
        assert served.status_code == 200
