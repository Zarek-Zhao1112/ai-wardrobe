"""视觉打标：衣服照片 → 受控词表内的属性。

**为什么必须归一**
模型输出的自由文本五花八门：同一个意思可能是「黑白条纹 / striped / 条纹 / Stripe」。
直接入库会让同样的属性变成十几种写法，于是检索、缺口分析、偏好统计**全部失效** ——
而且失效是静默的（数字算得出来，只是不对）。所以每个字段都要过 `_match` 落到受控 key。

**归一失败怎么办：不静默写脏值**
认不出的值不塞进库，而是进 `unresolved` 返回给调用方。调用方要么让用户在界面上手选，
要么把单品留在 `tagging_status='pending'`。宁可少一个字段，也不要库里多一条
「黑白条纹」这种没法统计的值。

**本模块不碰网络之外的任何东西**：不连数据库（`apply_tags` 除外，它是显式调用的落库口）。
这样测试用假客户端就能完全确定性地验证归一逻辑。

---

词表统一住在 `taxonomy.py`（`pattern` 的 `PATTERNS` 已在 P1 收尾时上收），
本文件只负责"把模型输出往词表里塞"的归一，**不自己维护任何词表**。
"""

from __future__ import annotations

import base64
import json
import mimetypes
import re
from pathlib import Path
from typing import Any, Optional

from .. import config, db, taxonomy
from . import images, items
from .llm.client import LLMClient, LLMError, get_client

# ---------------------------------------------------------------------------
# 受控字段
# ---------------------------------------------------------------------------

#: 单值字段 → `taxonomy.VOCABULARIES` 的键
SINGLE_VOCAB: dict[str, str] = {
    "primary_color": "color",
    "material": "material",
    "fit": "fit",
    # 裤长是**独立维度**（2026-10-20 从品类里拆出来的）：
    # 「九分运动裤」= type 运动裤 + length 九分。
    "length": "length",
    "formality": "formality",
}

#: 多值字段 → 词表键
MULTI_VOCAB: dict[str, str] = {
    "colors": "color",
    "style": "style",
    "season": "season",
    "scene": "scene",
}

#: 打标结果的属性字段（归一后只会出现这些键）
TAG_FIELDS: tuple[str, ...] = (
    "type", "slot", "primary_color", "colors",
    "pattern", "material", "fit", "length", "style", "season", "formality", "scene",
)

#: 需要以 JSON 数组形式入库的字段（与 `services/items.py` 的口径一致）
JSON_FIELDS: frozenset = frozenset({"colors", "style", "season", "scene", "tags"})


# ---------------------------------------------------------------------------
# 归一
# ---------------------------------------------------------------------------

def _norm(text: Any) -> str:
    """归一化：小写 + 去掉空格 / 下划线 / 连字符。"""
    return (
        str(text).strip().lower()
        .replace(" ", "")
        .replace("\u3000", "")
        .replace("_", "")
        .replace("-", "")
    )


def _match(raw: Any, table: dict[str, str]) -> Optional[str]:
    """把一个自由文本值匹配到受控词表的 key。认不出返回 `None`。

    四级匹配，从严到宽：
    1. 原值就是合法 key
    2. 归一化后等于某个 key（`t_shirt` / `T-Shirt` → `tshirt`）
    3. 等于某个中文标签
    4. **最长包含匹配**（词长 ≥ 2）
       —— 设最小词长是为了防 `"包" in "面包"` 那类误判；取最长是为了让
       `"短袖T恤"` 命中的是「短袖」而不是被更短的词抢走
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None

    if text in table:
        return text

    flat = _norm(text)
    for key in table:
        if _norm(key) == flat:
            return key

    for key, label in table.items():
        if label and label == text:
            return key

    best: Optional[str] = None
    best_len = 1
    for key, label in table.items():
        for word in (key, label):
            if not word:
                continue
            w = str(word)
            if len(w) >= 2 and w in text and len(w) > best_len:
                best, best_len = key, len(w)
    if best:
        return best

    # 4) 单字中文标签允许**前缀匹配**：用户说「黑色」，而词表里的标签是「黑」。
    #    单字不许做「包含匹配」（防「包」命中「面包」），但作为**开头**是安全的：
    #    「深红色」以「深」开头，不匹配任何标签；「米白色」命中的是 2 字的「米白」。
    #    （W4 审查发现：不加这条，「黑色」「蓝色」这类最常见的说法全部认不出。）
    for key, label in table.items():
        text_label = str(label) if label else ""
        if len(text_label) == 1 and text.startswith(text_label):
            return key

    return None


def resolve_enum(raw: Any, vocab_key: str) -> Optional[str]:
    """把模型返回的值映射到受控词表的 key。**词汇表里的字段都走这里。**"""
    table = taxonomy.VOCABULARIES.get(vocab_key)
    if not table:
        return None
    return _match(raw, table)


def _squash(text: str) -> str:
    """把分隔符归一：空格/逗号/顿号/斜杠/连字符/下划线 → 单个 `_`，并转小写。

    用于判断「整个字符串**就是**某个枚举值」：`"light blue"` → `light_blue`。
    与 `_as_list` 的切分规则对齐（同一批分隔符），避免两处口径不一致。
    """
    return re.sub(r"[\s,，、/_\-]+", "_", str(text).strip().lower()).strip("_")


def _as_list(value: Any) -> list[str]:
    """把模型可能给出的各种形状统一成字符串列表。

    模型经常把多值字段写成 `"black, white"` 而不是数组 —— 这不是错误，
    是常见行为，所以这里按逗号 / 顿号 / 斜杠 / 空白拆开。
    """
    if value is None:
        return []
    if isinstance(value, str):
        parts = re.split(r"[,，、/\s]+", value.strip())
        return [p for p in parts if p]
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for v in value:
            if v is None:
                continue
            text = str(v).strip()
            if text:
                out.append(text)
        return out
    return [str(value).strip()]


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def normalize_tags(raw: dict) -> tuple[dict, dict]:
    """模型原始输出 → `(归一后的属性, 认不出的原始值)`。

    归一失败的**不会**出现在第一个返回值里 —— 这是刻意的：
    调用方拿到 `clean` 就能直接写库，不需要再判断哪个值可信。

    第二个返回值是给界面用的（"这几个值模型给的是 X，请你手选"）。
    """
    clean: dict[str, Any] = {}
    failed: dict[str, Any] = {}

    # type 走 taxonomy 里更完整的解析（它有别名表 + 最长包含匹配）
    #
    # ⚠️ 归一失败时**不写** `clean["type"]`。否则 `apply_tags` 会拿 `other`
    # 去覆盖已有的分类 —— 一件本来是 `tshirt`（上装）的衣服，会被一次失败的
    # 打标降级成 `other`（配饰），**直接掉出组合计算**。
    # 打标失败不该破坏已有数据。
    raw_type = raw.get("type")
    type_key = taxonomy.resolve_type(raw_type) if raw_type else None
    if type_key and type_key != "other":
        clean["type"] = type_key
        clean["slot"] = taxonomy.slot_of(type_key)
    elif raw_type:
        # 模型给了值但认不出 → 记进 failed，等用户在界面上手选
        failed["type"] = raw_type

    for field, vocab_key in SINGLE_VOCAB.items():
        raw_val = raw.get(field)

        if field == "length":
            # ★ 长度有两条额外的规矩（2026-10-20 W4 双审查抓到）：
            #   ① 走 `resolve_length`（认 `LENGTH_ALIASES`），不能只 `_match` 词表 ——
            #      否则模型回的「9分」「7分裤」「全长」全进 unresolved，白给。
            #   ② **必须过类型闸门**：模型可能给衬衫/上衣填长度，
            #      而「九分袖」这种词面也会命中长度词。不是裤装就**直接丢掉** ——
            #      不写库、也不报 unresolved（"T恤有裤长"本来就不该提，报了是噪声）。
            if not taxonomy.supports_length(clean.get("type")):
                continue
            value = taxonomy.resolve_length(raw_val)
            if value:
                clean[field] = value
            elif raw_val:
                failed[field] = raw_val
            continue

        value = _match(raw_val, taxonomy.VOCABULARIES.get(vocab_key, {}))
        if value:
            clean[field] = value
        elif raw_val:
            failed[field] = raw_val

    pattern = _match(raw.get("pattern"), taxonomy.PATTERNS)
    if pattern:
        clean["pattern"] = pattern
    elif raw.get("pattern"):
        failed["pattern"] = raw["pattern"]

    for field, vocab_key in MULTI_VOCAB.items():
        table = taxonomy.VOCABULARIES.get(vocab_key, {})
        accepted: list[str] = []
        rejected: list[str] = []
        # ★ **整词优先**：`_as_list` 会按空白/逗号切分（模型爱写 `"black, white"`），
        #   但有些颜色名**本身就带空格**且是合法枚举值 —— `light_blue`（浅蓝）。
        #   先拿整个字符串试一次，认得出就当**一个值**；认不出才退回逐块匹配。
        #
        #   不这么做的代价（2026-10-10 实测）：`"light blue"` 被切成
        #   `["light", "blue"]` → `light` 认不出进 `unresolved`（用户以为是噪声），
        #   而 `blue` **静默写进库** → 浅蓝被记成蓝。**不报错**，等到按颜色统计
        #   偏好时才发现数据不对。
        #
        # ⚠️ 判据必须是「**完整覆盖**」而不是「`_match` 认得出」：
        #   `_match` 走的是**包含匹配**，`"black, white"` 整词也能命中 `black`
        #   —— 直接用它的返回值会以为"整词命中"，于是**丢掉 white**。
        #   （实测：`"black, white"` / `"black white"` / `"navy blue"` 全部只出 1 个色。）
        #
        #   完整覆盖 = 把分隔符归一（空格/逗号/斜杠/下划线/连字符 → `_`）
        #   之后与命中 key **全等**。这样：
        #     "light blue"   → `light_blue` == `light_blue`  ✓ 当一个值
        #     "black, white" → `black__white` ≠ `black`      ✗ 退回切分
        raw_value = raw.get(field)
        whole_hit: Optional[str] = None
        if isinstance(raw_value, str) and raw_value.strip():
            # ★ 用**原始字符串**判，不能用切分结果 —— `"light blue"` 切完是 2 块，
            #   用切分结果判就永远走不到这里（这正是第一版修法的错）。
            candidate = _match(raw_value, table)
            if candidate and _squash(raw_value) == candidate:
                whole_hit = candidate

        if whole_hit:
            # 整词命中 → **整个字符串就是一个值**，不看切分结果。
            # （否则 `"light blue"` 会既认成 `light_blue` 又认成切出来的 `blue`。）
            accepted.append(whole_hit)
        else:
            for chunk in _as_list(raw_value):
                hit = _match(chunk, table)
                if hit and hit not in accepted:
                    accepted.append(hit)
                elif not hit:
                    rejected.append(chunk)
        if accepted:
            clean[field] = accepted
        if rejected:
            failed[field] = rejected

    # 主色兜底：模型只给了 colors 没给 primary_color 时，取第一个
    if not clean.get("primary_color") and clean.get("colors"):
        clean["primary_color"] = clean["colors"][0]

    return clean, failed


# ---------------------------------------------------------------------------
# Prompt 组装
# ---------------------------------------------------------------------------

def _vocab_lines() -> str:
    """把受控词表排成可读的选项清单，直接嵌进 prompt。"""
    blocks: list[tuple[str, dict[str, str]]] = [
        ("type", {k: v[0] for k, v in taxonomy.CLOTHING_TYPES.items()}),
        ("primary_color / colors", taxonomy.COLORS),
        ("pattern", taxonomy.PATTERNS),
        ("material", taxonomy.MATERIALS),
        ("fit", taxonomy.FITS),
        ("length", taxonomy.LENGTHS),
        ("style", taxonomy.STYLES),
        ("season", taxonomy.SEASONS),
        ("formality", taxonomy.FORMALITIES),
        ("scene", taxonomy.SCENES),
    ]
    lines: list[str] = []
    for name, table in blocks:
        options = "、".join(f"{k}({v})" for k, v in table.items())
        lines.append(f"- {name}: {options}")
    return "\n".join(lines)


#: 结构化输出的 schema。**只声明形状，不在这里做枚举约束** ——
#: 枚举约束靠 prompt 里的选项清单 + 输出后的 `normalize_tags`。
#: 原因：不同供应商对 `enum` 的支持程度差很多，写进 schema 会平白多一层降级。
TAG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "type": {"type": "string"},
        "primary_color": {"type": "string"},
        "colors": {"type": "array", "items": {"type": "string"}},
        "pattern": {"type": "string"},
        "material": {"type": "string"},
        "fit": {"type": "string"},
        "length": {"type": "string"},
        "style": {"type": "array", "items": {"type": "string"}},
        "season": {"type": "array", "items": {"type": "string"}},
        "formality": {"type": "string"},
        "scene": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
    },
    "required": ["type"],
}

SYSTEM_PROMPT = """你是服装属性识别器。看一张衣服的照片，输出它的属性。

**只能从下面的选项里取值**，不要自创词。拿不准的字段直接省略，不要瞎填。
{options}

输出要求：
- `type` 必填；其余字段没有把握就省略
- 多值字段（colors / style / season / scene）用数组
- `season` 按这件衣服**适合穿的季节**判断；四季皆宜就写全四个
- `length` **只对长裤类有意义**（牛仔 / 西裤 / 休闲 / 运动 / 打底裤），按裤脚位置判断；
  短裤、裙子、上衣一律不填
- `confidence` 给 0~1 的整数级把握度（0.9 表示很确定）
"""


def _image_data_url(image: str | Path) -> str:
    """本地图片 → data URL。模型接不了本地路径，必须转成 base64 内联。"""
    path = Path(image)
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def build_messages(image: str | Path, hint: Optional[str] = None) -> list[dict[str, Any]]:
    """组装打标请求。`hint` 是用户顺手补的一句话（"这是冬天的外套"），
    它对识别帮助很大 —— 所以留着这个口子，而不是让调用方去拼 prompt。"""
    user_text = "识别这件衣服的属性。"
    if hint:
        user_text += f"补充信息：{hint}"
    return [
        {"role": "system", "content": SYSTEM_PROMPT.format(options=_vocab_lines())},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": _image_data_url(image)}},
            ],
        },
    ]


# ---------------------------------------------------------------------------
# 打标
# ---------------------------------------------------------------------------

def tag_image(
    image: str | Path,
    *,
    client: Optional[LLMClient] = None,
    hint: Optional[str] = None,
) -> dict:
    """照片 → 受控属性。

    返回：
        `{"tags": {...归一后的属性...}, "unresolved": {...认不出的原始值...},
          "ai_confidence": 0.0~1.0, "raw": {...模型原始输出...}}`

    失败（模型不可用 / 返回不可解析）→ **抛 `LLMError`**，由调用方决定怎么降级
    （通常是把单品留在 `tagging_status='pending'`，而不是让整个请求 500）。
    这里不吞异常，因为"打标失败"和"打标出了个空结果"必须能区分开。
    """
    cli = client or get_client(vision=True)
    messages = build_messages(image, hint=hint)
    raw = cli.chat_json(messages, schema=TAG_SCHEMA, temperature=0.0)
    if not isinstance(raw, dict):
        raw = {}
    clean, failed = normalize_tags(raw)
    return {
        "tags": clean,
        "unresolved": failed,
        "ai_confidence": _as_float(raw.get("confidence")),
        "raw": raw,
    }


def apply_tags(
    user_id: str,
    item_id: str,
    result: dict,
    *,
    tagged_by: str = "ai",
) -> Optional[dict]:
    """把打标结果写回单品。归一失败的字段**不会**被写入。

    ⚠️ 刻意**不走** `services/items.py::update()` —— 那个是"用户手改"语义，
    它会把 `tagged_by` 写成 `manual`。打标必须记成 `ai`，否则 P2 的
    "改 AI 打错的标"就分不清原始来源了。（这正是 wardrowbe 缺 `external`
    枚举导致的问题，我们不能重犯。）

    ⚠️ `tagged_by` 是**整件单品的来源**，不是"这次写了哪些字段"的来源
    （库里没有字段级来源标记）。所以只在填空模式下要小心：
    一件用户从头手填的单品（`tagged_by='manual'`），AI 补了 1 个颜色，
    整件就被记成 `ai` —— 用户填的品类/版型也跟着"变成 AI 填的"。
    调用方（`tag_and_apply`）在这种情况下会传 `tagged_by='manual'` 保住原来源。
    """
    tags = {k: v for k, v in (result.get("tags") or {}).items() if k in TAG_FIELDS}
    if not tags:
        return None

    now = db.now_iso()
    sets: list[str] = []
    args: list[Any] = []
    for field, value in tags.items():
        if field in JSON_FIELDS:
            sets.append(f"{field}=?")
            args.append(json.dumps(value, ensure_ascii=False))
        else:
            sets.append(f"{field}=?")
            args.append(value)

    sets += ["ai_confidence=?", "tagging_status=?", "tagged_by=?", "tagged_at=?", "updated_at=?"]
    args += [
        result.get("ai_confidence"),
        "tagged",
        tagged_by,
        now,
        now,
    ]
    args += [item_id, user_id]

    with db.conn() as c:
        c.execute(
            f"UPDATE clothing_items SET {', '.join(sets)} WHERE id=? AND user_id=?",
            args,
        )
        row = c.execute("SELECT * FROM clothing_items WHERE id=?", (item_id,)).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# 打标 + 写回（自动路径专用）
# ---------------------------------------------------------------------------

#: 视为"还没分类"的 `type` 值 —— AI 认出来的品类应该盖掉它们
#: 这些值视作"**还没填**"，对所有字段生效（见 `_only_blank`）。
#:
#: `'other'` 来自词表里的"其他"占位值 —— `type` 的默认值是 `other`，
#: `PATTERNS` 里有 `"other": "其他图案"`。它们语义上是"我没意见"，
#: 不是用户真的选了"其他"，所以 AI 的判断应该能覆盖掉。
_BLANK_VALUES = {"", "other"}


def _is_blank(value: Any) -> bool:
    """字段算不算"还没值"。空字符串 / None / 空列表都算。"""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, tuple)):
        return len(value) == 0
    return False


def _only_blank(tags: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    """只保留 `item` 上**还是空**的字段。

    ⚠️ 为什么不能用 `tagged_by` 判断"这个值是谁填的"：
    `services/items.py` 在用户手改时**保留原值**（`current.get("tagged_by") or "manual"`），
    所以用户把 AI 打错的标改对之后，`tagged_by` **仍然是 `ai`**。
    拿它当判据会把用户刚改对的值又覆盖回 AI 的错值。
    库里没有字段级来源标记，所以只能做到字段级"只填空"。

    ★ `slot` 是 `type` 的**派生值**，必须跟 `type` 一起处理（`items.update()` 同样如此，
    见 `services/items.py` "type 变了要同步 slot"）：
    - `type` **不写** → `slot` 也不写（否则两者会不一致）
    - `type` **要写** → `slot` **必须重新派生**，不能拿单品上那个旧的来判"空不空"

    ⚠️ 后半句是踩过的坑：`items.get()` 走 `to_out()`，会给缺失的 `slot` 补上默认值
    `accessory`（`schema.py` 的列默认值）。于是 `slot` 永远"非空" → 被当作用户填的
    过滤掉 → 结果 `type='tshirt'` 但 `slot='accessory'`。
    代价是**这件衣服永远不进任何组合**（组合数按 slot 算），而且**不报错**，
    要等"为什么我推荐的全是配饰"才发现。P5 已经在 `other` 品类上吃过同一类亏。
    """
    out: dict[str, Any] = {}
    for field, value in tags.items():
        current = item.get(field)
        # ★ `'other'` / `''` 一律视作"**还没填**"，对**所有**字段生效，
        #   不只 `type`。这些是词表里的"其他"占位值（`taxonomy.PATTERNS` 有
        #   `"other": "其他图案"`），语义上等于"我没意见"。
        #   只特判 `type` 会漏掉 `pattern` —— AI 明明认出了"条纹"，
        #   却因为单品默认 `pattern='other'` 被判定成"已填"而永远补不上
        #   （2026-10-10 W4 双审查实测复现）。
        if isinstance(current, str) and current.strip().lower() in _BLANK_VALUES:
            current = None
        if _is_blank(current):
            out[field] = value
    # ★ `slot` 只可能由下面这两行产生（循环里它必然被判"非空"而过滤掉 ——
    #   `to_out()` 补的默认值 `accessory` 永不为空）。刻意集中在这里处理。
    if "type" in out:
        out["slot"] = taxonomy.slot_of(out["type"])
    else:
        out.pop("slot", None)
        # ⚠️ `type` 没写，但库里的 `slot` 跟 `type` **对不上**（历史脏数据）：
        #   照样要按 `type` 重新派生修好。
        #
        #   不修的代价（实测复现）：`wardrobe._by_slot()` 直接按 `it["slot"]`
        #   分组算组合数（`services/wardrobe.py:89`），所以一件 `type=shirt`
        #   却被存成 `slot=accessory` 的衣服，会**永远不进任何组合**，
        #   缺口分析和补货建议一起错，而且全程不报错。
        #
        #   只在"确实不一致"时才写 —— 一致的话不产生任何多余 UPDATE。
        expected = taxonomy.slot_of(str(item.get("type") or "other"))
        if str(item.get("slot") or "") != expected:
            out["slot"] = expected

    # ★ 字段间一致性：`primary_color` **必须属于** `colors`。
    #
    #   逐字段判空看不出这类矛盾：用户填了 `colors=['black']`（非空 → AI 的
    #   `colors` 被跳过），AI 认出 `primary_color='navy'`（空 → 写入），
    #   结果「主色不属于配色」。`taxonomy._stats()` 用 `primary_color`
    #   统计颜色多样性（`services/taxonomy.py:435`），分母会算错，且不报错。
    #
    #   裁决：**以用户的 `colors` 为准**，AI 的 `primary_color` 丢弃 ——
    #   它只是个派生字段（本来就该从 `colors[0]` 来），不值得跟用户的数据打架。
    #   反过来（用户填了 `primary_color` 但 `colors` 空）不在此处理：
    #   `normalize_tags` 已经用 `colors[0]` 做过主色兜底了。
    if "primary_color" in out and "colors" not in out:
        existing = item.get("colors") or []
        if isinstance(existing, list) and existing and out["primary_color"] not in existing:
            out.pop("primary_color")
    return out


def _tag_result(
    status: str,
    *,
    tags: Optional[dict] = None,
    unresolved: Optional[dict] = None,
    confidence: Optional[float] = None,
    message: Optional[str] = None,
    raw: Optional[dict] = None,
) -> dict:
    return {
        "status": status,
        "tags": tags or {},
        "unresolved": unresolved or {},
        "ai_confidence": confidence,
        "message": message,
        "raw": raw,
        "item": None,
    }


def tag_and_apply(
    user_id: str,
    item_id: str,
    *,
    hint: Optional[str] = None,
    overwrite: bool = False,
    client: Optional[LLMClient] = None,
) -> dict:
    """打标 + 写回。**自动路径专用**：失败不抛异常，只回报状态。

    和手动 `tag_image` + `apply_tags` 的区别：
    - **失败不抛**：上传照片时顺手打标，打标炸了不能让上传变成 4xx/5xx
    - 支持"只填空、不覆盖"（`overwrite=False`），保护用户已经填过的值
    - 图片**从库里读**（`images.read_image`），与 `routes/tagging.py` 一致 ——
      保证用的是已落库的那张图，而不是某个可能已被替换的临时文件

    返回 `{"status", "tags", "unresolved", "ai_confidence", "message", "item"}`：
      `applied` 已写库 · `empty` 没有可写的属性 · `skipped` 没配视觉模型（**不是故障**）
      · `failed` 模型不可用 / 图片丢了（单品留在 `pending`，属性可以手填）
    """
    # ⚠️ 必须走 `items.get()`：它过 `to_out()`，多值字段才是列表。
    #    直接读原始行会把 `colors` 的 JSON 文本当字符串逐字符消费（P1 踩过）。
    item = items.get(item_id, user_id)
    if not item:
        return _tag_result("failed", message="单品不存在")

    if not config.VISION_READY:
        return _tag_result("skipped", message="还没配视觉模型，属性可以手动填")

    try:
        image_file = images.read_image(str(item.get("image_path") or ""))
    except OSError as e:
        # 磁盘层异常（文件被占用、权限、坏块）——同样不能穿透，
        # 理由同下面的 `apply_tags` 包裹。
        return _tag_result("failed", message=f"读图片失败：{e}")
    if image_file is None:
        return _tag_result("failed", message="图片文件丢了，请重新上传")

    try:
        result = tag_image(image_file, client=client, hint=hint)
    except LLMError as e:
        return _tag_result("failed", message=f"模型不可用：{e}")
    except Exception as e:
        # ⚠️ `tag_image` 内部会读文件（`_image_data_url` → `read_bytes`），
        #   磁盘层的 `OSError`（Windows 上文件被别的进程占用 → `PermissionError`）
        #   不属于 `LLMError`，会直接穿透 —— 上传接口就变成 500，
        #   而**照片其实已经存好了**，用户只看到"上传失败"。
        return _tag_result("failed", message=f"读图或识别失败：{type(e).__name__}: {e}")

    unresolved = dict(result.get("unresolved") or {})
    confidence = result.get("ai_confidence")
    raw = result.get("raw")
    tags = dict(result.get("tags") or {})
    if not tags:
        return _tag_result(
            "empty", unresolved=unresolved, confidence=confidence, raw=raw,
            message="这张图里我没认出可用的属性",
        )

    if not overwrite:
        tags = _only_blank(tags, item)
        if not tags:
            return _tag_result(
                "empty", unresolved=unresolved, confidence=confidence, raw=raw,
                message="这件已经有属性了，没有需要补的",
            )

    # 来源标记：**只填空**且单品本来是用户手填的 → 保住 `manual`。
    # 全量覆盖（手动点按钮）→ 记 `ai`（这次结果就是 AI 给的）。
    #
    # ⚠️ 为什么不看"这次写了哪些字段"：库里没有字段级来源标记，
    #   `tagged_by` 是整件单品的来源。用户手填的 `type`/`fit` 不该因为
    #   AI 补了个颜色就被记成 "ai 填的"（2026-10-10 W4 双审查实测复现）。
    source = "ai" if overwrite or item.get("tagged_by") != "manual" else "manual"

    try:
        row = apply_tags(user_id, item_id, {**result, "tags": tags}, tagged_by=source)
        fresh = items.get(item_id, user_id) if row else None
    except Exception as e:
        # ⚠️ 必须包住 `apply_tags` 和后面的重读。它会碰数据库
        #   （`sqlite3.OperationalError` / `IntegrityError`）。
        #   不包的话，自动路径（上传照片时顺手打标）会因为一个**写库失败**
        #   变成 500 —— 可照片**已经存好了、旧的也已经换掉了**，
        #   用户收到"上传失败"，重试同一张图还会再撞一次唯一索引。
        #   （2026-10-10 W4 双审查实测复现。）
        return _tag_result(
            "failed",
            tags=tags, unresolved=unresolved, confidence=confidence, raw=raw,
            message=f"识别成功但写库失败（属性没保存，可手动填）：{type(e).__name__}: {e}",
        )

    # ⚠️ 返回的 `item` 必须**是 `to_out()` 口径**：`apply_tags` 给的是**原始行**
    #   （`colors` 还是 JSON 文本），直接透出去会让下游拿到逐字符消费的字符串。
    return {
        "status": "applied",
        "tags": tags,
        "unresolved": unresolved,
        "ai_confidence": confidence,
        "message": None,
        "raw": raw,
        "item": fresh,
    }
