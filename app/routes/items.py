"""衣橱单品路由。"""

from __future__ import annotations

import base64
import logging
import os
import re
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Body, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from .. import db, taxonomy
from ..models import (
    BulkItemsIn,
    BulkItemsOut,
    ItemCreate,
    ItemCreateOut,
    ItemListOut,
    ItemOut,
    ItemParseIn,
    ItemUpdate,
    ItemsParseOut,
    OkOut,
    WearEventOut,
    WearLogIn,
)
from ..services import images as image_svc
from ..services import photo_import
from ..services import presentation
from ..services import ingest as ingest_svc
from ..services import items as svc
from ..services import tagging as tagging_svc
from ..services import wardrobe
from ..services import wear as wear_svc

router = APIRouter(prefix="/items", tags=["items"])

logger = logging.getLogger(__name__)


def _uid() -> str:
    return db.get_default_user_id()


@router.post("/{item_id}/presentation", response_model=ItemOut)
def save_presentation(item_id: str, file: UploadFile = File(...), source_hash: str = Form(...), only_if_empty: bool = Form(False), expected_display_path: str | None = Form(None, max_length=512)):
    if file.size is not None and file.size > image_svc.MAX_IMAGE_BYTES: raise HTTPException(400, "平铺图超过10MB")
    try: return presentation.attach(item_id, _uid(), file.file.read(image_svc.MAX_IMAGE_BYTES + 1), source_hash, only_if_empty=only_if_empty, expected_display_path=expected_display_path)
    except presentation.PresentationError as error: raise HTTPException(error.status, str(error)) from error
    except OSError: raise HTTPException(503, "平铺图未能保存，源照片仍保留")


@router.patch("/{item_id}/presentation", response_model=ItemOut)
def choose_presentation(item_id: str, selected: bool = Body(..., embed=True)):
    try: return presentation.select(item_id, _uid(), selected)
    except presentation.PresentationError as error: raise HTTPException(error.status, str(error)) from error


@router.get("", response_model=ItemListOut)
def list_items(
    slot: str | None = Query(None, description="top/bottom/full/outer/shoes/accessory"),
    type: str | None = Query(None, description="受控 type_key 或中文名"),
    tagging_status: str | None = Query(None, description="pending|tagged"),
    favorite: bool | None = None,
    include_archived: bool = False,
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    return svc.list_items(
        _uid(),
        slot=slot,
        type_key=type,
        tagging_status=tagging_status,
        favorite=favorite,
        include_archived=include_archived,
        search=search,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=ItemCreateOut)
def create_item(payload: ItemCreate) -> dict:
    """建一件单品，并立即返回"这次录入带来了多少新搭配"。

    设计文档 §9.1 / §9.2：录入这个体力活本身必须有可感知的回报，
    否则用户没有动机把整个衣橱录进来。所以响应里带上
    组合数的前后变化 + 用现有衣橱搭出来的现成样例。
    """
    uid = _uid()
    before = wardrobe.total_combinations(uid)["total_combinations"]
    try:
        item = svc.create(uid, payload)
    except sqlite3.IntegrityError as e:
        # image_hash 唯一索引命中：同一张图已经录过了
        raise HTTPException(400, f"这件衣服可能已经录过了（图片指纹重复）：{e}") from e
    after = wardrobe.total_combinations(uid)["total_combinations"]
    previews = wardrobe.sample_outfits_with(uid, item["id"], limit=3)

    delta = after - before
    if delta > 0:
        message = f"已入库。你的可选搭配从 {before} 套变成 {after} 套。"
    elif after > 0:
        message = f"已入库。当前这季共 {after} 套组合——再补一件基础单品就能明显变多。"
    else:
        message = "已入库。现在还没有能搭出的整套，再补上缺的基础单品就能成型。"

    return {
        "item": item,
        "combination_delta": {"before": before, "after": after},
        "sample_outfits": previews,
        "message": message,
    }


@router.post("/bulk", response_model=BulkItemsOut)
def create_items_bulk(payload: BulkItemsIn) -> dict:
    """批量导入。

    ⚠️ **声明在 `/{item_id}` 之前**是刻意的：现在还没有 `POST /items/{item_id}`，
    所以不存在冲突，但把具体路径放在通配路径前面是防御性的 ——
    将来谁加了一个 `POST /{item_id}`，这条不会被静默吞掉。

    失败语义（2026-10-03 拍板）：**跳过并回报清单**，不是整体回滚。
    """
    uid = _uid()
    before = wardrobe.total_combinations(uid)["total_combinations"]
    result = svc.create_many(uid, payload.items)
    after = wardrobe.total_combinations(uid)["total_combinations"]

    parts = [f"共 {result['total']} 件，成功 {result['created']}"]
    if result["skipped"]:
        parts.append(f"跳过 {result['skipped']}")
    if result["failed"]:
        parts.append(f"失败 {result['failed']}")
    message = "，".join(parts) + "。"

    delta = after - before
    if delta > 0:
        message += f"可选搭配从 {before} 套变成 {after} 套。"
    elif after > 0:
        message += f"当前这季共 {after} 套组合。"

    return {
        **result,
        "combination_delta": {"before": before, "after": after},
        "message": message,
    }


@router.post("/parse", response_model=ItemsParseOut)
def parse_pasted_items(payload: ItemParseIn) -> dict:
    """解析粘贴来的商品文案。**只解析，不落库、不写会话状态**（§11.3）。

    待确认清单由**前端**持有：用户在页面上勾选/改完，一次性提交 `/items/bulk`。
    好处是**不用动 `conversation_state`、也不用动 `schema.py`**（两者都在
    Do-Not-Modify 清单里）。代价是刷新页面清单会丢，可以接受。

    ⚠️ 声明在 `/{item_id}` **之前**：否则 `POST /items/parse` 会被
    `/{item_id}` 当成品单 id 吃掉（跟 `/bulk` 同一个理由）。
    """
    uid = _uid()
    candidates = ingest_svc.build_candidates(payload.text)
    if not candidates:
        # ⚠️ 失败提示要**说清为什么、下一步做什么**（2026-10-20 用户反馈：
        #    粘了个商品链接，只看到"没读出商品名"，不知道是坏了还是自己用错）。
        #
        # 两种最常见的情况分开说：
        #   ① 只粘了链接 → 用户以为"粘链接就能录"（这是很自然的预期）。
        #      必须讲明**不抓网页**是刻意的（淘宝无登录态读不到，实测 6 条入口全被挡），
        #      否则他会反复试、以为是 bug。
        #   ② 文字里没标题 → 给个能直接照抄的例子。
        text = (payload.text or "").strip()
        has_url = "http://" in text or "https://" in text
        has_cjk = bool(re.search(r"[\u4e00-\u9fff]", text))
        example = "例如：宽松直筒牛仔裤 深灰"
        if has_url and not has_cjk:
            message = (
                "这是个商品链接，但我读不到链接里的内容 —— 我不会去打开那个网页"
                "（淘宝等平台在没登录时读不到）。把商品标题一起粘进来就行，" + example
            )
        elif has_url:
            message = "这段里有链接，但没读出能当标题的文字 —— 补一句商品标题就行，" + example
        else:
            message = "这段我没读出商品名 —— 直接粘商品标题就行，" + example
        return {"total": 0, "items": [], "message": message}

    # 疑似重复只看"归一化标题完全一致"。**只提示，不阻止** ——
    # 用户确实可能买了两件一样的（§11.3 失败语义）。
    seen = {
        ingest_svc.normalize_title(it.get("name") or "")
        for it in svc.iter_all_items(uid)
    }
    seen.discard("")

    items = []
    for idx, c in enumerate(candidates):
        key = str(c.fields.get("type") or "other")
        items.append(
            {
                "index": idx,
                "title": c.title,
                "url": c.url or None,
                "code": c.code or None,
                "platform": c.platform or None,
                "fields": c.fields,
                "unresolved": c.unresolved,
                "type_key": key,
                "type_label": taxonomy.label_of(key),
                "needs_type": c.needs_type,
                "duplicate_hint": (
                    "你已经有一件同名的了" if ingest_svc.normalize_title(c.title) in seen else None
                ),
                "used_llm": c.pick.used_llm,
                "llm_error": c.pick.error or None,
            }
        )
    return {"total": len(items), "items": items, "message": None}


@router.post("/import-photo")
def import_photo(file: UploadFile = File(...), token: str = Form(...), payload: str = Form(...),
                 original: UploadFile | None = File(None), display: UploadFile | None = File(None)) -> dict:
    """§14：用户确认后整件提交，不在保存时重复识别。"""
    try:
        for upload in (file, original, display):
            if upload is not None and upload.size is not None and upload.size > image_svc.MAX_IMAGE_BYTES:
                raise photo_import.ImportError("照片超过10MB")
        return photo_import.commit(_uid(), token, photo_import.parse_payload(payload), file.file.read(image_svc.MAX_IMAGE_BYTES + 1),
            original_data=original.file.read(image_svc.MAX_IMAGE_BYTES + 1) if original else None,
            display_data=display.file.read(image_svc.MAX_IMAGE_BYTES + 1) if display else None)
    except photo_import.ImportError as error:
        raise HTTPException(error.status, {"code": error.code, "message": str(error), "item_id": error.item_id}) from error
    except OSError as error:
        raise HTTPException(400, "照片暂时无法保存，当前内容保留，请重试") from error
    except sqlite3.OperationalError as error:
        raise HTTPException(503, "保存暂时不可用，请重试核对同一件") from error


@router.get("/import-photo/{token}")
def import_photo_status(token: str) -> dict:
    try:
        item = photo_import.lookup(_uid(), token)
    except photo_import.ImportError as error:
        raise HTTPException(400, str(error)) from error
    return {"item": item, "saved": item is not None}


@router.get("/{item_id}", response_model=ItemOut)
def get_item(item_id: str) -> dict:
    got = svc.get(item_id, _uid())
    if not got:
        raise HTTPException(404, "单品不存在")
    return got


@router.patch("/{item_id}", response_model=ItemOut)
def update_item(item_id: str, payload: ItemUpdate) -> dict:
    got = svc.update(item_id, payload, _uid())
    if not got:
        raise HTTPException(404, "单品不存在")
    return got


@router.delete("/{item_id}", response_model=OkOut)
def delete_item(item_id: str) -> dict:
    if not svc.delete(item_id, _uid()):
        raise HTTPException(404, "单品不存在")
    return {"ok": True}


@router.post("/wear", response_model=WearEventOut)
def log_wear(payload: WearLogIn) -> dict:
    try:
        return wear_svc.log_wear(_uid(), payload)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except sqlite3.IntegrityError as e:
        # 兜底：外键 / 唯一约束冲突一律归成 400，不要漏成 500
        raise HTTPException(400, f"数据约束冲突：{e}") from e


@router.get("/wear/events", response_model=list[WearEventOut])
def list_wear_events(limit: int = 50) -> list[dict]:
    return wear_svc.list_wear_events(_uid(), limit=limit)


def _save_and_tag(uid: str, item_id: str, data: bytes, *, reference_image: Path | None = None, reference_crop: dict | None = None) -> dict:
    """落盘一张图 + 顺手打标。**上传接口与「从相册导入」共用这一段**。

    分成两个入口却共用实现，是为了不让两条上传路径慢慢走岔
    （一条有打标、一条没有 —— 这种事最难被发现）。
    """
    try:
        saved = image_svc.save_image(uid, item_id, data, **({"original_data": reference_image.read_bytes()} if reference_image else {}))
    except image_svc.ImageError as e:
        raise HTTPException(400, str(e)) from e
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except sqlite3.IntegrityError as e:
        # ⚠️ 不要把 `str(e)` 拼进给用户的话里（2026-10-21 审查发现）：
        #    原文是 `UNIQUE constraint failed: clothing_items.user_id,
        #    clothing_items.image_hash` —— 英文 + 表名，用户看不懂，
        #    在 toast 里还占两行。日志里有原始异常，排查够用了。
        logger.warning("图片指纹重复，拒绝入库：%s", e)
        raise HTTPException(400, "这张图已经录给别的单品了（图片指纹重复）") from e

    # 存图成功后顺手跑一次打标（上传即识别，认完用户能改）。
    # ⚠️ 打标**失败绝不能让上传变成 4xx/5xx** —— 照片已经存好了，
    #    单品留在 `pending` 就行，属性用户可以手填。所以走"只回报状态"的那个入口。
    #
    # ⚠️ 外面再包一层 `Exception` 兜底：`tag_and_apply` 自己已经承诺"不抛"，
    #    但那是**约定**不是**保证** —— 万一它内部漏接了某个异常类型
    #    （比如数据库 `OperationalError`），让上传变成 500 是最坏结果：
    #    照片已经换成新的了，用户却收到"上传失败"，重试还会再撞一次
    #    `image_hash` 唯一索引。这里兜住，把结果如实告诉前端。
    try:
        context = {"reference_image": reference_image, "reference_crop": reference_crop} if reference_image is not None else {}
        tagging = tagging_svc.tag_and_apply(uid, item_id, **context)
    except Exception as e:  # noqa: BLE001 —— 兜底就是这里的目的
        tagging = {
            "status": "failed",
            "tags": {},
            "unresolved": {},
            "ai_confidence": None,
            "message": f"识别失败（照片已保存，可手动填属性）：{type(e).__name__}",
            "item": None,
        }

    return {
        **saved,
        "item": tagging.get("item") or svc.get(item_id, uid),
        "tagging": tagging,
    }


@router.post("/{item_id}/image")
def upload_item_image(
    item_id: str,
    file: UploadFile = File(...),
    reference_image: UploadFile | None = File(None),
    reference_crop: str | None = Form(None),
) -> dict:
    """给单品传一张照片。**会覆盖旧图**（一个单品只保留一张）。

    类型靠**读文件头**判断，不信 `content_type` —— 微信转发的图片
    有时报 `application/octet-stream`，信它会把正常图片拒之门外。
    """
    try:
        crop = tagging_svc.parse_reference_crop(reference_crop)
        if crop and reference_image is None:
            raise ValueError("给出原图选区时也需要参考原图")
        reference_data = reference_image.file.read(image_svc.MAX_IMAGE_BYTES + 1) if reference_image is not None else None
        with image_svc.temporary_image(reference_data) as reference:
            return _save_and_tag(_uid(), item_id, file.file.read(image_svc.MAX_IMAGE_BYTES + 1), reference_image=reference, reference_crop=crop)
    except (image_svc.ImageError, ValueError) as e:
        raise HTTPException(400, str(e)) from e


# ---------------------------------------------------------------------------
# 手机相册直读（2026-10-21）
#
# **为什么要有这条看起来多余的通道**
#
# 桌面图标打开的是小米浏览器的 PWA 壳（`PWAActivity`）。它没有实现 WebView
# 的 `onShowFileChooser`，所以 `<input type=file>` 点下去**毫无反应** ——
# 不弹选择器、不报错、连 `change` 都不会来。同一个地址在同一台手机的
# 浏览器里却是正常的（实测能弹出「相机/文件」），所以这不是页面代码的问题。
#
# 浏览器沙箱不给页面直接读本地文件，前端怎么改都绕不过去。但服务本身就
# 跑在这台手机上（Termux），于是改成：**后端去读相册目录，页面只负责展示
# 缩略图 + 回传一个 id**。拍照打标的链路完全不变，仍然走 `_save_and_tag`。
#
# ⚠️ 电脑上没有 `/sdcard`，这些目录一个都不存在 → `dirs` 为空、
#    `count` 为 0，前端据此把入口藏起来，不会在电脑上露出一个不能用的按钮。
# ---------------------------------------------------------------------------

#: 扫描的相册目录。**顺序只影响同 mtime 时的稳定排序**，结果统一按时间倒序。
ALBUM_DIRS: tuple[str, ...] = (
    "/sdcard/DCIM/Camera",
    "/sdcard/DCIM",
    "/sdcard/Pictures",
    "/sdcard/Download",
)

#: 认得的后缀。**不收 HEIC** —— 浏览器显示不了，`images.sniff()` 也不认，
#: 列出来只会让用户选中后收到一个"认不出图片格式"。
ALBUM_EXTS: frozenset[str] = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif"})

#: 单次扫描的文件上限。`/sdcard/Pictures` 里可能有几万张，
#: 全扫一遍会让一次相册请求变成秒级 IO。
ALBUM_SCAN_LIMIT: int = 3000


def _album_id(path: str) -> str:
    """把绝对路径编码成一个不透明 id。

    不用明文路径：路径里带 `/`，直接当 query 参数会被各种转义规则折腾；
    更重要的是**不透明 id 能挡住手改参数去读任意文件**（虽然 `_album_path`
    还有白名单兜底，两层更稳）。
    """
    return base64.urlsafe_b64encode(path.encode("utf-8")).decode("ascii").rstrip("=")


def _album_path(pid: str) -> Path:
    """反解 id → 磁盘路径。**只认白名单目录下的真实文件**。"""
    if not pid:
        raise HTTPException(400, "缺少照片 id")
    try:
        pad = "=" * (-len(pid) % 4)
        raw = base64.urlsafe_b64decode(pid + pad).decode("utf-8")
        # ★ `realpath` 也放进这个 try（2026-10-21 审查发现）：base64 能编码进
        #   `\x00`，utf-8 也解得出，而 `os.path.realpath` 遇到 NUL 会抛
        #   `ValueError: embedded null byte` —— 它不是 `OSError`，漏出去就是 500。
        resolved = Path(os.path.realpath(raw))
    except (ValueError, UnicodeDecodeError) as e:
        raise HTTPException(400, "照片 id 不合法") from e

    # 白名单判据用 `os.path.realpath` 展开 `..` 和符号链接之后再比前缀：
    # `/sdcard/DCIM/../../data/...` 这种写法过不了 map（`/sdcard/DCIM` 的
    # realpath 是 `/storage/emulated/0/DCIM`，两者都要覆盖）。
    allowed = [Path(os.path.realpath(d)) for d in ALBUM_DIRS]
    if not any(resolved == d or d in resolved.parents for d in allowed):
        raise HTTPException(403, "这个路径不在相册目录里")
    # ★ 只认图片后缀（2026-10-21 审查发现）：白名单里的 `/sdcard/Download`
    #   什么都有（pdf / txt / 微信的 .db），而 id 就是路径的 base64、格式在
    #   `album_photos` 里是公开的。不挡后缀的话，`GET /items/album/photo?id=…`
    #   会把这些原样吐给同一网段的任何人 —— 手机端必然 `APP_HOST=0.0.0.0`，
    #   而且整套接口没有任何鉴权。
    #   列表接口本来就按 `ALBUM_EXTS` 过滤（`_iter_album_files`），这里补的是
    #   **直取**那条路 —— 两条路必须同一口径。
    #   用 404 而不是 403：不透露"文件存在、只是格式不对"。
    if resolved.suffix.lower() not in ALBUM_EXTS:
        raise HTTPException(404, "这张照片不在相册里")
    if not resolved.is_file():
        raise HTTPException(404, "这张照片不在了（可能被删或移走）")
    # 返回**已解析**的路径而不是原路径：校验和真正 `open()` 之间若符号链接被
    # 换掉，前缀检查就白做了（TOCTOU）。返回 resolved 让两步看的是同一个 inode。
    return resolved


def _iter_album_files() -> list[tuple[float, str, int]]:
    """按修改时间倒序列出相册里的图片。`(mtime, 绝对路径, 字节数)`。

    只往下走 2 层：`/sdcard/DCIM` 下面有 `Camera/`、`Screenshots/`，
    `/sdcard/Pictures` 下面有 `WeiXin/`、`Screenshots/`。再深就不是相册了。
    """
    found: list[tuple[float, str, int]] = []
    seen: set[str] = set()
    for base in ALBUM_DIRS:
        if not os.path.isdir(base):
            continue
        base_depth = len(Path(base).parts)
        for root, dirs, names in os.walk(base):
            # ★ 跳过隐藏目录（2026-10-22 真机实测发现）：小米的回收站就是
            #   `/sdcard/DCIM/.globalTrash/`，里面全是**已经删掉**的照片，而它们
            #   的 mtime 往往最新 —— 按时间倒序正好挤在最前面。实测前 20 张里
            #   12 张来自回收站，用户一点就导入了一张自己删过的图。
            #   顺带把 `.thumbnails` 这类缓存目录也挡在外面。
            #   注意必须写在"层数到了就清空 dirs"**之前**，否则先清空就白筛了。
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            if len(Path(root).parts) - base_depth >= 2:
                dirs[:] = []
            for name in names:
                if len(found) >= ALBUM_SCAN_LIMIT:
                    break
                # 隐藏文件同理：回收站里的照片会被改名成 `.IMG_xxx.jpg`
                # （原名前加个点），只看目录名挡不住已经改名的。
                if name.startswith("."):
                    continue
                if os.path.splitext(name)[1].lower() not in ALBUM_EXTS:
                    continue
                full = os.path.join(root, name)
                real = os.path.realpath(full)
                if real in seen:
                    continue
                try:
                    st = os.stat(real)
                except OSError:
                    continue
                seen.add(real)
                found.append((st.st_mtime, real, st.st_size))
    found.sort(key=lambda r: r[0], reverse=True)
    return found


@router.get("/album/photos")
def album_photos(limit: int = Query(24, ge=1, le=60)) -> dict:
    """列出手机相册里最近的几张照片。

    `dirs` / `available` 一起返回是**故意的**：相册读不到时前端要能说清
    "为什么是空的"（Termux 没拿到存储权限 vs 相册本来就没照片），
    否则用户只会看到一个空列表。
    """
    rows = _iter_album_files()
    return {
        "available": bool(rows),
        "dirs": [d for d in ALBUM_DIRS if os.path.isdir(d)],
        "total": len(rows),
        "photos": [
            {
                "id": _album_id(path),
                "name": Path(path).name,
                "mtime": int(mtime),
                "bytes": size,
            }
            for mtime, path, size in rows[:limit]
        ],
    }


@router.get("/album/photo")
def album_photo_file(id: str = Query(...)) -> FileResponse:
    """取相册原图（前端当缩略图用）。

    没做缩略图：项目不装 Pillow，而 `/sdcard` 和进程在同一台机器上，
    `localhost` 传原图的代价比引入一个图像库小得多。前端用
    `loading="lazy"` 保证只加载滚到的那几张。
    """
    target = _album_path(id)
    return FileResponse(str(target))


@router.post("/{item_id}/image-from-album")
def import_image_from_album(item_id: str, body: dict = Body(...)) -> dict:
    """把相册里的一张照片直接装到单品上。**绕开文件选择器**。

    前端只传 `{"id": "..."}`，真实字节由后端自己从磁盘读 ——
    这就是这条通道存在的意义：整个过程中浏览器一次都没碰过本地文件。
    """
    target = _album_path(str(body.get("id") or ""))
    try:
        data = target.read_bytes()
    except OSError as e:
        raise HTTPException(400, f"读不到这张照片：{e.strerror or e}") from e
    return _save_and_tag(_uid(), item_id, data)
