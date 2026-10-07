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

from .. import db, taxonomy
from .llm.client import LLMClient, get_client

# ---------------------------------------------------------------------------
# 受控字段
# ---------------------------------------------------------------------------

#: 单值字段 → `taxonomy.VOCABULARIES` 的键
SINGLE_VOCAB: dict[str, str] = {
    "primary_color": "color",
    "material": "material",
    "fit": "fit",
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
    "pattern", "material", "fit", "style", "season", "formality", "scene",
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
        value = _match(raw.get(field), taxonomy.VOCABULARIES.get(vocab_key, {}))
        if value:
            clean[field] = value
        elif raw.get(field):
            failed[field] = raw[field]

    pattern = _match(raw.get("pattern"), taxonomy.PATTERNS)
    if pattern:
        clean["pattern"] = pattern
    elif raw.get("pattern"):
        failed["pattern"] = raw["pattern"]

    for field, vocab_key in MULTI_VOCAB.items():
        table = taxonomy.VOCABULARIES.get(vocab_key, {})
        accepted: list[str] = []
        rejected: list[str] = []
        for chunk in _as_list(raw.get(field)):
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


def apply_tags(user_id: str, item_id: str, result: dict) -> Optional[dict]:
    """把打标结果写回单品。归一失败的字段**不会**被写入。

    ⚠️ 刻意**不走** `services/items.py::update()` —— 那个是"用户手改"语义，
    它会把 `tagged_by` 写成 `manual`。打标必须记成 `ai`，否则 P2 的
    "改 AI 打错的标"就分不清原始来源了。（这正是 wardrowbe 缺 `external`
    枚举导致的问题，我们不能重犯。）
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
        "ai",
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
