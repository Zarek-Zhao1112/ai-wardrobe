"""图片链路测试：类型校验 / 落盘 / 回写 / 清理。

**为什么类型校验要测这么细**：它是唯一挡住"用户传了个 PDF 当衣服照片"的地方，
而且失败方式很隐蔽 —— 文件写进去了，只是前端显示不出来。

⚠️ 每个用例用**不同的图片字节**（`jpeg("tag")` 的那个 tag）。
`image_hash` 上有唯一索引，同一用户不允许两个单品用同一张图 ——
那是刻意的防重复录入语义，所以多个测试共用一副字节会互相撞。
"""

from __future__ import annotations

import pytest

from app import db
from app.models import ItemCreate
from app.services import images
from app.services import items as item_svc


def jpeg(tag: str) -> bytes:
    return b"\xff\xd8\xff\xe0" + tag.encode() + b"\x00" * 100


def png(tag: str) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + tag.encode() + b"\x00" * 100


def _new_item(uid: str, name: str) -> str:
    return item_svc.create(uid, ItemCreate(type="tshirt", name=name))["id"]


class TestSniff:
    def test_jpeg(self):
        assert images.sniff(jpeg("x")) == ("image/jpeg", ".jpg")

    def test_png(self):
        assert images.sniff(png("x")) == ("image/png", ".png")

    def test_gif(self):
        assert images.sniff(b"GIF89a" + b"\x00" * 10) == ("image/gif", ".gif")

    def test_webp_needs_the_marker_at_offset_8(self):
        """`RIFF` 开头的东西很多（wav / avi），必须再看偏移 8 处是不是 WEBP。"""
        assert images.sniff(b"RIFF" + b"\x00" * 4 + b"NOPE") is None
        assert images.sniff(b"RIFF" + b"\x00" * 4 + b"WEBP") == ("image/webp", ".webp")

    def test_unknown_content(self):
        assert images.sniff(b"%PDF-1.4 fake pdf") is None

    def test_empty(self):
        assert images.sniff(b"") is None


class TestContentHash:
    def test_stable(self):
        assert images.content_hash(b"abc") == images.content_hash(b"abc")

    def test_differs_on_content(self):
        assert images.content_hash(b"abc") != images.content_hash(b"abd")

    def test_is_namespaced(self):
        """带算法前缀是为了将来换哈希算法时能分辨新旧值。"""
        assert images.content_hash(b"abc").startswith("sha256:")


class TestSaveImage:
    def test_saves_file_and_writes_back(self, uid):
        item_id = _new_item(uid, "正常上传")
        payload = jpeg("save-happy")
        result = images.save_image(uid, item_id, payload)

        assert result["image_path"] == f"{item_id}.jpg"
        assert result["mime"] == "image/jpeg"
        assert result["bytes"] == len(payload)
        assert (db.IMAGES_DIR / result["image_path"]).is_file()

        item = item_svc.get(item_id, uid)
        assert item["image_path"] == result["image_path"]
        assert item["image_hash"] == result["image_hash"]

    def test_rejects_non_image(self, uid):
        item_id = _new_item(uid, "非图")
        with pytest.raises(images.ImageError, match="认不出图片格式"):
            images.save_image(uid, item_id, b"%PDF-1.4 not an image")

    def test_rejects_empty(self, uid):
        item_id = _new_item(uid, "空内容")
        with pytest.raises(images.ImageError, match="为空"):
            images.save_image(uid, item_id, b"")

    def test_rejects_oversize(self, uid, monkeypatch):
        item_id = _new_item(uid, "超大图")
        monkeypatch.setattr(images, "MAX_IMAGE_BYTES", 10)
        with pytest.raises(images.ImageError, match="太大"):
            images.save_image(uid, item_id, jpeg("oversize"))

    def test_unknown_item_does_not_leave_a_file_behind(self, uid):
        """单品不存在时要把刚落盘的文件收回去 —— 否则 images/ 会堆孤儿文件。"""
        with pytest.raises(LookupError):
            images.save_image(uid, "it_根本不存在", jpeg("unknown-item"))
        assert not (db.IMAGES_DIR / "it_根本不存在.jpg").exists()

    def test_reupload_switches_extension_and_removes_the_old_file(self, uid):
        """换成不同格式重传时，旧扩展名的文件必须清掉。"""
        item_id = _new_item(uid, "换格式")
        first = images.save_image(uid, item_id, jpeg("reupload-a"))
        second = images.save_image(uid, item_id, png("reupload-b"))

        assert second["image_path"].endswith(".png")
        assert not (db.IMAGES_DIR / first["image_path"]).exists()

    def test_hash_collision_keeps_the_old_state_intact(self, uid):
        """★ 撞唯一索引时，**不能**出现「旧文件没了、新记录也没写上」。

        `image_hash` 是唯一的（防重复录入），所以给第二个单品传同一张图会失败。
        失败时磁盘上**不该留下任何这次写的痕迹**。
        """
        payload = jpeg("collision")
        item_a = _new_item(uid, "先占图")
        item_b = _new_item(uid, "后撞图")
        images.save_image(uid, item_a, payload)

        import sqlite3

        with pytest.raises(sqlite3.IntegrityError):
            images.save_image(uid, item_b, payload)

        assert not (db.IMAGES_DIR / f"{item_b}.jpg").exists(), "失败时不该留下半成品文件"
        assert item_svc.get(item_b, uid)["image_path"] in (None, "")


class TestReadImage:
    def test_rejects_path_traversal(self):
        """只取文件名，天然挡掉 `../` 和绝对路径。"""
        assert images.read_image("../../etc/passwd") is None
        assert images.read_image("") is None
        assert images.read_image(None) is None

    def test_missing_file_returns_none(self):
        assert images.read_image("nope.jpg") is None
