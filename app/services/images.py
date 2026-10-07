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
from pathlib import Path
from typing import Optional

from .. import db

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
    filename = image_path_of(item_id, ext)
    target = db.IMAGES_DIR / filename
    staging = db.IMAGES_DIR / f".{filename}.staging"

    # ⚠️ 顺序：写**暂存文件** → 更新数据库 → 成功后才替换正式文件。
    #
    # 为什么不直接写 `target`：如果这件单品本来就有图、扩展名又相同（jpg 换 jpg），
    # `write_bytes(target)` 会**当场覆盖掉原图**。万一数据库这一步失败
    # （比如 image_hash 撞了唯一索引）而在 except 里把 target 删掉 ——
    # 原图就凭空消失了，可数据库里 `image_path` 还指着它。
    # （这是 W4 审查抓到的数据丢失缺陷，原实现在这里踩了坑。）
    #
    # 写入也放进 `try`：路径消毒在 `image_path_of` 已做，但仍要保证
    # **任何异常都不留下半成品文件**（写盘中途失败会留一个残缺的暂存文件）。
    sql = "UPDATE clothing_items SET image_path=?, image_hash=?, updated_at=? WHERE id=?"
    args: list = [filename, digest, db.now_iso(), item_id]
    if check_owner:
        sql = sql.replace(" WHERE id=?", " WHERE id=? AND user_id=?")
        args.append(user_id)

    try:
        staging.write_bytes(data)
        with db.conn() as c:
            cur = c.execute(sql, args)
            if cur.rowcount == 0:
                raise LookupError("单品不存在")
            row = c.execute(
                "SELECT image_path, image_hash FROM clothing_items WHERE id=?", (item_id,)
            ).fetchone()
    except Exception:
        # 数据库没成（或写盘失败）—— 丢掉暂存文件即可。**原图原封不动。**
        staging.unlink(missing_ok=True)
        raise

    # 数据库已成功，这时才动正式文件
    staging.replace(target)          # 同目录内 rename，原子替换
    # 换过扩展名时（jpg → png）把旧文件清掉
    for stale in db.IMAGES_DIR.glob(f"{item_id}.*"):
        if stale.name != filename:
            stale.unlink(missing_ok=True)

    return {
        "image_path": row["image_path"],
        "image_hash": row["image_hash"],
        "bytes": len(data),
        "mime": mime,
    }


def read_image(image_path: str) -> Optional[Path]:
    """按相对路径取得磁盘上的文件。路径做了越界防护（不允许 `..` 或绝对路径）。"""
    if not image_path:
        return None
    name = Path(image_path).name  # 只取文件名，天然挡掉 `../` 和绝对路径
    candidate = db.IMAGES_DIR / name
    return candidate if candidate.is_file() else None
