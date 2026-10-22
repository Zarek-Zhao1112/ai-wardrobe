"""删除单品要连图片一起删干净（A1 决策，2026-10-22）。

**为什么补这组用例**：`DELETE /items/{id}` 原先只跑一条 SQL，磁盘上的
`data/images/<item_id>.jpg` 谁都不管 ——
- 桌面端实测已经躺着一个 797 KB 的孤儿文件（数据库里那行早没了）
- 手机端删一件留 4 MB（相机原图）

按钮文案写的是「删掉就找不回来了」，那语义就该是**真删**：记录和图片一起清。

⚠️ 这些用例把 `IMAGES_DIR` 指到 `tmp_path`。`conftest.py` 其实已经把
`WARDROBE_DB` 隔离到临时目录、`IMAGES_DIR` 跟着一起隔离了，这里再指定一次
是为了**不依赖那个推导链**（断言的文件路径必须是本用例自己的目录）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import images, items as items_svc


def png(tag: str) -> bytes:
    """最小可用 PNG。每个用例用**不同字节** —— `image_hash` 有唯一索引。"""
    return b"\x89PNG\r\n\x1a\n" + tag.encode() + b"\x00" * 60


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def img_dir(tmp_path, monkeypatch) -> Path:
    """把图片目录整个换到临时目录（`images.py` 用的是 `db.IMAGES_DIR`）。"""
    d = tmp_path / "images"
    d.mkdir()
    monkeypatch.setattr(images.db, "IMAGES_DIR", d)
    return d


def _new_item(client, name: str) -> str:
    return client.post("/items", json={"type": "tshirt", "name": name}).json()["item"]["id"]


def _upload(client, item_id: str, tag: str) -> str:
    r = client.post(
        f"/items/{item_id}/image",
        files={"file": ("p.png", png(tag), "image/png")},
    )
    assert r.status_code == 200, r.text
    return r.json()["image_path"]


class TestDeleteRemovesImages:
    def test_delete_removes_the_image_file(self, client, img_dir):
        """核心断言：删完这件，磁盘上那张图必须没了。"""
        item_id = _new_item(client, "删图-单张")
        name = _upload(client, item_id, "del-one")
        assert (img_dir / name).is_file()

        assert client.delete(f"/items/{item_id}").status_code == 200

        assert not (img_dir / name).exists(), "★ 数据库行删了，图片文件还躺在磁盘上（孤儿）"

    def test_delete_removes_stale_extension_files_too(self, client, img_dir):
        """换过扩展名的旧文件也一起清 —— 它们同样只属于这一件。"""
        item_id = _new_item(client, "删图-旧扩展名")
        name = _upload(client, item_id, "del-stale")
        stale = img_dir / f"{item_id}.jpg"  # 模拟 jpg→png 换扩展名后的残留
        stale.write_bytes(png("del-stale-old"))

        client.delete(f"/items/{item_id}")

        assert not (img_dir / name).exists()
        assert not stale.exists(), "换扩展名留下的旧文件没被清掉"

    def test_delete_leaves_other_items_images_alone(self, client, img_dir):
        """只删这一件的图 —— 按 item_id 判归属，别连坐。"""
        keep_id = _new_item(client, "删图-别的件")
        drop_id = _new_item(client, "删图-要删的")
        keep = _upload(client, keep_id, "del-keep")
        drop = _upload(client, drop_id, "del-drop")

        client.delete(f"/items/{drop_id}")

        assert not (img_dir / drop).exists()
        assert (img_dir / keep).is_file(), "把别的单品的图删了"

    def test_delete_cleans_staging_leftovers(self, client, img_dir):
        """写盘中断留下的暂存文件也要清。

        实测（`Path("it_x.jpg.staging").stem`）它的 stem 是 `it_x.jpg`、
        带点的 `.it_x.jpg.staging` 的 stem 是 `.it_x.jpg` —— **都不等于
        `it_x`**。只按 stem 判据的话，删一件就留一个可能几 MB 的残片。
        """
        item_id = _new_item(client, "删图-暂存残留")
        name = _upload(client, item_id, "del-staging")
        staging = img_dir / f".{name}.staging"
        staging.write_bytes(png("del-staging-left"))

        client.delete(f"/items/{item_id}")

        assert not (img_dir / name).exists()
        assert not staging.exists(), "暂存文件 `.it_x.jpg.staging` 没被清掉"

    def test_delete_keeps_non_image_sidecar_files(self, client, img_dir):
        """只清图片后缀。同名的边车文件（比如 `<id>.json`）不该被顺手删掉。"""
        item_id = _new_item(client, "删图-边车文件")
        name = _upload(client, item_id, "del-sidecar")
        sidecar = img_dir / f"{item_id}.json"
        sidecar.write_text('{"note": "边车"}', encoding="utf-8")

        client.delete(f"/items/{item_id}")

        assert not (img_dir / name).exists()
        assert sidecar.is_file(), "把非图片文件也删了"

    def test_missing_item_deletes_nothing(self, client, img_dir):
        """id 不存在 → 404，且不许碰目录里的任何文件。

        越权/打错 id 时顺手删文件，是最不该发生的那类事故。

        ⚠️ 这里必须**先造一个 stem 恰好等于请求 id 的孤儿文件**（2026-10-22
        审查指出原写法恒真）：否则用的文件名属于另一件真实单品，就算实现
        写成"无条件清目录"，stem 也对不上、断言照样通过 —— 等于没测。
        """
        ghost_id = "it_这个id不存在"
        orphan = img_dir / f"{ghost_id}.jpg"
        orphan.write_bytes(png("del-ghost-orphan"))

        r = client.delete(f"/items/{ghost_id}")
        assert r.status_code == 404
        assert orphan.is_file(), "删一个不存在的 id 却把同名孤儿文件清掉了"

    def test_wrong_owner_does_not_delete_file(self, client, img_dir):
        """服务层给错 user_id → 返回 False，图片原封不动。"""
        item_id = _new_item(client, "删图-越权")
        name = _upload(client, item_id, "del-owner")

        assert items_svc.delete(item_id, user_id="u_别人") is False

        assert (img_dir / name).is_file(), "越权删除不该顺手删掉别人的图"


class TestDeleteSurvivesCleanupFailure:
    def test_unlink_failure_does_not_fail_the_delete(self, client, img_dir, monkeypatch):
        """清文件失败（占用 / 权限）不能让「删除单品」变失败。

        数据库那一行**已经删掉了**。这时候报错的话，用户会以为没删掉、
        再点一次 → 404，然后以为数据坏了。
        """
        item_id = _new_item(client, "删图-删不掉")
        name = _upload(client, item_id, "del-eacces")
        assert (img_dir / name).is_file()

        real_unlink = Path.unlink

        def boom(self, *a, **kw):
            if self.name == name:
                raise OSError("文件被占用（测试模拟）")
            return real_unlink(self, *a, **kw)

        monkeypatch.setattr(Path, "unlink", boom)

        r = client.delete(f"/items/{item_id}")

        assert r.status_code == 200, "清文件失败把删除单品带崩了"
        assert client.get(f"/items/{item_id}").status_code == 404, "数据库那行应该已经删掉"

    def test_missing_images_dir_is_not_fatal(self, client, tmp_path, monkeypatch):
        """图片目录整个不在（新装 / 被挪走）也不能让删除 500。"""
        gone = tmp_path / "no_such_dir"
        monkeypatch.setattr(images.db, "IMAGES_DIR", gone)

        item_id = _new_item(client, "删图-没目录")

        assert client.delete(f"/items/{item_id}").status_code == 200
