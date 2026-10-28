"""§14：照片与确认属性整件提交，稳定请求标识用于失败重试。"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import tempfile
import uuid
from pathlib import Path

from .. import db, taxonomy
from ..models import ItemCreate
from . import images, items

logger = logging.getLogger(__name__)

def _remove(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("逐件录入暂存图片未能清理")

class ImportError(ValueError):
    def __init__(self, message: str, *, status: int = 400, code: str = "invalid_import", item_id: str | None = None):
        super().__init__(message)
        self.status, self.code, self.item_id = status, code, item_id

def item_id_for(user_id: str, token: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        raise ImportError("录入标识无效，请重新选择这张照片")
    return "it_import_" + hashlib.sha256(f"{user_id}:{token}".encode()).hexdigest()[:32]

def _reject_constant(value: str):
    raise ValueError("数值必须是有限数字")

def parse_payload(value: str) -> ItemCreate:
    if len(value) > 65536:
        raise ImportError("单件属性内容过长，请缩短备注后重试")
    try:
        raw = json.loads(value, parse_constant=_reject_constant)
        if not isinstance(raw, dict):
            raise ValueError
        payload = ItemCreate.model_validate(raw)
        # 路径和指纹由服务器根据本次照片生成，客户端不能指定。
        payload = payload.model_copy(update={"image_path": None, "image_hash": None})
        json.dumps(payload.model_dump(mode="json"), allow_nan=False)
        return payload
    except (ValueError, TypeError, OverflowError):
        raise ImportError("属性格式不正确，请检查品类和填写内容") from None

def lookup(user_id: str, token: str) -> dict | None:
    return items.get(item_id_for(user_id, token), user_id)

def commit(user_id: str, token: str, payload: ItemCreate, data: bytes, *, original_data: bytes | None = None, display_data: bytes | None = None) -> dict:
    tid = item_id_for(user_id, token)
    def checked(value):
        if not value or len(value) > images.MAX_IMAGE_BYTES:
            raise ImportError("照片为空或超过10 MB，请换一张较小的图片")
        sniffed = images.sniff(value)
        if not sniffed:
            raise ImportError("图片格式不支持，请选择 JPEG / PNG / GIF / WebP")
        return sniffed
    sniffed = checked(data)
    if original_data is not None: checked(original_data)
    if display_data is not None: checked(display_data)
    digest = images.content_hash(data)
    canonical = json.dumps(payload.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, allow_nan=False)
    # §23：去重仍按真实裁剪图；提交身份额外冻结原图/展示图，重试不能更换已选版本。
    identity = canonical + digest
    if original_data is not None: identity += ":original:" + images.content_hash(original_data)
    if display_data is not None: identity += ":display:" + images.content_hash(display_data)
    fingerprint = hashlib.sha256(identity.encode()).hexdigest()
    filename = images.image_path_of(f"{tid}.import-{uuid.uuid4().hex}", sniffed[1])
    original_name, display_name = filename, None
    files = [(filename, data)]
    if original_data is not None and original_data != data:
        original_name = images.image_path_of(f"{tid}.original-{uuid.uuid4().hex}", checked(original_data)[1])
        files.append((original_name, original_data))
    if display_data is not None:
        display_name = images.image_path_of(f"{tid}.display-{uuid.uuid4().hex}", checked(display_data)[1])
        files.append((display_name, display_data))
    db.IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    staged, installed, committed = [], [], False
    try:
        for name, content in files:
            with tempfile.NamedTemporaryFile(prefix=".photo-import-", suffix=".staging", dir=db.IMAGES_DIR, delete=False) as handle:
                staging = Path(handle.name)
                staged.append((staging, db.IMAGES_DIR / name))
                handle.write(content)
        with db.conn(immediate=True) as connection:
            previous = connection.execute("SELECT * FROM clothing_items WHERE id=? AND user_id=?", (tid, user_id)).fetchone()
            if previous:
                previous = items.to_out(previous)
                marker = previous["tags"].get("_photo_import")
                if not isinstance(marker, dict) or marker.get("fingerprint") != fingerprint:
                    raise ImportError("这次提交已保存过，属性或展示图发生变化，请查看已保存单品", status=409, code="import_changed", item_id=tid)
                paths = [previous["image_path"], previous.get("original_image_path")]
                if previous.get("display_selected"): paths.append(previous.get("display_image_path"))
                if any(path and not images.read_image(path) for path in paths):
                    raise ImportError("单品已存在，但照片暂不可用，请到详情页补图", status=409, code="image_missing", item_id=tid)
                return {"item": previous, "replayed": True}
            duplicate = connection.execute("SELECT id FROM clothing_items WHERE user_id=? AND image_hash=?", (user_id, digest)).fetchone()
            if duplicate:
                raise ImportError("这张照片已经录入过，可以跳过或查看已有单品", status=409, code="duplicate_image", item_id=duplicate["id"])
            tags = dict(payload.tags)
            tags["_photo_import"] = {"fingerprint": fingerprint}
            final = payload.model_copy(update={"image_path": filename, "image_hash": digest, "tags": tags})
            if not final.name:
                final = final.model_copy(update={"name": items.auto_name({**final.model_dump(), "type_label": taxonomy.label_of(final.type)})})
            items.create(user_id, final, item_id=tid, connection=connection)
            connection.execute("UPDATE clothing_items SET original_image_path=?,display_image_path=?,display_source_hash=?,display_model=?,display_selected=? WHERE id=?",
                (original_name, display_name, digest if display_name else None, "hunyuan-image-alpha-edit" if display_name else None, 1 if display_name else 0, tid))
            for staging, target in staged:
                staging.replace(target)
                installed.append(target)
            created = items.to_out(connection.execute("SELECT * FROM clothing_items WHERE id=?", (tid,)).fetchone())
        committed = True
        return {"item": created, "replayed": False}
    finally:
        if not committed:
            for target in installed: _remove(target)
        for staging, _ in staged: _remove(staging)
