"""粘贴录入：把用户粘来的商品分享文案，变成可入库的单品草稿。

规格：`docs/设计文档_v0.2.md` §11.3（PRD）+ §10.6（受控选择）。

## 这一层是"读文本"，不是"读网页"

实测（2026-10-07）：淘宝无登录态 6 条入口全被挡（短链 / SSR 页 / PC 页 / 天猫 /
mtop 接口 / 老版 H5）。**不抓取任何网页** —— 数据本来就在分享文案的「」里。

## 分层

本模块**不 import `conversation`**。原因：W2 会让 `conversation` 反过来用本模块，
模块级互相 import 会撞上"半初始化模块"（ImportError，且报错信息很难懂）。
所以属性抽取仍由 `conversation.extract_fields` 负责，**由调用方做完再传进来**，
这里只提供一个 `fill_type()` 把品类补上。
判据：`grep -E "import.*conversation" app/services/ingest.py` 应为空。

## 品类识别的三级顺序（★ 模型是第三手段，不是第一手段）

① `taxonomy.resolve_type()` —— 精确别名 / 最长包含匹配（**零成本、确定性**）
② 仍落 `other` → 才调模型，且模型只能**从受控 key 清单里挑一个**
③ 模型回值必须**再过一次** `resolve_type()`；仍归不到就按"认不出"处理

⚠️ 顺序不可颠倒。反过来会把本该零成本的本地命中变成付费调用 ——
而且**救不了**词表缺口（模型回「卫裤」，`resolve_type` 在补别名前照样给 `other`）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from .. import config, taxonomy

# ---------------------------------------------------------------------------
# 正则
# ---------------------------------------------------------------------------

#: URL。刻意排除中文标点与引号 —— 分享文案里 URL 后面常紧跟空格 / 「」/ 换行
_URL_RE = re.compile(r"""https?://[^\s"'<>，。、；）)】」』]+""")

#: 商品名。淘宝/京东等把商品标题放在中文书名号里
_TITLE_RE = re.compile(r"[「『]([^」』]+)[」』]")

#: 平台标签，形如 `【淘宝】`
_PLATFORM_RE = re.compile(r"【([^】]{1,8})】")

#: 分享码，形如 `CZ007` / `CZ057`。
#: 前后都要求非字母数字，避免把标题里的 `A4` `100` 之类当成分享码
_CODE_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z]{1,4}\d{2,8})(?![A-Za-z0-9])")

#: 兜底路径的最低要求：必须含汉字或字母。
#: 挡掉 `？？？`、`……`、【】 这类纯符号 —— 它们不可能是商品名。
#: ⚠️ 刻意**只做这一道**：像「今天穿什么」这种短句与「宽松直筒牛仔裤」这种真标题
#: 在**没有 URL / 书名号**时本质上是分不开的，靠启发式去猜只会误伤真标题。
#: 所以这里选择"放过去、但让用户看得见"（前端会显示标题 + 要求选品类）。
#: 微信端不受此影响 —— 那边由 `is_share_like` 严格预筛。
_HAS_WORD_RE = re.compile(r"[0-9A-Za-z\u4e00-\u9fff]")

#: 已知平台（用于把【淘宝】这类标签识别成平台，而不是当成标题的一部分）
KNOWN_PLATFORMS = frozenset(
    {"淘宝", "天猫", "京东", "拼多多", "得物", "抖音", "小红书", "唯品会", "1688", "苏宁"}
)

#: 仅在"没有书名号"的兜底路径下才剥离的噪音词。
#: ⚠️ 主路径（有「」）**绝不**剥标题内部 —— 那是卖家自己写的商品名，动它等于改数据。
_NOISE_WORDS = (
    "7天无理由退货",
    "七天无理由退货",
    "无理由退货",
    "点击链接直接打开",
    "点击链接",
    "淘宝搜索直接打开",
    "直接打开",
    "打开手机淘宝",
    "复制这段",
    "包邮",
)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ParsedShare:
    """一段分享文案里解析出的一件商品。"""

    title: str          # 干净的商品标题（**入库时 `name` 用它，不用原始整段**）
    url: str = ""       # 第一个 URL；没有则空串
    code: str = ""      # 分享码；没有则空串
    platform: str = ""  # 淘宝 / 天猫 / …；没有则空串
    raw: str = ""       # 原始片段，回显用


@dataclass(frozen=True)
class TypePick:
    """品类识别结果。三个字段分开，是为了让调用方能**如实告知**降级原因。"""

    key: str                # 归一后的 type_key，可能是 "other"
    used_llm: bool = False  # 是否真的调了模型
    error: str = ""         # 调了但失败时的原因（不静默）


# ---------------------------------------------------------------------------
# ① 预筛：这条消息像不像商品分享文案
# ---------------------------------------------------------------------------

def is_share_like(text: str, *, min_title_len: int = 4) -> bool:
    """**必须放在意图分类之前调用。**

    为什么不能塞进 `classify_intent` 的分支里：`conversation._QUESTION_HINTS`
    含 `'?'`，而分享链接形如 `...?tk=xxx` → 实测（2026-10-08）真实链接被判成
    `ask`（知识库提问），跑去答非所问还白烧一次模型。

    判据刻意保守：**必须**有 URL，或有「」包着的像样标题。
    普通聊天（"今天穿什么"、"这样搭好吗？"）一律 False。
    """
    t = (text or "").strip()
    if not t:
        return False
    if _URL_RE.search(t):
        return True
    return any(
        len(m.group(1).strip()) >= min_title_len for m in _TITLE_RE.finditer(t)
    )


# ---------------------------------------------------------------------------
# ② 解析：文本 -> 若干件商品
# ---------------------------------------------------------------------------

def _clean_title(s: str) -> str:
    """标题收尾清理。**只动首尾空白与标点，不动内部**。"""
    s = (s or "").strip()
    return s.strip(" 。．.,，、;；:：\t\r\n")


def _strip_noise(s: str) -> str:
    """兜底路径专用：剥掉噪音词。主路径不用（见 `_NOISE_WORDS` 的注释）。"""
    out = s
    for w in _NOISE_WORDS:
        out = out.replace(w, " ")
    return _clean_title(out)


def _last_before(pairs: list[tuple[int, str]], pos: int) -> str:
    """取位置在 `pos` 之前、且最靠近的那一个。"""
    hit = ""
    for start, val in pairs:
        if start < pos:
            hit = val
        else:
            break
    return hit


def _in_span(pairs: list[tuple[int, str]], start: int, end: int) -> str:
    """取**落在 [start, end) 之内**的第一个。用于逐行兜底：先把本行自己的找出来。"""
    for pos, val in pairs:
        if start <= pos < end:
            return val
    return ""


def _extract_platform(text: str, before: int) -> str:
    tags = [
        (m.start(), m.group(1))
        for m in _PLATFORM_RE.finditer(text)
        if m.group(1) in KNOWN_PLATFORMS
    ]
    return _last_before(tags, before)


def parse_share(text: str) -> list[ParsedShare]:
    """把一段（可能含多件的）分享文案解析成若干 `ParsedShare`。

    **比 `is_share_like` 宽松**：网页端的粘贴框是用户明确要求"解析这段"，
    所以没有 URL、没有书名号的纯标题也接受。微信端靠 `is_share_like` 先筛。

    配对规则：以**书名号标题**为锚（这是最可靠的信号），
    每件商品配"它前面最近的那个 URL / 分享码 / 平台标签"。
    没有书名号时退回逐行解析。
    """
    t = (text or "").strip()
    if not t:
        return []

    urls = [(m.start(), m.group(0)) for m in _URL_RE.finditer(t)]
    codes = [(m.start(), m.group(1)) for m in _CODE_RE.finditer(t)]
    titles = [(m.start(), _clean_title(m.group(1))) for m in _TITLE_RE.finditer(t)]
    titles = [(p, s) for p, s in titles if s]

    out: list[ParsedShare] = []

    if titles:
        for idx, (pos, title) in enumerate(titles):
            url = _last_before(urls, pos)
            if not url:
                # 标题在 URL 之前（少见，但别丢）→ 取该标题之后最近的一个
                after = [u for p, u in urls if p > pos]
                url = after[0] if after else ""
            code = _last_before(codes, pos)
            out.append(
                ParsedShare(
                    title=title,
                    url=url,
                    code=code,
                    platform=_extract_platform(t, pos),
                    raw=title,
                )
            )
        return out

    # ---- 兜底：没有书名号 ----
    # 逐行试。⚠️ 这条路径**注定宽松**（见 `_HAS_WORD_RE` 的注释）——
    # 它的存在是为了支持"用户只复制了标题"这种用法（PRD 选项 A）。
    #
    # ⚠️ **这里不剥分享码**（W4 审查抓到）：`_CODE_RE` 只按"字母+数字"的形状判断，
    #    分不清分享码 `CZ007` 和真实型号 `GT2000` / `AJ1130` / `A1234` ——
    #    剥掉会把型号从商品名里删掉（实测 `"Nike GT2000 跑步鞋"` → `"Nike   跑步鞋"`，
    #    而带书名号的同一句 `「Nike GT2000 跑步鞋」` 却保留）。两条路径必须一致：
    #    **宁可名字里多一个分享码，也不要把型号删掉**（主路径本来就"绝不剥标题内部"）。
    for line in t.splitlines():
        s = _URL_RE.sub(" ", line)
        s = _PLATFORM_RE.sub(" ", s)
        s = _strip_noise(s)
        if not (s and _HAS_WORD_RE.search(s)):
            continue
        # 这一行的 URL / 分享码：先在本行范围内找；找不到就退到"本行结束前最近的那个"
        start = t.find(line)
        end = start + len(line)
        out.append(
            ParsedShare(
                title=s,
                url=_in_span(urls, start, end) or _last_before(urls, end),
                code=_in_span(codes, start, end) or _last_before(codes, end),
                platform=_extract_platform(t, end),
                raw=line.strip(),
            )
        )
    # ⚠️ 原来这里还有一段"连换行都没有"的收尾分支，**恒不可达**：
    #   逐行都抽不出词时，拼回整串同样抽不出（`_HAS_WORD_RE` 对两者等价）。
    #   W4 审查（Bug 猎手）抓到，已删 —— 留着只会掩盖"兜底丢 url"那个 bug。
    return out


def normalize_title(title: str) -> str:
    """查重用的归一化标题：小写 + 去掉所有非字母数字汉字。

    用于"疑似重复"提示（**只提示，不阻止** —— 见 §11.3 失败语义）。
    ⚠️ 不能用 `image_hash` 去重：那是**部分唯一索引**
    （`WHERE image_hash IS NOT NULL`），而粘贴录入没有图 → 永远是 NULL → 不去重。
    """
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", (title or "").lower())


# ---------------------------------------------------------------------------
# ③ 品类：本地优先，模型兜底（受控选择）
# ---------------------------------------------------------------------------

def _type_options() -> str:
    """把受控品类清单排成可读选项，嵌进 prompt。

    ⚠️ 属性名是 `taxonomy.CLOTHING_TYPES`（不是 `TYPES`）。
    """
    return "、".join(
        f"{k}({v[0]})" for k, v in taxonomy.CLOTHING_TYPES.items() if k != "other"
    )


#: ⚠️ **必须显式要求 JSON 对象，并给出形状。**
#: 原因（2026-10-08 真机实测，**P4 同一个坑的翻版**）：
#: DeepSeek `deepseek-v4-pro` 的 `capabilities()` 是
#: `supports_json_schema=False, supports_json_mode=False` ——
#: `chat_json` 只能走**最后一级**「纯文本 + 正则抽 JSON」。
#: 而按"只输出一个 key、不要解释"的写法，模型会回**裸词** `other`
#: （回答**完全正确**），却因为没有 `{...}` 可抽 →
#: 抛 `LLMError("策略 text 未能返回可解析的 JSON 对象")`。
#: 现象极具误导性：看着像"模型没接上"，其实是**我们没跟它要 JSON**。
#: ⇒ 单测全绿 ≠ 能用；静态审查抓不到"模型会怎么回答"。
_SYSTEM = """你是服装品类识别器。用户给你一段电商商品标题，判断它属于哪个品类。

**只能从下面的清单里挑一个 key**，不要自创、不要翻译、不要输出中文名：
{options}

规则：
- 只能挑大类就挑最接近的大类（例：说不清是什么裤子就挑 pants）
- 标题里没有可识别的服装品类（例如是配饰以外的杂物、乱码）→ 回 "other"
- 不要输出任何解释

输出 JSON：
{{"type": "pants"}}
"""

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"type": {"type": "string"}},
    "required": ["type"],
}


def pick_type(title: str, *, client: Any = None) -> TypePick:
    """决定一件商品的品类。**只决定品类，不碰其它属性。**

    顺序即成本：本地能定就**绝不**调模型。
    """
    local = taxonomy.resolve_type(title)
    if local != "other":
        return TypePick(key=local, used_llm=False)

    if client is None:
        if not config.LLM_READY:
            # ★ "没配模型"和"模型挂了"都是**不可用**，都要如实说，不能静默。
            #   规格 §11.3 失败语义表：模型不可用（没 key / 401 / 超时）→
            #   降级为只用本地词表，**如实告知**。
            #   （W4 审查抓到：原来这里 `error=""`，用户只看到"没认出品类"，
            #   不知道是因为压根没接模型 —— 这正是本项目最怕的静默降级。）
            return TypePick(
                key="other",
                error="没配置模型（LLM_BASE_URL / LLM_API_KEY / LLM_MODEL 缺一个）",
            )
        from .llm.client import get_client  # 延迟 import：没配模型时不该拉起网络栈
        try:
            client = get_client()
        except Exception as exc:  # noqa: BLE001 —— 没接上不是异常状态，是正常降级
            return TypePick(key="other", error=f"{type(exc).__name__}: {exc}")

    from .llm.client import LLMError

    messages = [
        {"role": "system", "content": _SYSTEM.format(options=_type_options())},
        {"role": "user", "content": title},
    ]
    try:
        raw = client.chat_json(messages, schema=_SCHEMA, temperature=0.0)
    except LLMError as exc:
        return TypePick(key="other", used_llm=True, error=str(exc))
    except Exception as exc:  # noqa: BLE001 —— 网络/解析异常一律降级，不冒泡打断录入
        return TypePick(key="other", used_llm=True, error=f"{type(exc).__name__}: {exc}")

    picked = str((raw or {}).get("type") or "").strip()

    # ★ 本地再归一一次。**不能假设模型遵守了清单** ——
    # 它可能回「卫裤」这类口语词，那样 resolve_type 在词表没补别名时给 other，
    # 等于白调一次还失败。
    mapped = taxonomy.resolve_type(picked)
    return TypePick(key=mapped, used_llm=True)


def fill_type(fields: dict[str, Any], title: str, *, client: Any = None) -> TypePick:
    """就地给 `fields` 补 `type`。**只在缺失或 `other` 时才动手。**

    返回 `TypePick`，让调用方能如实告知"模型没接上，这次只用了本地词表"。
    """
    current = str(fields.get("type") or "").strip()
    if current and current != "other":
        return TypePick(key=current, used_llm=False)

    pick = pick_type(title, client=client)
    if pick.key != "other":
        fields["type"] = pick.key
    return pick


# ---------------------------------------------------------------------------
# ④ 组合入口：解析 + 抽属性 + 补品类
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Candidate:
    """一件**候选**商品（还没入库）。微信端与网页端共用。"""

    title: str
    fields: dict[str, Any]
    unresolved: dict[str, str]
    pick: TypePick
    url: str = ""
    code: str = ""
    platform: str = ""

    @property
    def needs_type(self) -> bool:
        """品类没定 → 调用方**必须**让用户先补，不许直接入库（§11.3 失败语义）。"""
        return str(self.fields.get("type") or "").strip() in ("", "other")


def _default_extract_fields(text: str) -> tuple[dict[str, Any], dict[str, str]]:
    """`conversation.extract_fields` 的**懒加载代理**。

    为什么要懒加载：`conversation` 要 import 本模块（用 `is_share_like` /
    `build_candidates`），本模块在**模块级** import 它会撞"半初始化模块"
    （ImportError，报错信息还很难懂）。放进函数体就避开了 ——
    真跑到这一行时 `conversation` 早就加载完了。
    """
    from .conversation import extract_fields

    return extract_fields(text)


def build_candidates(
    text: str,
    extract_fields: Callable[[str], tuple[dict[str, Any], dict[str, str]]] | None = None,
    *,
    client: Any = None,
) -> list[Candidate]:
    """解析 → 抽属性 → 补品类。**微信端和网页端共用这一个入口。**

    `extract_fields` 一般不用传（默认走懒加载代理取 `conversation.extract_fields`）；
    留这个参数是给测试注入用的。
    """
    extract = extract_fields or _default_extract_fields
    out: list[Candidate] = []
    for parsed in parse_share(text):
        fields, unresolved = extract(parsed.title)
        # ★ `name` 用**干净标题**，不用整段原文。
        #   放在这里而不是各调用方，是因为**两个入口都要** ——
        #   漏一个就会静默存出 `【淘宝】7天无理由退货 https://...` 这种脏名字。
        fields["name"] = parsed.title
        pick = fill_type(fields, parsed.title, client=client)
        out.append(
            Candidate(
                title=parsed.title,
                fields=fields,
                unresolved=unresolved,
                pick=pick,
                url=parsed.url,
                code=parsed.code,
                platform=parsed.platform,
            )
        )
    return out
