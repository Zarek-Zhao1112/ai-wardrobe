"""必选衣物约束：模型前及事务保存前复用相同的归属、品类与季节校验。"""
from .. import taxonomy
from . import aesthetics


class AnchorUnavailable(ValueError):
    """必选衣物不可用时明确失败，不回退为任意搭配。"""


def validate_anchor(items: list[dict], anchor_item_id: str | None, season: str) -> None:
    if not anchor_item_id:
        return
    item = next((it for it in items if it["id"] == anchor_item_id), None)
    if not item:
        raise AnchorUnavailable("这件衣服已不存在，请返回衣橱重新选择。")
    if item.get("is_archived") or item.get("type") in taxonomy.HOME_LIKE:
        raise AnchorUnavailable("这件衣服暂不能参与搭配，请选择衣橱中未归档的外出衣物。")
    if not taxonomy.usable_in(item, season):
        raise AnchorUnavailable("这件衣服不适合所选季节，请调整季节后再试。")
    if item.get("slot") not in {"top", "bottom", "full", "shoes", "outer", "accessory"} or (
        item.get("slot") == "accessory" and item.get("type") not in aesthetics._ACCESSORIES
    ):
        raise AnchorUnavailable("这件衣服的品类暂不能参与搭配。")
