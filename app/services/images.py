"""图片落盘：校验 → 存文件 → 回写 `image_path` / `image_hash`。

**为什么要嗅探文件头而不是信 `content_type`**
浏览器/客户端的 MIME 经常不准（微信转发的图片有时报 `application/octet-stream`），
而按后缀名判断更不可靠。所以这里读**魔数**：JPEG 是 `FF D8 FF`、PNG 是 `89 50 4E 47`……
认不出的一律拒收，不猜。

---

⚠️ **`image_hash` 用内容 sha256，不是 pHash（技术债）**

感知哈希能认出「同一张图的不同压缩版本」，但它需要 Pillow 做图像解码 ——
本项目没装 Pillow，也不想为一个去重功能引入图像库。sha256 只能认出**字节完全相同**的文件。

取舍说清楚：
- **够用**：用户从相册反复导入同一张原图，字节是一样的
- **不够用**：微信转发会重新压缩图片，同一张衣服照片两次导入可能算出不同 hash

所以 `image_hash` 唯一索引只是**第一道防线**，不是「绝对不会重复录入」的保证。
真正的防重在交互层（导入前把清单摆给用户确认）。
上收到 pHash 的时机：P2 做前端时（那时才需要处理"用户自己从相册选图"）。
"""

from __future__ import annotations

import hashlib
import logging
import re
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from .. import db

logger = logging.getLogger(__name__)

#: 单张图片大小上限。手机直出照片通常 3~8 MB，10 MB 留了余量。
MAX_IMAGE_BYTES: int = 10 * 1024 * 1024

#: 接受的图片类型 → 落盘扩展名
MAGIC_TYPES: tuple[tuple[bytes, str, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"GIF87a", "image/gif", ".gif"),
    (b"GIF89a", "image/gif", ".gif"),
    (b"RIFF", "image/webp", ".webp"),  # 还需在偏移 8 处看到 WEBP，见 sniff()
)


class ImageError(ValueError):
    """图片不合法（太大 / 类型不认 / 内容为空）。调用方转成 400。"""


def sniff(data: bytes) -> Optional[tuple[str, str]]:
    """读文件头判断类型。返回 `(mime, 扩展名)`；认不出返回 `None`。"""
    if not data:
        return None
    for magic, mime, ext in MAGIC_TYPES:
        if data.startswith(magic):
            if mime == "image/webp" and data[8:12] != b"WEBP":
                continue
            return mime, ext
    return None


def content_hash(data: bytes) -> str:
    """内容指纹（sha256 前 32 位）。加前缀是为了将来换算法时能分辨新旧值。"""
    return "sha256:" + hashlib.sha256(data).hexdigest()[:32]


@contextmanager
def temporary_image(data: bytes | None) -> Iterator[Path | None]:
    """§12：原图只在本次模型请求中可读，校验后写系统临时目录并清理。"""
    if data is None:
        yield None
        return
    if not data:
        raise ImageError("参考原图内容为空")
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageError("参考原图太大，上限 10 MB")
    sniffed = sniff(data)
    if sniffed is None:
        raise ImageError("认不出参考原图格式（只支持 JPEG / PNG / GIF / WebP）")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="wardrobe-reference-", suffix=sniffed[1], delete=False) as fh:
            temporary = Path(fh.name)
            fh.write(data)
        yield temporary
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                logger.warning("参考原图临时文件暂时无法清理")


#: 文件名里不许出现的字符：两种路径分隔符 + Windows 保留字符。
_UNSAFE_ID_CHARS = set('\\/:*?"<>|')


def image_path_of(item_id: str, ext: str) -> str:
    """单品的图片文件名（相对 `data/images`）。

    一个单品只保留一张图 —— 重新上传就是覆盖，所以文件名里不带 hash。

    ⚠️ **`item_id` 必须消毒**：它直接来自 URL 路径段。
    `Path("a\\b")` 在 Windows 上被当成子目录 → `save_image` 的暂存写入指向
    不存在的目录 → `FileNotFoundError` → **500**（实测复现）。
    更糟的是那个写入发生在归属校验**之前**，等于让调用方控制写路径。

    这里拦下来抛 `LookupError`，路由层会翻成 404「单品不存在」——
    既正确又不对探测者透露更多信息。
    （`read_image` 早就用 `Path(...).name` 消毒了，这里是同一类问题的另一半。）
    """
    if not item_id or item_id in (".", ".."):
        # 消息**不回显原始值** —— 它是用户可控输入，回显会变成一个反射面
        # （前端 renderError 虽然过了 esc()，但少一处反射总是更稳）。
        # 也不区分"格式非法"和"真不存在"：语义上都是"这个 id 我不处理"。
        raise LookupError("单品 id 不合法")
    if any(ch in item_id for ch in _UNSAFE_ID_CHARS):
        raise LookupError("单品 id 不合法（含路径分隔符或保留字符）")
    # 第二道：即便将来放开某些字符，也不允许跨出 images 目录
    if Path(item_id).name != item_id:
        raise LookupError("单品 id 不合法（不允许路径）")
    return f"{item_id}{ext}"


def save_image(
    user_id: str,
    item_id: str,
    data: bytes,
    *,
    check_owner: bool = True,
    original_data: bytes | None = None,
) -> dict:
    """把图片存进 `data/images/` 并回写单品的 `image_path` / `image_hash`。

    返回 `{"image_path", "image_hash", "bytes", "mime"}`。

    抛 `ImageError`（内容不合法）或 `LookupError`（单品不属于该用户）。
    `sqlite3.IntegrityError` 会**原样抛出** —— 那是「同一张图已经录过」的信号，
    由路由层翻译成对用户友好的 400，而不是在这里吞掉。
    """
    if not data:
        raise ImageError("图片内容为空")
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageError(
            f"图片太大（{len(data) / 1024 / 1024:.1f} MB），上限 {MAX_IMAGE_BYTES // 1024 // 1024} MB"
        )

    sniffed = sniff(data)
    if sniffed is None:
        raise ImageError("认不出图片格式（只支持 JPEG / PNG / GIF / WebP）")
    mime, ext = sniffed

    db.IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    digest = content_hash(data)
    image_path_of(item_id, ext)
    if original_data is not None and (not original_data or len(original_data) > MAX_IMAGE_BYTES or not sniff(original_data)):
        raise ImageError("参考原图格式或大小不支持")
    staging = target = original_staging = original_target = None
    installed = committed = False
    previous_paths = []
    try:
        with tempfile.NamedTemporaryFile(prefix=".image-upload-", suffix=".staging", dir=db.IMAGES_DIR, delete=False) as handle:
            staging = Path(handle.name)
            handle.write(data)
        if original_data is not None and original_data != data:
            with tempfile.NamedTemporaryFile(prefix=".image-original-", suffix=".staging", dir=db.IMAGES_DIR, delete=False) as handle:
                original_staging = Path(handle.name)
                handle.write(original_data)
        # §23：安装新来源与清空旧衍生引用在同一写事务中，失败不破坏旧来源/采用状态。
        with db.conn(immediate=True) as connection:
            sql = "SELECT * FROM clothing_items WHERE id=?" + (" AND user_id=?" if check_owner else "")
            args = (item_id, user_id) if check_owner else (item_id,)
            old = connection.execute(sql, args).fetchone()
            if not old: raise LookupError("单品不存在")
            filename = image_path_of(item_id if not old["image_path"] else f"{item_id}.source-{uuid.uuid4().hex}", ext)
            target = db.IMAGES_DIR / filename
            original_name = filename
            if original_staging:
                original_name = image_path_of(f"{item_id}.original-{uuid.uuid4().hex}", sniff(original_data)[1])
                original_target = db.IMAGES_DIR / original_name
            previous_paths = [old["image_path"], old["original_image_path"], old["display_image_path"]]
            connection.execute("UPDATE clothing_items SET image_path=?,image_hash=?,updated_at=?,original_image_path=?,display_image_path=NULL,display_source_hash=NULL,display_model=NULL,display_selected=0 WHERE id=?",
                (filename, digest, db.now_iso(), original_name, item_id))
            staging.replace(target)
            installed = True
            if original_staging: original_staging.replace(original_target)
            row = connection.execute("SELECT image_path,image_hash FROM clothing_items WHERE id=?", (item_id,)).fetchone()
        committed = True
    except OSError as error:
        raise ImageError("图片写盘失败，请重试；旧照片仍保留") from error
    finally:
        if installed and not committed and target:
            try: target.unlink(missing_ok=True)
            except OSError: pass
        if staging:
            try: staging.unlink(missing_ok=True)
            except OSError: pass
        if original_staging:
            try: original_staging.unlink(missing_ok=True)
            except OSError: pass
        if original_target and not committed:
            try: original_target.unlink(missing_ok=True)
            except OSError: pass
    # 只清本轮捕获的旧版本，不能扫描删掉并发刚生成的新展示图。
    for name in set(previous_paths):
        if name and name not in (filename, original_name) and is_item_image(item_id, name):
            try: (db.IMAGES_DIR / name).unlink(missing_ok=True)
            except OSError: logger.warning("旧版本图片暂时无法清理")
    return {"image_path": row["image_path"], "image_hash": row["image_hash"], "bytes": len(data), "mime": mime}


def read_image(image_path: str) -> Optional[Path]:
    """按相对路径取得磁盘上的文件。路径做了越界防护（不允许 `..` 或绝对路径）。"""
    if not image_path:
        return None
    name = Path(image_path).name  # 只取文件名，天然挡掉 `../` 和绝对路径
    candidate = db.IMAGES_DIR / name
    return candidate if candidate.is_file() else None


#: 单品图片允许的后缀。与 `MAGIC_TYPES` 对齐 —— 清理时只认这几种，
#: 免得将来目录里放了 `<id>.json` 之类的边车文件被顺手删掉。
_IMAGE_SUFFIXES: frozenset[str] = frozenset({".jpg", ".png", ".gif", ".webp"})


def is_derivative(item_id: str, name: str) -> bool:
    """§23：精确判定归属，不允许路径/通配符扩大删除范围。"""
    path = Path(name)
    return path.name == name and path.suffix.lower() in _IMAGE_SUFFIXES and bool(re.fullmatch(re.escape(item_id) + r"\.(?:original|display)-[0-9a-f]{32}", path.stem))


def is_item_image(item_id: str, name: str) -> bool:
    path = Path(name)
    return path.name == name and path.suffix.lower() in _IMAGE_SUFFIXES and bool(re.fullmatch(re.escape(item_id) + r"(?:\.(?:source|import|original|display)-[0-9a-f]{32})?", path.stem))


def _remove_by_stem(
    item_id: str, *, keep: Optional[str] = None, include_staging: bool = True
) -> list[str]:
    """删掉该单品的图片（含§14的独立尝试文件），返回被删的文件名。

    归属判定用精确主干/严格尝试后缀 + 图片后缀白名单，**不用 glob 拼 pattern**：
    `image_path_of` 只拦了 `\\ / : * ? " < > |`，`[`、`]` 是放行的，
    拼成 pattern 会按字符类匹配到别人的文件。

    顺手清暂存文件 `.<item_id>.<ext>.staging`：实测它们的 stem 是
    `it_x.jpg`（带点的变成 `.it_x.jpg`），**永远不等于 item_id**，
    只靠 stem 判据会一直漏，删一件就留下一个可能几 MB 的残片。

    永不抛异常（调用方多半是在数据库已经改完之后才调它）。
    """
    if not item_id or item_id in (".", "..") or Path(item_id).name != item_id:
        return []
    try:
        entries = list(db.IMAGES_DIR.iterdir())
    except OSError as e:  # 目录不存在 / 读不了
        logger.debug("跳过图片清理，读不了目录 item=%s: %s", item_id, e)
        return []

    removed: list[str] = []
    for path in entries:
        if not path.is_file():
            continue
        name = path.name
        if name == keep:
            continue
        # §14：原子录入的尝试文件带随机后缀，换图/删单品时一起清理。
        imported = item_id.startswith("it_import_") and re.fullmatch(re.escape(item_id) + r"\.import-[0-9a-f]{32}", path.stem)
        mine = is_item_image(item_id, name)
        staging = include_staging and name.startswith(f".{item_id}.") and name.endswith(".staging")
        if not (mine or staging):
            continue
        try:
            path.unlink()
        except OSError as e:
            # 留个线索：静默失败的话，空间泄漏会再次变成"没人知道为什么少不掉"
            logger.warning("删除图片失败 item=%s file=%s: %s", item_id, name, e)
            continue
        removed.append(name)
    return removed


def remove_item_images(item_id: str) -> list[str]:
    """删掉某个单品在磁盘上的图片文件，返回被删掉的文件名。

    **为什么要单独一个函数**（2026-10-22）：删除单品原先只跑一条 SQL，
    图片文件谁都不管 —— 删一件就在 `data/images/` 里留一个几 MB 的孤儿
    （桌面端实测已经躺着一个 797 KB 的、行都没了；手机端删一件留 4 MB）。
    按钮文案写的是「删掉就找不回来了」，那就连图一起删干净。

    **永不抛异常**：调用它的时候数据库那一行**已经删掉了**。清文件失败
    （文件被占用、权限、目录没了）不该让「删除单品」变成失败 —— 那会让
    用户以为没删掉、再点一次，然后收到 404。所以这里只记日志、不抛。

    换过扩展名的旧文件（jpg → png）、以及写盘中断留下的暂存文件，也一起清。

    ⚠️ 本项目**没有缩略图**（`thumbnail_path` 列存在但从未被写入），
    所以一个单品在磁盘上只有这一份原图。
    """
    removed = _remove_by_stem(item_id, include_staging=True)
    if removed:
        logger.info("删除单品 %s 时清掉 %d 个图片文件：%s", item_id, len(removed), "、".join(removed))
    return removed
