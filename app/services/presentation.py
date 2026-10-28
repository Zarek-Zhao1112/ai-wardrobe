"""§23：展示图单独存放，采用或撤回均不改变识别源图与属性。"""
from __future__ import annotations

from pathlib import Path
import tempfile
import uuid

from .. import db
from . import images, items


class PresentationError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def select(item_id, user_id, selected):
    with db.conn(immediate=True) as connection:
        row = connection.execute("SELECT * FROM clothing_items WHERE id=? AND user_id=?", (item_id, user_id)).fetchone()
        if not row: raise PresentationError("单品不存在", 404)
        if selected and (not row["display_image_path"] or row["display_source_hash"] != row["image_hash"] or not images.read_image(row["display_image_path"])):
            raise PresentationError("当前来源没有可用的平铺图，请重新生成", 409)
        connection.execute("UPDATE clothing_items SET display_selected=?,updated_at=? WHERE id=?", (1 if selected else 0, db.now_iso(), item_id))
        return items.to_out(connection.execute("SELECT * FROM clothing_items WHERE id=?", (item_id,)).fetchone())


def attach(item_id, user_id, data, source_hash, *, only_if_empty=False, expected_display_path=None):
    sniffed = images.sniff(data)
    if not data or len(data) > images.MAX_IMAGE_BYTES or not sniffed: raise PresentationError("平铺图为空、格式无效或超过10MB")
    filename = images.image_path_of(f"{item_id}.display-{uuid.uuid4().hex}", sniffed[1])
    db.IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    target, staging, installed, committed = db.IMAGES_DIR / filename, None, False, False
    old = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".flatlay-", suffix=".staging", dir=db.IMAGES_DIR, delete=False) as handle:
            staging = Path(handle.name)
            handle.write(data)
        with db.conn(immediate=True) as connection:
            row = connection.execute("SELECT * FROM clothing_items WHERE id=? AND user_id=?", (item_id, user_id)).fetchone()
            if not row: raise PresentationError("单品不存在", 404)
            if not source_hash or source_hash != row["image_hash"]: raise PresentationError("源照片已更换，请基于新照片生成", 409)
            if expected_display_path is not None and row["display_image_path"] != expected_display_path:
                raise PresentationError("这件衣物的平铺图已更换，请保留当前展示", 409)
            if only_if_empty and row["display_image_path"]:
                raise PresentationError("这件衣物已有新的展示图，请保留当前选择", 409)
            old = row["display_image_path"]
            connection.execute("UPDATE clothing_items SET display_image_path=?,display_source_hash=?,display_model=?,display_selected=1,updated_at=? WHERE id=?",
                (filename, source_hash, "hunyuan-image-alpha-edit", db.now_iso(), item_id))
            staging.replace(target)
            installed = True
            result = items.to_out(connection.execute("SELECT * FROM clothing_items WHERE id=?", (item_id,)).fetchone())
        committed = True
        if old and old != filename and images.is_derivative(item_id, old):
            try: (db.IMAGES_DIR / old).unlink(missing_ok=True)
            except OSError: pass
        return result
    finally:
        if installed and not committed:
            try: target.unlink(missing_ok=True)
            except OSError: pass
        if staging:
            try: staging.unlink(missing_ok=True)
            except OSError: pass
