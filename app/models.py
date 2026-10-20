"""Pydantic 请求 / 响应模型。"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from . import taxonomy

Slot = Literal["top", "bottom", "full", "outer", "shoes", "accessory"]


def _normalize_length(value: Any) -> Optional[str]:
    """把裤长统一成受控 key（`full` / `nine_tenth` / …）。

    ⚠️ 这一列有**三个写入入口**：AI 打标（写 key）、粘贴解析（写 key）、
    手工 API（用户/脚本可能写中文）。不归一的话同一列会混着
    `nine_tenth` / `九分` / `9分` / `随便写`，统计和筛选全废 ——
    而且**不报错**（2026-10-20 Bug 猎手实测）。

    认不出 → `None`（宁缺勿脏）。不抛 422：录入不该因为一个可选字段被挡住，
    用户填了个词表外的长度，丢掉比整条记录失败好。
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return taxonomy.resolve_length(text)


# ---------------------------------------------------------------------------
# 单品
# ---------------------------------------------------------------------------
class ItemCreate(BaseModel):
    type: str = Field("other", description="受控 type_key，可用中文名，服务端会归一化")
    subtype: Optional[str] = None
    name: Optional[str] = None
    brand: Optional[str] = None
    image_path: Optional[str] = Field(None, description="相对 data/images 的文件名；不传则留空待补图")
    image_hash: Optional[str] = Field(
        None, description="客户端的图片指纹（pHash），用于去重；不传则不做去重"
    )

    colors: list[str] = Field(default_factory=list)
    primary_color: Optional[str] = None
    pattern: Optional[str] = None
    material: Optional[str] = None
    fit: Optional[str] = None
    length: Optional[str] = Field(
        None, description="裤长（受控 key：full/nine_tenth/seven_tenth/five_tenth/three_tenth）"
    )
    style: list[str] = Field(default_factory=list)
    season: list[str] = Field(default_factory=list)
    formality: Optional[str] = None
    scene: list[str] = Field(default_factory=list)
    tags: dict[str, Any] = Field(default_factory=dict)

    purchase_date: Optional[str] = None
    purchase_price: Optional[float] = None
    favorite: bool = False
    notes: Optional[str] = None

    @field_validator("type")
    @classmethod
    def _norm_type(cls, v: str) -> str:
        return taxonomy.resolve_type(v)

    @field_validator("length")
    @classmethod
    def _norm_length(cls, v: Any) -> Optional[str]:
        return _normalize_length(v)


class ItemUpdate(BaseModel):
    type: Optional[str] = None
    subtype: Optional[str] = None
    name: Optional[str] = None
    brand: Optional[str] = None
    image_path: Optional[str] = None      # 允许后补照片（创建时不一定有图）
    image_hash: Optional[str] = None      # 客户端图片指纹，用于去重

    colors: Optional[list[str]] = None
    primary_color: Optional[str] = None
    pattern: Optional[str] = None
    material: Optional[str] = None
    fit: Optional[str] = None
    length: Optional[str] = Field(
        None, description="裤长（受控 key：full/nine_tenth/seven_tenth/five_tenth/three_tenth）"
    )
    style: Optional[list[str]] = None
    season: Optional[list[str]] = None
    formality: Optional[str] = None
    scene: Optional[list[str]] = None
    tags: Optional[dict[str, Any]] = None

    purchase_date: Optional[str] = None
    purchase_price: Optional[float] = None
    favorite: Optional[bool] = None
    notes: Optional[str] = None
    is_archived: Optional[bool] = None

    @field_validator("type")
    @classmethod
    def _norm_type(cls, v: Optional[str]) -> Optional[str]:
        """PATCH 里出现的 type 必须是真值。

        空串一律拒收：`type` 是 NOT NULL DEFAULT 'other'，空串违反这个意图，
        而且会让 slot 被静默降级成 accessory（一件上衣变成配饰）。宁可报 422，
        也不静默改坏数据。
        （create 的语义不同：那里空值等于"还没分类"，归一成 other 是合理的）
        """
        if v is None:
            return None
        v = str(v).strip()
        if not v:
            raise ValueError("type 不能为空；不确定品类就不要传这个字段")
        return taxonomy.resolve_type(v)

    @field_validator("length")
    @classmethod
    def _norm_length(cls, v: Any) -> Optional[str]:
        """`None` 要保留成 `None`（= 清空），不能被归一成别的值。"""
        return _normalize_length(v)


class ItemOut(BaseModel):
    id: str
    user_id: str
    image_path: Optional[str] = None
    thumbnail_path: Optional[str] = None

    type: str
    type_label: str = ""
    subtype: Optional[str] = None
    slot: str

    colors: list[str] = Field(default_factory=list)
    primary_color: Optional[str] = None
    pattern: Optional[str] = None
    material: Optional[str] = None
    fit: Optional[str] = None
    length: Optional[str] = Field(
        None, description="裤长（受控 key：full/nine_tenth/seven_tenth/five_tenth/three_tenth）"
    )
    style: list[str] = Field(default_factory=list)
    season: list[str] = Field(default_factory=list)
    formality: Optional[str] = None
    scene: list[str] = Field(default_factory=list)
    tags: dict[str, Any] = Field(default_factory=dict)

    name: Optional[str] = None
    brand: Optional[str] = None
    favorite: bool = False
    notes: Optional[str] = None

    ai_confidence: Optional[float] = None
    ai_description: Optional[str] = None
    tagging_status: str = "pending"
    tagged_by: Optional[str] = None

    wear_count: int = 0
    last_worn_at: Optional[str] = None
    last_suggested_at: Optional[str] = None
    suggestion_count: int = 0
    acceptance_count: int = 0

    # 洗护（P0 只读不写，接口后补）
    wears_since_wash: int = 0
    last_washed_at: Optional[str] = None
    wash_interval: Optional[int] = None

    is_archived: bool = False
    created_at: str
    updated_at: str


class ItemListOut(BaseModel):
    items: list[ItemOut]
    total: int
    page: int = 1
    page_size: int = 50


# ---------------------------------------------------------------------------
# 录入一件衣服后的即时反馈（设计文档 §9.1 / §9.2 的落地）
# ---------------------------------------------------------------------------
class CombinationDelta(BaseModel):
    before: int
    after: int


class OutfitPreview(BaseModel):
    """本地枚举出来的搭配样例。不经过模型，所以 item_id 必然真实。"""

    item_ids: list[str]
    items: list[ItemOut] = Field(default_factory=list)
    kind: str = "split"          # split（上下分体）| full（连身）


class ItemCreateOut(BaseModel):
    item: ItemOut
    combination_delta: CombinationDelta
    sample_outfits: list[OutfitPreview] = Field(default_factory=list)
    message: Optional[str] = None


# ---------------------------------------------------------------------------
# 批量导入（P1 必需前置：缺口分析少于 20 件没有意义，而 P1 阶段
# 微信/网页都还没做，只能靠这个入口一次性把衣橱灌进去）
# ---------------------------------------------------------------------------
class BulkItemsIn(BaseModel):
    items: list[ItemCreate] = Field(..., min_length=1, max_length=200)


class BulkItemOutcome(BaseModel):
    """单件的处理结果。逐件回报，而不是只给一个总结 —— 否则用户
    无法知道"少的那几件是哪些、为什么"。"""

    index: int
    name: Optional[str] = None
    status: Literal["created", "skipped", "failed"]
    item_id: Optional[str] = None
    reason: Optional[str] = None


class BulkItemsOut(BaseModel):
    """⚠️ 失败语义已定（2026-10-03）：**跳过并回报清单**，不是整体回滚。
    理由：导入是补数据的体力活，一件撞了不该让另外 19 件白干；
    但必须逐件回报，否则会变成"以为导完了其实少了几件"。
    """

    total: int
    created: int
    skipped: int
    failed: int
    outcomes: list[BulkItemOutcome] = Field(default_factory=list)
    # 沿用 §9.1/§9.2 的录入动机机制：让"一次导入"也看得到回报
    combination_delta: Optional[CombinationDelta] = None
    message: Optional[str] = None


# ---------------------------------------------------------------------------
# 粘贴录入（P5 / 设计文档 §11.3）
#
# ⚠️ 这个接口**不落库、不写会话状态** —— 只解析，待确认清单由前端持有。
# 这样"不动 conversation_state、不动 schema"（两者都在 Do-Not-Modify 里），
# 代价是刷新页面清单会丢，可以接受。
# ---------------------------------------------------------------------------
class ItemParseIn(BaseModel):
    """要解析的文本。可以是**多段**分享文案（一次粘好几件）。"""

    text: str = Field(..., min_length=1, max_length=20000)


class ParsedItemOut(BaseModel):
    """一件商品的解析结果。**还没有入库** —— 用户确认后才提交 `/items/bulk`。"""

    index: int
    title: str                       # 干净标题（入库时当 `name`）
    url: Optional[str] = None
    code: Optional[str] = None       # 分享码
    platform: Optional[str] = None
    fields: dict[str, Any] = Field(default_factory=dict)   # 提交时原样回传
    unresolved: dict[str, str] = Field(default_factory=dict)
    type_key: Optional[str] = None
    type_label: Optional[str] = None
    needs_type: bool = False         # 认不出品类 → 前端必须让用户选，否则不许提交
    duplicate_hint: Optional[str] = None   # 疑似重复（**只提示，不阻止**）
    used_llm: bool = False
    llm_error: Optional[str] = None


class ItemsParseOut(BaseModel):
    total: int
    items: list[ParsedItemOut] = Field(default_factory=list)
    message: Optional[str] = None    # 一件都没解析出来时的说明


# ---------------------------------------------------------------------------
# 穿着记录
# ---------------------------------------------------------------------------
class WearLogIn(BaseModel):
    item_ids: list[str] = Field(..., min_length=1)
    worn_at: Optional[str] = Field(None, description="YYYY-MM-DD，不传则取今天")
    outfit_id: Optional[str] = None
    occasion: Optional[str] = None
    weather: Optional[str] = None
    notes: Optional[str] = None
    # 穿戴来源：自己记的 / 采纳了 AI 的建议。
    # 只有后者会计入采纳率（设计文档 §7 的一级指标）。
    source: Literal["self_recorded", "ai_suggested"] = "self_recorded"

    @field_validator("worn_at")
    @classmethod
    def _check_date(cls, v: Optional[str]) -> Optional[str]:
        """必须是 YYYY-MM-DD。

        数据库里日期是按字符串比较的（last_worn_at < ?），
        非定长格式会破坏这个比较，所以在入口就拦住。
        """
        if v is None:
            return None
        v = str(v).strip()
        from datetime import date as _date

        try:
            _date.fromisoformat(v)
        except ValueError as e:
            raise ValueError("worn_at 必须是 YYYY-MM-DD 格式") from e
        return v


class WearEventOut(BaseModel):
    id: str
    worn_at: str
    item_ids: list[str]
    outfit_id: Optional[str] = None
    occasion: Optional[str] = None
    weather: Optional[str] = None
    source: str


# ---------------------------------------------------------------------------
# 搭配 / 反馈
# ---------------------------------------------------------------------------
class OutfitOut(BaseModel):
    id: str
    item_ids: list[str]
    items: list[ItemOut] = Field(default_factory=list)
    scene: Optional[str] = None
    weather: Optional[str] = None
    reason: Optional[str] = None
    confidence: Optional[float] = None
    status: str = "proposed"
    generated_by: str = "llm"
    created_at: str


class FeedbackIn(BaseModel):
    outfit_id: str
    feedback_type: Literal["like", "dislike", "swap"]
    rating: Optional[int] = Field(None, ge=1, le=5)
    comfort_rating: Optional[int] = Field(None, ge=1, le=5)
    style_rating: Optional[int] = Field(None, ge=1, le=5)
    comment: Optional[str] = None


# ---------------------------------------------------------------------------
# 偏好记忆
# ---------------------------------------------------------------------------
class PreferenceOut(BaseModel):
    tag_type: str
    tag_value: str
    context_key: str
    score: float                  # 原始分 -1..1
    decayed_score: float          # 按 context_key 条件衰减后的有效分
    evidence_count: int
    confidence: float
    last_updated: str


class BodyProfileIn(BaseModel):
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    chest_cm: Optional[float] = None
    waist_cm: Optional[float] = None
    hip_cm: Optional[float] = None
    body_type: Optional[str] = None
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# 衣橱缺口分析 / 组合数
# ---------------------------------------------------------------------------
class SlotSuggestion(BaseModel):
    """某个槽位的补货建议。score = ΔC × 多样性折扣。"""

    slot: str
    slot_label: str
    current_count: int
    delta: int
    diversity_discount: float
    score: float


class GapAnalysisOut(BaseModel):
    season: str
    season_label: str
    counts: dict[str, int]
    total_combinations: int
    min_viable: bool

    # 组合数为 0 时，缺哪些槽位（真正的"断链"，必须先补这些）
    blocking_slots: list[str] = Field(default_factory=list)
    blocking_labels: list[str] = Field(default_factory=list)

    # 按 ΔC × 多样性折扣 排序的补货建议
    slot_suggestions: list[SlotSuggestion] = Field(default_factory=list)

    never_worn: list[ItemOut] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)

    # 与旧实现（硬编码白名单比例法）的对照，用来说明它为什么漏判
    legacy_rule_verdict: Optional[str] = None


# ---------------------------------------------------------------------------
# 通用
# ---------------------------------------------------------------------------
class TaxonomyOut(BaseModel):
    slots: dict[str, str]
    types: dict[str, dict[str, str]]   # type_key -> {label, slot}
    vocabularies: dict[str, dict[str, str]]


class OkOut(BaseModel):
    ok: bool = True
    detail: Optional[str] = None
