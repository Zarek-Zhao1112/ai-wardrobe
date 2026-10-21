"""打标路由：给单品的照片跑一次视觉打标。

**未配模型 key 时返回 503，不是 500** —— 那不是 bug，是还没配。
把这两者混在一起会让排查方向完全跑偏（去看代码 vs 去看配置）。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from .. import config, db
from ..services import images as image_svc
from ..services import items as item_svc
from ..services import tagging as tagging_svc
from ..services.llm.client import LLMError

# 相册 id → 路径的校验（白名单 + realpath）**只写一份**。
# 复制一遍的话，两处口径迟早走岔 —— 那是安全校验，走岔的代价不是"少个功能"。
from .items import _album_path

router = APIRouter(prefix="/tagging", tags=["tagging"])


def _uid() -> str:
    return db.get_default_user_id()


@router.post("/preview")
def preview_tags(
    file: UploadFile | None = File(None, description="直接上传的照片"),
    album_id: str | None = Form(None, description="手机相册里的照片 id"),
    hint: str | None = Form(None, description="补充信息，例如「这是冬天的外套」"),
) -> dict:
    """照片 → 属性，**只识别、不落库**（录入页的「AI 识别」按钮用）。

    与 `POST /tagging/{item_id}?apply=false` 的区别：那个要求单品**已经存在**
    且**已经有照片**；这里连单品都还没建 —— 正是「先看照片、再决定怎么填」
    那一步。也因此不写 `image_path`、不碰 `tagging_status`。

    `file` 与 `album_id` 二选一（手机 PWA 壳不支持 `<input type=file>`，
    只能走相册 id）；两个都给时以 `file` 为准。

    契约与单品打标保持一致：**没配模型 → 503**（不是 500 —— 那是配置问题
    不是代码问题），模型报错 → 503。
    """
    if file is None and not album_id:
        raise HTTPException(400, "要么传一张照片，要么给一个相册照片 id")

    # 顺序是**先校验输入、再判模型**（和 `tag_item` 一致：404 → 400 → 503）。
    # 反过来的话，一个自己写错的请求在没配模型时会拿到 503，
    # 于是"我参数传错了"被伪装成"模型没配好"，排查方向直接歪掉。
    if not config.VISION_READY:
        raise HTTPException(503, "模型不可用：还没配视觉模型（.env 的 VISION_MODEL）")

    tmp: Path | None = None
    try:
        if file is not None:
            # 先看声明的大小：Starlette 已经把 >1 MB 的上传 spool 到磁盘了，
            # 为了判断大小先整体读进内存，是这个接口最容易吃内存的地方。
            if file.size is not None:
                _check_size(file.size)
            data = file.file.read(image_svc.MAX_IMAGE_BYTES + 1)
            if not data:
                raise HTTPException(400, "这个文件是空的")
            _check_size(len(data))
            # 后缀不是装饰：`_image_data_url()` 靠**文件名**猜 mime，猜不到退回
            # `image/jpeg` —— PNG 被标成 jpeg 有端点会直接拒。所以用文件头定。
            sniffed = image_svc.sniff(data)
            if sniffed is None:
                raise HTTPException(400, "认不出这个图片格式（支持 jpg / png / webp / gif）")
            _mime, ext = sniffed
            # `delete=False` + 自己 unlink：Windows 上 `NamedTemporaryFile`
            # 关掉之前别的句柄读不了（`tag_image` 要再打开一次）。
            with tempfile.NamedTemporaryFile(
                prefix="wz-preview-", suffix=ext, delete=False
            ) as fh:
                # ★ 先登记再写：`write()` 抛 OSError（磁盘满 / 配额）时，
                #   `finally` 也要能把它清掉 —— 否则每失败一次就漏一个临时文件。
                tmp = Path(fh.name)
                fh.write(data)
            image_file: Path = tmp
        else:
            # 复用 items 的白名单校验：挡 `../`、符号链接越界、非白名单目录。
            # 但它只管「在相册目录里」，**不管是不是图片、有多大** —— 那两件在这里补。
            image_file = _album_path(album_id or "")
            try:
                size = image_file.stat().st_size
                with image_file.open("rb") as fh:
                    head = fh.read(16)
            except OSError as e:
                raise HTTPException(400, f"读不到这张照片：{e.strerror or e}") from e
            # 手机原图 15~25 MB 很常见。超了会被模型端拒成 413，再被下面翻成
            # 503「模型不可用」—— 用户就会去 .env 里找一个根本不存在的问题。
            _check_size(size)
            if image_svc.sniff(head) is None:
                raise HTTPException(400, "认不出这个图片格式（支持 jpg / png / webp / gif）")

        try:
            result = tagging_svc.tag_image(image_file, hint=hint)
        except LLMError as e:
            raise HTTPException(503, f"模型不可用：{e}") from e
        except OSError as e:
            # 读图失败：相册没权限 / 文件被杀软占用 / 刚被删。**不能变成 500** ——
            # 那看起来像代码坏了，实际只是这张照片读不到（换一张就好）。
            raise HTTPException(400, f"读不到这张照片：{e.strerror or e}") from e
        return {**result, "applied": False, "status": "preview"}
    finally:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                # `missing_ok` 只吞 FileNotFoundError，吞不掉 Windows 上杀软 /
                # 搜索索引器的短暂占用。清不掉就留给系统 —— **绝不能因为清理
                # 失败把已经成功的 200 变成 500**。
                pass


def _check_size(size: int) -> None:
    """超出 `MAX_IMAGE_BYTES` 一律 400 —— 与 `images.save_image()` 同一口径。"""
    if size > image_svc.MAX_IMAGE_BYTES:
        mb = image_svc.MAX_IMAGE_BYTES // 1024 // 1024
        raise HTTPException(400, f"图片太大（{size / 1024 / 1024:.1f} MB），上限 {mb} MB")


@router.post("/{item_id}")
def tag_item(
    item_id: str,
    hint: str | None = Query(None, description="补充信息，例如「这是冬天的外套」"),
    apply: bool = Query(True, description="是否把结果写回单品"),
) -> dict:
    """对单品的照片跑打标。

    契约：
    - 单品必须**已经有照片**（`image_path` 非空），否则 400
    - 未配 key / 模型不可用 → **503**，`detail` 里说明原因
    - 归一失败的字段出现在 `unresolved` 里，等用户在界面上手选
      —— 它们**不会**被写进库（宁缺勿脏）
    - `apply=true` 时写回的记录 `tagged_by='ai'`，与用户手改区分开
    """
    uid = _uid()
    item = item_svc.get(item_id, uid)
    if not item:
        raise HTTPException(404, "单品不存在")
    if not item.get("image_path"):
        raise HTTPException(400, "这件单品还没有照片，先上传图片再打标")

    # 用 `read_image()` 而不是自己拼路径：它只取文件名，
    # 天然挡掉 `../` 和绝对路径（`image_path` 是用户可控字段）。
    image_file = image_svc.read_image(item["image_path"])
    if image_file is None:
        raise HTTPException(400, f"图片文件丢了（{item['image_path']}），请重新上传")

    if not apply:
        # 预览：只打标不写库（先看一眼模型认出了什么，再决定要不要落库）
        try:
            result = tagging_svc.tag_image(image_file, hint=hint)
        except LLMError as e:
            raise HTTPException(503, f"模型不可用：{e}") from e
        return {**result, "applied": False, "status": "preview"}

    # 手动点「AI 识别属性」= **全量覆盖**（`overwrite=True`）：
    # 用户主动点的，就是要拿这次结果替换旧值。
    # 自动路径（上传照片时顺手打标）走 `overwrite=False`，只填空。
    out = tagging_svc.tag_and_apply(uid, item_id, hint=hint, overwrite=True)
    if out["status"] in ("failed", "skipped"):
        # 保持既有契约：没配视觉模型 / 模型不可用一律 **503**（不是 500 —— 那通常只是还没配）。
        #
        # ⚠️ 为什么 `skipped` 也要 503：手动点按钮时"没配模型"就是**用不了**，
        #    返回 200 会让前端误报"识别完成"（`view-item.js` 靠 503 区分这两种），
        #    变成一次静默失败。`skipped` 只在**自动路径**（上传照片）里才是"正常降级"。
        #
        # ⚠️ `detail` 必须以「模型不可用」开头：`tests/test_routes_api.py:210` 锁住了这个串，
        #    它是**排障线索**（一眼看出该去查配置而不是查代码）。前端只读 status 不读这句，
        #    所以加前缀不影响用户看到的文案。前端自己的措辞在 `view-item.js:161`。
        detail = out["message"] or "打标失败"
        if not detail.startswith("模型不可用"):
            detail = f"模型不可用：{detail}"
        raise HTTPException(503, detail)
    return {
        "tags": out["tags"],
        "unresolved": out["unresolved"],
        "ai_confidence": out["ai_confidence"],
        "raw": out["raw"],
        "applied": out["status"] == "applied",
        "status": out["status"],
        "message": out["message"],
        "item": out["item"] or item,
    }
