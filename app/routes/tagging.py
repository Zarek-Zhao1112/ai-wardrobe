"""打标路由：给单品的照片跑一次视觉打标。

**未配模型 key 时返回 503，不是 500** —— 那不是 bug，是还没配。
把这两者混在一起会让排查方向完全跑偏（去看代码 vs 去看配置）。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from .. import db
from ..services import images as image_svc
from ..services import items as item_svc
from ..services import tagging as tagging_svc
from ..services.llm.client import LLMError

router = APIRouter(prefix="/tagging", tags=["tagging"])


def _uid() -> str:
    return db.get_default_user_id()


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

    try:
        result = tagging_svc.tag_image(image_file, hint=hint)
    except LLMError as e:
        raise HTTPException(503, f"模型不可用：{e}") from e

    if not apply:
        return {**result, "applied": False}

    row = tagging_svc.apply_tags(uid, item_id, result)
    return {**result, "applied": row is not None, "item": row}
