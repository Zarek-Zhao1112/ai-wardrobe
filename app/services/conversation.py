"""会话状态机：把一条消息变成"该做什么"，再变成一句回复。

**边界**（与 `channels/clawbot.py` 的分工，见 `docs/p3-design.md` §2）：

    本文件认：意图、会话状态、怎么调业务
    本文件**不认**：`context_token`、长轮询、iLink、任何协议细节

自检判据（W4 审查后修正）：本文件**不 `import httpx`**、不拼 URL、不出现任何
iLink 端点名。`context_token` 会作为形参出现在这里（因为要持久化它）——
它只做**原样透传**、不做任何解释，这**不算**越界。

---

## 状态机（第一版：纯规则，不调模型）

```
收到消息
  ├─ 会话过期？ → 清 state
  ├─ 有待确认草稿 且 像"确认"  → 入库 → 回复已入库 → 清草稿
  ├─ 有待确认草稿 且 像"修正"  → 改字段 → 回显 → 继续等确认
  ├─ 有待确认草稿 且 都不像    → 放弃草稿（避免卡死），当新消息处理
  ├─ "没得搭/缺什么"           → 场景 E
  ├─ "想买/值得买"             → 明确告知在 P4（不假装能做）
  ├─ 像"描述一件衣服"          → 场景 C：抽属性 → 存草稿 → 反问确认
  ├─ "怎么搭/配什么"           → 场景 A：推荐 → 存 last_outfits
  ├─ 序号/这套行/换一套 + 有候选 → 反馈
  └─ 都不命中 → 列出能做什么（不猜、不装懂）
```

**为什么第一版不用 LLM 做意图识别**：高频输入（"怎么搭"、"第 2 套"、"对"）
规则就能覆盖；规则**可测试、零成本、结果可复现**。规则不中就明确提示，
比猜错强 —— 猜错的代价是"它把我说的 A 当成了 B"，而用户看不出为什么。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any, Optional

from .. import db, taxonomy
from ..channels import InboundMessage
from ..models import ItemCreate
from . import feedback as feedback_svc
from . import items as item_svc
from . import recommend as recommend_svc
from . import wardrobe as wardrobe_svc
from .knowledge import session as kb_session
from .llm.client import LLMError

CHANNEL = "clawbot"

#: 会话有效期。超过就当成新对话（清掉待确认草稿与上次的搭配候选）。
#:
#: 取值理由（`docs/p3-design.md` §3.1 ②）：太短（5 分钟）"想一下再回"就失效；
#: 太长（跨天）昨天问的搭配今天回「第一套」会关联错，而用户早忘了是哪套。
#: 2 小时覆盖"出门前问、路上回"这个真实节奏。
SESSION_TTL = timedelta(hours=2)

#: 待确认草稿最多保留多久 —— 比整个会话短：一个"要不要入库"的问题
#: 放两小时再问就没意义了。
DRAFT_TTL = timedelta(minutes=30)

#: 待落盘的**笔记**最多保留多久（P4）。
#:
#: 比衣物草稿长得多：生成一次要跑十几次模型调用、用户可能先看半天再回「落」；
#: 但也不能无限期 —— 隔天的「落」不该落到昨天那份提纲上。6 小时覆盖"晚上聊完、
#: 睡前后悔改主意还来得及"。
NOTE_TTL = timedelta(hours=6)

#: 回复里每套搭配前的序号（**必须有** —— 否则用户说"第 2 套"时双方没有共同语言）
NUM_EMOJI = ("1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣")

# ---------------------------------------------------------------------------
# 词表：意图识别用的关键词
# ---------------------------------------------------------------------------

_CONFIRM = {"对", "对的", "是", "是的", "好", "好的", "可以", "嗯", "没错", "正确", "确认", "ok", "okay"}
_CORRECT = ("不对", "不是", "错了", "应该", "改成", "其实", "不是这个")
_GAP = ("没得搭", "没什么可搭", "缺什么", "缺哪", "搭不出", "没有可搭", "没衣服穿")
_BUY = ("想买", "值得买", "适合我吗", "要不要买", "买这件")
#: 指代"刚才那件"的口语 —— 有 `last_items` 时，光说「这件」也算在问搭配（规格 §4 分支 7）
_REFER = ("这件", "这条", "这双", "这顶", "这身", "这个", "那件", "那条", "那双", "它")
_MATCH = ("怎么搭", "搭配", "配什么", "今天穿", "穿什么", "怎么穿", "搭一下")
#: 肯定反馈 —— **子串**匹配，所以每一条都必须是"说得出口的完整短语"。
_LIKE = ("这套行", "这套可以", "不错", "好看", "喜欢", "就这套")
#: 只说一个词就算肯定 → **必须整句相等**。
#: ⚠️ 单字「行」曾经躺在上面那个子串列表里，于是「我去**银行**」被判成 like，
#:    并**真的写进偏好记忆**（W4 审查抓到）。单字/宽泛词一律走精确匹配。
_LIKE_EXACT = {"行", "行的", "行吧", "可以", "可以了", "好的", "好", "嗯",
               "不错", "好看", "喜欢", "就这套"}
_REJECT = ("不行", "不喜欢", "不好看", "换一套", "都不行", "都不喜欢", "再换")
#: "新的一件衣服"的标志词。**用子串匹配，不用 `startswith`** ——
#: 「我**买了**条裤子」不以「买了」开头（开头是「我」），曾经因此被判成
#: 对当前草稿的"修正"，新单品属性被并进旧草稿（W4 审查抓到）。
_DESCRIBE = ("我有一件", "我有件", "新买", "刚买", "刚到手", "新入", "入手", "买了")
#: 抽完属性后要剔掉的"口水话" —— 用来判断还有哪些词**没被识别**（见 `extract_fields`）
_FILLER = ("我有一件", "我有件", "有一件", "有件", "新买的", "新买", "刚买的", "刚买",
           "刚到手", "入手", "买了", "最近", "一个", "一件", "一条", "一双", "一顶",
           "这件", "那条", "我的", "还有", "另外", "比较", "有点", "稍微", "很", "挺",
           "我", "的", "是")

#: 中文数字（用户很可能说"第二套"而不是"第2套"）
_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5}
#: 匹配「2」「第2」「第 2 套」「二」「第二套」
_INDEX_RE = re.compile(r"^\s*第?\s*([1-5一二两三四五])\s*[套个]?\s*$")
#: 匹配「2不行」「第2套不行」这种"序号 + 态度"
_INDEX_TAIL_RE = re.compile(r"^\s*第?\s*([1-5一二两三四五])\s*[套个]?\s*(.*)$")

# ---------------------------------------------------------------------------
# 知识库技能（P4）的词表
# ---------------------------------------------------------------------------

#: "想学某个知识主题"的信号词。
_LEARN = ("我想学", "想学", "学一下", "讲讲", "讲一下", "了解一下", "了解下", "教教我")

#: ⚠️ **反向闸**：句子里出现这些词 → 说明还在说衣服，不是想学知识。
#:
#: 没有它，「我想学怎么**搭配**」会被抢到知识库分支去（那是在问穿搭）；
#: 「讲一下**这件**」同理。这层闸是"两个领域共用一句话"时的分界。
_LEARN_GUARD = ("衣橱", "单品", "这件", "这条", "这双", "这顶", "这身",
                "搭配", "怎么穿", "穿什么", "衣服")

#: "让助手挑一个"的信号词。
_BROWSE = ("随便挑", "随便聊聊", "随便学点", "最近学点", "学点什么",
           "你挑一个", "有什么可以学", "能学什么")

#: 从候选列表里挑一篇时的序号（「1」「第2」「二篇」）。
#: 与搭配那边的 `_INDEX_RE` 分开：那边只认「套/个」，这边还要认「篇」。
#: ⚠️ 范围开到 **9** —— 领域数量会长，写死 5 会让"第 6 项"永远选不中。
_NOTE_INDEX_RE = re.compile(r"^\s*第?\s*([1-9一二两三四五六七八九])\s*[篇个]?\s*$")

#: "把这次聊的总结成笔记"（生成），**不落盘**。
#: ★ 「记下来」「记一下」「记一笔」也在这（审查补的F-3）—— 用户说「我随口说它帮我记」
#:   是核心场景之一，而原来只有「帮我记下来」这个长词，短说法全部落到 unknown。
_NOTE_GEN = ("总结", "写成笔记", "写篇笔记", "生成笔记", "整理成笔记", "记成笔记",
             "做个笔记", "帮我记下来", "帮我记一下", "整理一下", "记下来", "记一下",
             "记一笔", "记个笔记", "沉淀一下", "存成笔记")

#: 确认落盘。⚠️ 只在**有待落盘笔记时**才判 —— 所以「落」这种短词不会误伤日常对话。
#:
#: ⚠️⚠️ **这里刻意不含裸「落」**（审查抓到的阴招）：「落肩」是标准服装版型，
#: 子串匹配会让 `我有一件落肩卫衣` 被判成"确认落盘" → 衣服没入库、需求被答偏。
#: 裸「落」改由下面的正则处理 —— 要求它**独立成词**。
_NOTE_SAVE = ("写入", "写吧", "存吧", "保存", "入库", "可以落", "就落", "写进去",
              "落盘", "落吧")

#: 确认落盘的**整句**判据：只有这一串里的话才算（配合 `_NOTE_SAVE` 的子串判据）。
_NOTE_SAVE_EXACT_RE = re.compile(
    r"^(?:落|落吧|落盘|写吧|存吧|保存|写入|入库|写进去|可以落|就落|好吧落)"
)

#: 放弃这次生成。
_NOTE_DROP = ("算了", "不落", "不写", "取消", "不用了", "先不了")

#: 同意建新领域目录 —— ⚠️ **只在 `pending_domain` 存在时才判**（审查补的 F-8）。
#:
#: 回复里承诺过「你说『开』我就建目录」，但原来**根本没有这个分支**，
#: 用户回「开」只会拿到帮助菜单 —— 承诺是假的。
#: 限定在"待建主题"状态下，才不会把「开个会」这类日常话判成建目录。
_DOMAIN_OPEN = ("开", "开吧", "开一个", "开新的", "建", "建吧", "建一个", "创建",
                "就建", "建个", "开主题", "建主题")

#: 「像在问问题」的特征词 —— 知识库问答兜底用（`ask`）。
#:
#: ★ 只在**所有其它意图都落空**之后才判（规格 §5.2 最后一行），且**零扩词面**：
#:   这里全是"疑问形状"而不是主题词，所以不会把「我想学卫衣怎么挑」这类
#:   已经被前面分支接走的句子抢过来。
_QUESTION_HINTS = ("？", "?", "是什么", "为什么", "怎么", "如何", "区别",
                    "有哪些", "哪个", "什么时候", "能不能")


# ---------------------------------------------------------------------------
# 纯函数（不碰库、不碰网络 —— 所以可以单测）
# ---------------------------------------------------------------------------


def parse_index(text: str, count: int) -> Optional[int]:
    """把「2」「第2套」「二」解成 **0-based 下标**。解不出或越界返回 `None`。"""
    m = _INDEX_RE.match(text or "")
    if not m:
        return None
    raw = m.group(1)
    n = int(raw) if raw.isdigit() else _CN_NUM.get(raw)
    if n is None or not (1 <= n <= count):
        return None
    return n - 1


def _is_new_item(text: str) -> bool:
    """这句是在说**新的一件衣服**，还是在**修正当前草稿**？

    ⚠️ 原来用 `text.startswith(p)`：「我买了条裤子」开头是「我」而不是「买了」
    → 不算新的一件 → 被判成"修正"，新单品的属性被合并进旧草稿，
    用户回「对」入库的是一条**属性串了味**的衣服（W4 审查抓到）。
    改成子串匹配，并显式排除否定式（「不买了」不是在描述衣服）。
    """
    if "不买" in text or "别买" in text:
        return False
    return any(p in text for p in _DESCRIBE)


def _looks_like_clothing(text: str) -> bool:
    """这句在讲**衣服**吗？—— 知识库分支的"让路守卫"。

    ★ W5 自己引入又自己抓到的（2026-10-06）：给 `_NOTE_GEN` 补了「记一下」这类
    短说法之后，「记一下这件卫衣」被判成"要记笔记"→ **用户的衣服没入库**。
    根因与 `_looks_like_learn` 完全一样：短捕获词 + 受控品类词 = 撞词。

    所以判据是**受控品类词**（`taxonomy`），而不是"有没有衣服的词"
    —— 后者会把「我想学怎么搭配衣服」也算成衣服。
    """
    return taxonomy.resolve_type(text or "") != "other"


def _looks_like_learn(text: str) -> bool:
    """这句是"想学某个知识主题"吗？（知识库技能，P4）

    ★ 两道闸：命中 `_LEARN` 词，**且不命中** `_LEARN_GUARD`（衣橱语境的词）。
    后者是关键 —— 没有它，「我想学怎么搭配」会被抢到知识库分支去。
    """
    if not any(k in text for k in _LEARN):
        return False
    return not any(g in text for g in _LEARN_GUARD)


def _is_bare_index(text: str) -> bool:
    """整句就是个序号（「1」「第2」「二篇」）—— 用于"从候选笔记里挑一篇"。"""
    return _NOTE_INDEX_RE.match(text or "") is not None


def classify_intent(
    text: str,
    *,
    has_draft: bool = False,
    has_outfits: bool = False,
    has_items: bool = False,
    has_candidates: bool = False,
    has_pending_note: bool = False,
    has_pending_domain: bool = False,
) -> str:
    """判断这条消息想干什么。**只看文本和当前上下文，不碰库。**

    返回：`confirm` | `correct` | `gap` | `buy` | `describe` | `match`
         | `like` | `dislike` | `swap`
         | `learn` | `browse` | `note_pick` | `note_gen` | `note_save` | `note_drop`
         | `domain_open` | `ask`
         | `unknown`

    ⚠️ **顺序即语义**：比如「可以」既可能是"确认草稿"也可能是"这套行" ——
    有草稿时前者优先。所以判断必须按上下文分叉，不能只做关键词集合匹配。
    """
    t = (text or "").strip()
    if not t:
        return "unknown"

    # 1) 待确认草稿优先 —— 否则"可以"会被当成对搭配的反馈
    if has_draft:
        if t.lower() in _CONFIRM:
            return "confirm"
        # 「我有一件…」「我刚买了…」是**新的一件**，不是对当前草稿的修正 → 交给下面
        if not _is_new_item(t):
            if any(t.startswith(p) for p in _CORRECT) or t.startswith("是"):
                return "correct"
            # 能抽出受控属性 → 当修正（"颜色是深蓝"、"材质改成棉"）
            # ⚠️ 这条要在 _DESCRIBE 判断之后，否则"我有一件深灰卫衣"会被当成修正
            fields, _unresolved = extract_fields(t)
            if fields:
                return "correct"
        # 都不像 → 不当确认（交给下面当新消息处理）

    # 1.5) 笔记候选的序号 —— ★ 必须在下面的 outfits 序号块**之前**（审查抓到的静默错）：
    #     两个候选列表**可能同时存在**（`last_outfits` 存 2 小时，
    #     `note_candidates` 存到焦点被清）。用户看着笔记列表回「1」，
    #     旧顺序会判成"选了第 1 套搭配" → **凭空往偏好记忆里写一条好评**。
    if has_candidates and _is_bare_index(t):
        return "note_pick"

    # 2) 序号 + 态度（「2不行」）—— 必须在 _REJECT 之前，否则会被"不行"吃掉
    if has_outfits:
        m = _INDEX_TAIL_RE.match(t)
        if m and m.group(2):
            tail = m.group(2)
            if any(k in tail for k in _REJECT):
                return "dislike"
            if any(k in tail for k in _LIKE) or not tail:
                return "like"

        # 只说序号 = "我选这套"。（这里按 5 判只是"像不像序号"，
        # 真正的越界拒绝在 `_handle_feedback` —— 那里才知道实际有几套。）
        if parse_index(t, 5) is not None:
            return "like"

    if any(k in t for k in _GAP):
        return "gap"
    if any(k in t for k in _BUY):
        return "buy"

    # ---- 知识库（P4）：生成 / 落盘 / 放弃 ----
    # 「总结一下」在**任何状态**下都该触发（用户聊着聊着就想沉淀），所以不挂在
    # `has_pending_note` 上；而「落」「算了」只在**真有东西待落盘**时才认 ——
    # 否则日常对话里的"落"字会被吃掉。
    if any(k in t for k in _NOTE_GEN) and not _looks_like_clothing(t):
        return "note_gen"
    # ★ `_NOTE_SAVE` **刻意不依赖** `has_pending_note`：
    #   否则用户在"正聚焦某篇笔记"的状态下说「落」，会掉进最后的问答分支，
    #   被模型当成一个普通问题瞎答（实测撞到过：「落」→ "模型这次没给出内容"）。
    #   「落 / 写入 / 保存 / 入库」这几个词在穿搭语境里几乎不会出现，
    #   可以无条件判。⚠️ 但**别把这句话当保证** —— 审查抓到的「落肩」就是反例：
    #   所以裸「落」走`_NOTE_SAVE_EXACT_RE`（要求独立成词），其余词走子串匹配。
    if any(k in t for k in _NOTE_SAVE) or _NOTE_SAVE_EXACT_RE.match(t):
        return "note_save"
    # 「算了」太常见（放弃衣物草稿也用它），**只在真有东西待落盘时才判**。
    if has_pending_note and any(k in t for k in _NOTE_DROP):
        return "note_drop"

    # ---- 知识库（P4）：同意建新领域目录 ----
    # ⚠️ **必须在 `_NOTE_GEN` 之后、`learn` 之前**：用户回「建个新领域吧」里
    #   含"新领域"但不含 `_NOTE_GEN` 的词；而「开个会」这种日常话在没有
    #   `pending_domain` 时不会走到这里 —— 门槛就是"刚刚建议过他开主题"。
    if has_pending_domain and any(k in t for k in _DOMAIN_OPEN):
        return "domain_open"

    # ---- 知识库（P4）：学某主题 / 让助手挑 ----
    # ⚠️ 纯序号 → 挑笔记 的判断**已经挪到上面**（outfits 序号块之前），别加回来。
    #
    # ⚠️ 而 `learn` / `browse` **必须排在 `_MATCH` 与 taxonomy 兜底之前**，两个真实撞词：
    #   · 「我想学怎么**搭配**」含 `_MATCH` 的词 → 顺序错了会被判成问穿搭；
    #   · 「我想学**卫衣**怎么挑」含品类词 → 顺序错了会被兜底判成"描述衣服"。
    if _looks_like_learn(t):
        return "learn"
    if any(k in t for k in _BROWSE):
        return "browse"

    # ⚠️ **问搭配要排在肯定反馈之前**：「今天穿什么**好看**的」同时含
    #    `_MATCH`（穿什么）与 `_LIKE`（好看）—— 旧的顺序会判成 like，
    #    于是**白白往偏好记忆里写一条没人给过的反馈**（W4 审查抓到）。
    #    「或有 `last_items`」= 光说「这件」（不带"搭"字）也算在问搭配（规格 §4 分支 7）。
    if any(k in t for k in _MATCH) or (has_items and any(k in t for k in _REFER)):
        return "match"

    if has_outfits and any(k in t for k in _REJECT):
        return "swap"
    if has_outfits and (t in _LIKE_EXACT or any(k in t for k in _LIKE)):
        return "like"
    if _is_new_item(t):
        return "describe"
    # 兜底：一句话里出现了受控品类词 → 当"描述一件衣服"
    if taxonomy.resolve_type(t) != "other":
        return "describe"

    # 知识库问答兜底（规格 §5.2 最后一行；**整条分支原先不存在** ——
    # 2026-10-06 审查抓到，需求 ③「我直接提问」整条落空，只能拿到帮助菜单）。
    # 位置**必须是最后**：像「我想学卫衣怎么挑」也含"怎么"，
    # 得先被上面的 learn / describe 接走。
    if any(k in t for k in _QUESTION_HINTS):
        return "ask"

    return "unknown"


def match_vocab(text: str, vocab: dict[str, str]) -> list[str]:
    """找出 `text` 里出现的词表值（中文标签优先，也认英文 key）。

    用**最长匹配优先**：先试两字以上的标签，避免"黑"把"深蓝"吃掉这种误判。
    （单字标签的确会误判，但它们本来就不该单独出现在受控词表里 —— 这里不做
    "单字也匹配"的妥协，宁可漏认，也不写错值。）
    """
    hits: list[str] = []
    lowered = (text or "").lower()
    # 长标签优先，避免短标签抢先命中
    pairs = sorted(vocab.items(), key=lambda kv: -len(str(kv[1])))
    for key, label in pairs:
        if key in hits:
            continue
        label_s = str(label)
        if len(label_s) >= 2 and label_s in text:
            hits.append(key)
        elif len(label_s) == 1 and label_s in text:
            # 单字中文标签：只在它前后不是"更像词"的语境时命中
            # （例："黑"在"黑色"里已由上面的 ≥2 分支命中）
            hits.append(key)
    return hits


def extract_fields(text: str) -> tuple[dict[str, Any], dict[str, str]]:
    """从一句话里抽受控属性。

    返回 `(识别到的, 没认出来的)` —— **认不出的如实回报**，不猜、不写脏值
    （与打标模块同一条原则）。

    第一版**纯本地词表匹配，不调模型**：高频输入（"我有一件深灰卫衣"）
    用词表就够，而且结果可复现、零成本。
    ⚠️ 已知短板：`深灰`/`藏青` 这类**口语变体**现在认不出（词表里只有"灰色"/"深蓝"），
    会落进 `unresolved` 并如实告诉用户，用户回一句"是灰色"即可修正。
    要真正解决应该在 `taxonomy` 里补别名（属技术债，不在本波范围）。
    """
    t = (text or "").strip()
    found: dict[str, Any] = {}
    unresolved: dict[str, str] = {}

    type_key = taxonomy.resolve_type(t)
    used: list[str] = []          # 已被词表"消费掉"的词 —— 剩下的就是没认出来的
    if type_key != "other":
        found["type"] = type_key

    # ⚠️ **把品类名从文本里剔掉再匹配其它字段。**
    # 否则子串会串味：实测「刚买一条牛仔裤」被抽成 `material: denim`
    # ——因为「牛仔裤」里含「牛仔」，而「牛仔」正好是材质词表里的 denim。
    # 这是**会写坏数据**的误判（材质被莫名填上），必须在源头挡掉。
    rest = t
    if type_key != "other":
        label = taxonomy.label_of(type_key)
        if label and label in rest:
            rest = rest.replace(label, "")
            used.append(str(label))

    for field, vocab in (
        ("colors", taxonomy.COLORS),
        ("style", taxonomy.STYLES),
        ("season", taxonomy.SEASONS),
        ("scene", taxonomy.SCENES),
    ):
        vals = match_vocab(rest, vocab)
        if vals:
            found[field] = vals
            used += [str(vocab[v]) for v in vals]
    for field, vocab in (
        ("material", taxonomy.MATERIALS),
        ("fit", taxonomy.FITS),
        ("formality", taxonomy.FORMALITIES),
        ("pattern", taxonomy.PATTERNS),
    ):
        vals = match_vocab(rest, vocab)
        if vals:
            found[field] = vals[0]
            used.append(str(vocab[vals[0]]))

    # ⚠️ `unresolved` 曾经**恒为空字典** —— `_reply_draft` 里"（这几个没认出来：…）"
    #    那句是**死代码**，用户永远不知道哪些词被丢掉了（W4 审查抓到）。
    #    现在把"没被任何词表消费、也不是口水话"的部分如实回报。
    #    刻意保守：剥掉口水话与标点后，只剩 2~12 个中文字才报，避免刷屏
    #    （**单字残留不报**：例如「深灰」里「灰」已被识别、只剩一个「深」，
    #     报出来只会像乱码。代价是「深灰」被静默存成「灰」——
    #     这是词表缺口语别名，属已知技术债，见 `taxonomy` 补别名那条）。
    leftover = rest
    for lab in used:
        leftover = leftover.replace(lab, "")
    for junk in _FILLER:
        leftover = leftover.replace(junk, "")
    leftover = re.sub(r"[，,。.!！?？、；;：:\s（）()\[\]【】「」“”\"']+", "", leftover)
    if 2 <= len(leftover) <= 12 and re.search(r"[\u4e00-\u9fff]", leftover):
        unresolved["未识别"] = leftover

    return found, unresolved


def describe_draft(fields: dict[str, Any]) -> str:
    """把草稿字段渲染成人类可读的一行（回显给用户确认）。"""
    labels = {
        "type": "品类",
        "colors": "颜色",
        "material": "材质",
        "fit": "版型",
        "style": "风格",
        "season": "季节",
        "scene": "场合",
        "formality": "正式度",
        "pattern": "图案",
    }
    vocab_of = {
        "colors": taxonomy.COLORS,
        "style": taxonomy.STYLES,
        "season": taxonomy.SEASONS,
        "scene": taxonomy.SCENES,
        "material": taxonomy.MATERIALS,
        "fit": taxonomy.FITS,
        "formality": taxonomy.FORMALITIES,
        "pattern": taxonomy.PATTERNS,
    }
    parts: list[str] = []
    for key, label in labels.items():
        if key not in fields:
            continue
        val = fields[key]
        vocab = vocab_of.get(key, {})
        if key == "type":
            # `CLOTHING_TYPES` 的值是 `(中文名, 槽位)` 元组 —— 用 `label_of()` 取名字，
            # 不要自己下标（那会拿到槽位）
            parts.append(taxonomy.label_of(val))
        elif isinstance(val, list):
            parts.append(" / ".join(vocab.get(v, v) for v in val))
        else:
            parts.append(vocab.get(val, val))
    return " · ".join(parts) if parts else "（什么都没认出来）"


# ---------------------------------------------------------------------------
# 状态读写
# ---------------------------------------------------------------------------


def _now() -> datetime:
    """当前时间。

    ⚠️ **必须带时区**：`db.now_iso()` 产出的是 `2026-10-05T19:49:19+08:00`（带 `+08:00`），
    拿一个 naive 的 `datetime.now()` 去减它会抛 `TypeError: can't subtract
    offset-naive and offset-aware datetimes`。
    这**同一个坑**在 `preference.py` 里踩过一次（W5 审查抓到），别在这里再来一遍。
    """
    return datetime.now().astimezone()


def _empty_state() -> dict[str, Any]:
    return {"last_outfits": [], "last_items": [], "pending_draft": None,
            # P4 知识库：候选笔记名 / 正在聊的那篇 / 待落盘的新笔记
            "note_candidates": [], "note_focus": None, "pending_note": None,
            # P4：刚建议过「要不要开新主题」，等用户回「开」。**建目录必须他点头**
            "pending_domain": None,
            "updated_at": db.now_iso()}


def _load_state(peer_id: str) -> dict[str, Any]:
    """读会话状态；不存在/损坏/过期都返回一份干净的（**不抛异常**）。

    ⚠️ `conversation_state` 表里的时间列叫 **`last_message_at`**（不是 `updated_at`）——
    后者只是 `state` JSON 里的字段。两者语义相同（上次活动时间），
    但**列名必须用表里的那个**。
    """
    with db.conn() as c:
        row = c.execute(
            "SELECT state, last_message_at FROM conversation_state "
            "WHERE channel=? AND peer_id=?",
            (CHANNEL, peer_id),
        ).fetchone()
    if row is None:
        return _empty_state()
    try:
        state = json.loads(row["state"] or "{}")
    except (ValueError, TypeError):
        # 库里的东西坏了不该让整条链路崩 —— 当成新会话，重新开始
        return _empty_state()
    if not isinstance(state, dict):
        return _empty_state()
    state.setdefault("last_outfits", [])
    state.setdefault("last_items", [])
    state.setdefault("pending_draft", None)
    # P4 知识库的三个键：**加 key 免迁移**（`state` 是 JSON blob，
    # `schema.py` 一列都不用动）—— 旧行读出来会被这里自动补齐。
    state.setdefault("note_candidates", [])
    state.setdefault("note_focus", None)
    state.setdefault("pending_note", None)
    state.setdefault("pending_domain", None)

    # 过期检查：搭配候选会过期，**单品记忆不过期**（"这件怎么搭"隔天也成立）
    stamp = state.get("updated_at") or row["last_message_at"]
    if _expired(stamp, SESSION_TTL):
        state["last_outfits"] = []
    if state.get("pending_draft"):
        draft_at = state["pending_draft"].get("at")
        if _expired(draft_at, DRAFT_TTL):
            state["pending_draft"] = None
    # 待落盘的笔记用**更长的** TTL：生成一次要跑十几次模型调用，
    # 用户可能看完预览想半天才回「落」（见 NOTE_TTL 的取值理由）。
    if state.get("pending_note"):
        note_at = state["pending_note"].get("at")
        if _expired(note_at, NOTE_TTL):
            state["pending_note"] = None
    # 笔记焦点/候选也要过期（审查补的）：它们不过期的话，**跨天随口一句话**
    # 会被当成"关于那篇笔记的问题"送去调模型 —— 白花一次调用，还答得牛头不对马嘴。
    if state.get("note_focus") and _expired(stamp, SESSION_TTL):
        state["note_focus"] = None
        state["note_candidates"] = []
    return state


def _expired(iso: Optional[str], ttl: timedelta) -> bool:
    """判断某个时间戳是否已超过 `ttl`。

    ⚠️ **时区要双向归一**，缺一半就会崩：
    - `db.now_iso()` 产出**带时区**的（`...+08:00`）
    - 但手工写进库的、或早期版本留下的，可能是**不带时区**的

    任意一边是 naive 而另一边是 aware，相减就抛 `TypeError`。
    这个坑在 `preference.py` 里踩过一次（W5 审查抓到），这里是同一类 ——
    **三次了，本该上收成公共工具**（已记技术债）。
    """
    if not iso:
        return False
    try:
        when = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return False
    now = _now()
    if when.tzinfo is None and now.tzinfo is not None:
        when = when.replace(tzinfo=now.tzinfo)
    elif when.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=when.tzinfo)
    return (now - when) > ttl


def _save_state(peer_id: str, state: dict[str, Any], *, context_token: str) -> None:
    """原子 upsert。

    用 `ON CONFLICT ... DO UPDATE` 而不是"先查再写"：后者在并发下会丢更新。
    （当前 worker 是**单线程**处理消息，理论上不会并发；但这是廉价保险，
    而且将来如果改成多线程处理，不会在这里静默出错。）
    """
    state["updated_at"] = db.now_iso()
    with db.conn() as c:
        c.execute(
            """
            INSERT INTO conversation_state
                (id, channel, peer_id, context_token, state, last_message_at, created_at)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(channel, peer_id) DO UPDATE SET
                context_token   = excluded.context_token,
                state           = excluded.state,
                last_message_at = excluded.last_message_at
            """,
            (
                db.new_id("cs"), CHANNEL, peer_id, context_token,
                json.dumps(state, ensure_ascii=False), db.now_iso(), db.now_iso(),
            ),
        )


# ---------------------------------------------------------------------------
# 各场景的回复
# ---------------------------------------------------------------------------


def _outfit_line(index: int, outfit: dict[str, Any]) -> str:
    """一套搭配渲染成微信里好读的一行。"""
    names = []
    for it in outfit.get("items") or []:
        name = it.get("name") or it.get("type_label") or "?"
        names.append(name)
    head = f"{NUM_EMOJI[index] if index < len(NUM_EMOJI) else str(index + 1)} " + " + ".join(names)
    reason = (outfit.get("reason") or "").strip()
    return f"{head}\n   {reason}" if reason else head


def _signature(outfit: dict[str, Any]) -> frozenset:
    """一套搭配的"身份" = 它由哪些单品组成。用来判断"这套刚才是不是给过了"。"""
    return frozenset(outfit.get("item_ids") or [])


def _outfit_item_sets(user_id: str, outfit_ids: list[str]) -> set[frozenset]:
    """把已经展示过的 `outfit_id` 解成单品集合。查不到的直接跳过（不抛异常）。"""
    out: set[frozenset] = set()
    for oid in outfit_ids or []:
        try:
            row = recommend_svc.get_outfit(user_id, oid)
        except Exception:            # noqa: BLE001 —— 解不出来不该拖垮这一轮
            continue
        if row:
            out.add(_signature(row))
    return out


def _reply_match(
    user_id: str,
    state: dict[str, Any],
    scene: Any = None,
    *,
    avoid: Optional[set[frozenset]] = None,
) -> str:
    """给几套搭配。

    `avoid` = **刚才已经展示过**的那几套（按"由哪些单品组成"来认）。
    规格 §4.1 要求「换一套」时排除它们 —— 否则用户说"换一套"，
    拿回来的却是**一模一样的三套**。
    """
    avoid = avoid or set()
    # 多要一些再过滤：如果 3 套全在排除集里，只按 limit=3 取就会一套都不剩。
    result = recommend_svc.recommend(
        user_id, scene=scene, limit=3 + len(avoid), source="wechat"
    )
    outfits = result.get("outfits") or []
    if avoid:
        fresh = [o for o in outfits if _signature(o) not in avoid]
        # 衣橱小的时候，"没给过的"可能就是没有 → 宁可重复，也不要空手
        outfits = fresh or outfits
    outfits = outfits[:3]
    if not outfits:
        return (
            "没搭出可用的组合 —— 可能是这个季节能穿的单品还不够。\n"
            "回「缺什么」我看看该补哪一类。"
        )
    state["last_outfits"] = [o["id"] for o in outfits]
    state["last_items"] = []

    lines = [f"今天（{result.get('season_label') or '当季'}）挑了 {len(outfits)} 套：", ""]
    for i, outfit in enumerate(outfits):
        lines.append(_outfit_line(i, outfit))
        lines.append("")
    lines.append("回「1」「2」「3」告诉我选哪套，或回「换一套」。")
    if result.get("degraded"):
        lines.append("（模型没接上，这次只用了本地排序，所以没有理由说明）")
    return "\n".join(lines)


def _reply_gap(user_id: str) -> str:
    gap = wardrobe_svc.analyze(user_id)
    combos = gap.get("total_combinations", 0)
    lines = [f"当前（{gap.get('season_label') or '当季'}）能搭出 {combos} 套。"]
    if not gap.get("min_viable"):
        labels = "、".join(gap.get("blocking_labels") or []) or "基础单品"
        lines.append(f"⚠️ 现在一套都搭不出来，缺：{labels}。")
        lines.append("至少要有「一件上装 + 一件下装 + 一双鞋」，或「一件连身 + 一双鞋」。")
    else:
        sugg = gap.get("slot_suggestions") or []
        if sugg:
            top = sugg[0]
            lines.append(
                f"最该补的是「{top['slot_label']}」—— 每多一件能多约 {top['delta']} 套。"
            )
    never = gap.get("never_worn") or []
    if never:
        lines.append(f"另外你有 {len(never)} 件从没穿过。")
    lines.append("")
    lines.append("想让我挑一套的话，回「今天穿什么」。")
    return "\n".join(lines)


def _reply_draft(fields: dict[str, Any], unresolved: dict[str, str], raw_text: str) -> str:
    known = describe_draft(fields)
    lines = [f"我理解成：{known}"]
    if unresolved:
        bad = "、".join(unresolved.values())
        lines.append(f"（这几个没认出来：{bad}）")
    lines.append("")
    lines.append("回「对」就入库；不对的话直接说（例如「颜色是深蓝」）。")
    return "\n".join(lines)


def _reply_help() -> str:
    return (
        "我能做这几件事：\n"
        "· 「今天穿什么」/「这件怎么搭」→ 给你挑几套\n"
        "· 「我有一件深灰卫衣」→ 帮你记进衣橱\n"
        "· 「缺什么」→ 看看衣橱缺哪类\n"
        "· 「1」「2」→ 告诉我你选了哪套\n"
        "· 「我想学 XXX」/「你挑一个」→ 从你的知识库里找笔记陪你过一遍\n\n"
        "（拍照识别还没开，先把衣服用文字说给我就行）"
    )


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def handle(message: InboundMessage) -> Optional[str]:
    """处理一条入站消息，返回要回复的文本（`None` = 不回复）。

    **这是通道层调用的唯一入口**（`ClawBotWorker` 的 handler）。

    异常策略：**已知的业务异常就地转成友好提示**（用户看到"模型没接上"比
    看到一个静默的失败好得多）；不认识的异常**放出去**，由 worker 兜底 +
    记日志 —— 因为那说明有 bug，不该被吞掉。
    """
    text = (message.text or "").strip()
    if not text:
        # 图片/文件等在第二阶段才处理；现在如实说，别装死
        return _reply_help()

    user_id = db.get_default_user_id()
    state = _load_state(message.peer_id)
    draft = state.get("pending_draft")

    intent = classify_intent(
        text,
        has_draft=bool(draft),
        has_outfits=bool(state.get("last_outfits")),
        has_items=bool(state.get("last_items")),
        has_candidates=bool(state.get("note_candidates")),
        has_pending_note=bool(state.get("pending_note")),
        has_pending_domain=bool(state.get("pending_domain")),
    )

    try:
        reply = _dispatch(user_id, message, state, draft, intent, text)
    except LLMError as exc:
        # 模型不可用不该让用户收到一坨技术报错；推荐本身已有本地降级，
        # 走到这里说明是别的 LLM 调用（例如文本抽取）
        state["pending_draft"] = draft
        _save_state(message.peer_id, state, context_token=message.context_token)
        return f"模型没接上，这件事暂时做不了：{exc}"

    if reply is not None:
        _save_state(message.peer_id, state, context_token=message.context_token)
    return reply


def _dispatch(
    user_id: str,
    message: InboundMessage,
    state: dict[str, Any],
    draft: Optional[dict[str, Any]],
    intent: str,
    text: str,
) -> Optional[str]:
    """按意图分派。**`state` 由本函数就地修改**，由 `handle` 统一落盘。"""

    # ---- 待确认草稿 ----
    if draft and intent == "confirm":
        payload = dict(draft.get("fields") or {})
        payload.setdefault("name", draft.get("raw_text") or "")
        try:
            created = item_svc.create(user_id, ItemCreate(**payload))
        except Exception as exc:                      # 入库失败要如实说
            return f"入库没成功：{exc}\n你可以改一下再说，例如「颜色是深蓝」。"
        state["pending_draft"] = None
        state["last_items"] = [created["id"], *(state.get("last_items") or [])][:5]
        return (
            f"已入库：{created.get('name') or created.get('type_label')}\n"
            "现在可以问「这件怎么搭」了。"
        )

    if draft and intent == "correct":
        # 把修正文本重新抽一遍，**合并**进原草稿（不是替换 —— 用户可能只说颜色）
        extra, _ = extract_fields(text)
        merged = {**(draft.get("fields") or {}), **extra}
        if not extra:
            return (
                "这句我没解析出属性。可以说得具体点，例如「颜色是深蓝」「材质是棉」。\n"
                f"当前草稿：{describe_draft(draft.get('fields') or {})}"
            )
        draft["fields"] = merged
        draft["at"] = db.now_iso()
        state["pending_draft"] = draft
        return _reply_draft(merged, draft.get("unresolved") or {}, draft.get("raw_text") or "")

    # 有草稿但消息没命中任何意图 → **保留草稿**，提示怎么说。
    # ⚠️ 这条是实测出来的产品缺陷：用户想说"颜色改成深蓝"但漏了具体值
    # （例如只说"颜色改一下"），原来的实现会把草稿**丢掉** ——
    # 用户会以为那件衣服没了，而其实只是他没说清楚。说不清 ≠ 改主意。
    if draft and intent == "unknown":
        return (
            "这句我没听懂。你可以：\n"
            f"· 直接说属性（例如「颜色是深蓝」「材质是棉」）\n"
            f"· 或者说「对」把它入库\n\n"
            f"当前草稿：{describe_draft(draft.get('fields') or {})}"
        )

    # 有草稿但用户明确在做别的事（描述新衣服/问搭配/查缺口）→ 放弃草稿。
    # ⚠️ **不能静默丢弃** —— 用户会以为那件已经入库了。下面各分支的回复里
    # 会带上 `dropped` 提示。
    dropped = None
    if draft:
        dropped = describe_draft(draft.get("fields") or {})
        state["pending_draft"] = None

    # ---- 知识库（P4）----
    if intent == "learn":
        return _with_dropped(_handle_learn(state, text), dropped)

    if intent == "browse":
        return _with_dropped(_handle_browse(state), dropped)

    if intent == "note_pick":
        return _with_dropped(_handle_note_pick(state, text), dropped)

    if intent == "note_gen":
        return _with_dropped(_handle_note_gen(state, text), dropped)

    if intent == "note_save":
        return _with_dropped(_handle_note_save(state), dropped)

    if intent == "note_drop":
        state["pending_note"] = None
        return _with_dropped("好，不写了。想重新来就说「我想学 XXX」。", dropped)

    if intent == "domain_open":
        return _with_dropped(_handle_domain_open(state), dropped)

    if intent == "ask":
        return _with_dropped(_handle_note_ask(state, text), dropped)

    # ---- 各场景 ----
    if intent == "gap":
        return _with_dropped(_reply_gap(user_id), dropped)

    if intent == "buy":
        return _with_dropped(
            "「这件适合我吗」还没做（计划在下一阶段）。\n"
            "现在可以先把衣服记进来，或问我「今天穿什么」。",
            dropped,
        )

    if intent == "describe":
        fields, unresolved = extract_fields(text)
        if not fields:
            return _with_dropped(
                "我没在这句话里认出品类。试着带上品类名，例如\n"
                "「我有一件深灰宽松卫衣」",
                dropped,
            )
        draft = {"raw_text": text, "fields": fields, "unresolved": unresolved,
                 "at": db.now_iso()}
        state["pending_draft"] = draft
        # ★ 互斥的**反向**也要做（規格 §4 硬約束 1）：開始記衣服時，
        #   待落盤的筆記草稿必須讓位 —— 否則用戶說「落」時系統不知道落哪個。
        _clear_note_draft(state)
        return _with_dropped(_reply_draft(fields, unresolved, text), dropped)

    if intent == "match":
        return _with_dropped(_reply_match(user_id, state), dropped)

    if intent in ("like", "dislike", "swap"):
        return _with_dropped(_handle_feedback(user_id, state, intent, text), dropped)

    # 正在聊某篇笔记时，其它话一律当"关于这篇的问题"。
    # ⚠️ 放在**最后** —— 这样「缺什么」「今天穿什么」这些已知意图仍然优先，
    #    不会被"我正聚焦一篇笔记"抢走。
    if state.get("note_focus"):
        note = _focused_note(state)
        if note is not None:
            return _with_dropped(kb_session.reply_answer(note, text), dropped)
        # 那篇笔记没了（改名/删除）→ 清掉焦点，别卡在一个永远答不了的状态
        state["note_focus"] = None

    return _with_dropped(_reply_help(), dropped)


# ---------------------------------------------------------------------------
# 知识库（P4）的会话处理
# ---------------------------------------------------------------------------


def _handle_learn(state: dict[str, Any], text: str) -> str:
    """「我想学 X」→ 列相关笔记；库里没有 → 建议开新主题（**但不建**）。"""
    reply, candidates, suggested = kb_session.reply_learn(text)
    state["note_candidates"] = candidates
    if not candidates:
        # 没列出候选 → 别留着过期的焦点，否则下一句会被当成"关于旧笔记的问题"
        state["note_focus"] = None
    # ★ 记住**建议过什么主题** —— 用户回「开」时要靠它才知道建哪个目录。
    #   原来根本没记，所以那句「你说『开』我就建」是空头承诺。
    state["pending_domain"] = suggested or None
    return reply


def _handle_domain_open(state: dict[str, Any]) -> str:
    """「开」→ **真的建**这个领域目录（用户已点头）。

    ⚠️ 这是全项目**唯一**会建目录的入口，且门槛是 `pending_domain` 存在
    ——也就是"我们刚刚建议过他开这个主题"。没有点头就不会走到这里。
    """
    topic = state.get("pending_domain") or ""
    state["pending_domain"] = None      # 一次性：用掉就清，避免重复建
    if not topic:
        return "你想开什么主题？先说个名字，比如「我想学量子力学」。"
    return kb_session.create_domain(topic)


def _handle_browse(state: dict[str, Any]) -> str:
    """「你挑一个」→ 列领域 + 每个领域最近的一篇。"""
    reply, candidates = kb_session.reply_browse()
    state["note_candidates"] = candidates
    return reply


def _handle_note_pick(state: dict[str, Any], text: str) -> str:
    """从候选里挑一篇 → 进入"聊这篇"模式。"""
    cat = kb_session.load_catalog()
    if cat is None:
        return kb_session.NOT_CONFIGURED
    note = kb_session.resolve_pick(text, state.get("note_candidates") or [], cat)
    if note is None:
        state["note_candidates"] = []
        return "我没认出你要哪篇。回个序号（1/2/3），或者把笔记名说全一点。"
    state["note_focus"] = note.name
    state["note_candidates"] = []      # 选完了，候选清空
    return kb_session.reply_focus(note)


def _focused_note(state: dict[str, Any]):
    """把 state 里的 `note_focus`（只存名字）解析回笔记对象。找不到返回 `None`。"""
    name = state.get("note_focus")
    if not name:
        return None
    cat = kb_session.load_catalog()
    if cat is None:
        return None
    return kb_session.find_by_name(cat, name)


def _note_topic(text: str) -> str:
    """从「帮我总结一下」这类话里剥出**主题**；剥完只剩口水词就返回空。

    ⚠️ 两个坑（都是实测撞到的）：
    1. 只剥 `_NOTE_GEN` 的词不够 ——「总结一下」会剥出残渣「一下」，
       拿它去库里检索当然找不到东西，然后回复"库里没有跟「一下」相关的笔记"。
       → 再用 `retrieval.normalize_topic` 过一遍（它剥"一下/这个/的"这类词）。
    2. 但**不能拿剥完的长度当判据**：「总结缓存」剥完是「缓存」（2 字），
       那是个正经主题；而「一下」也是 2 字。所以判据是"剥掉口水词后还剩不剩东西"。
    """
    from .knowledge import retrieval as kb_retrieval

    t = text or ""
    for k in _NOTE_GEN:
        t = t.replace(k, " ")
    t = t.strip(" ，,。.：:、!！?？")
    # ★ **返回归一化后的串**（审查补的）：原来只拿它当"还剩不剩东西"的判据，
    # 却 `return t` 返回未归一化的原文 —— 于是「总结一下缓存」传给模型的主题名
    # 是脏串"一下缓存"。
    return kb_retrieval.normalize_topic(t)


def _clear_note_draft(state: dict[str, Any]) -> None:
    """清掉"待落盘笔记"这**一个**草稿（与衣物草稿互斥）。

    ★ **两个草稿强制互斥**（`docs/knowledge-design.md` §4 硬约束 1）：
    衣物草稿（`pending_draft`）与待落盘笔记（`pending_note`）同时存在时，
    用户说「落」系统**不知道该落哪个** —— 这是**静默歧义**，不报错但结果错。
    项目里没有现成的互斥辅助（现有草稿清理是 `_dispatch` 内联的 `dropped`），
    所以这里自己写，并且**两个方向都要调**。

    ⚠️ **刻意不动 `note_focus`**：焦点是"我们在聊哪一篇"，不是草稿 ——
      生成新笔记时还要拿它当素材（我第一版顺手清掉了它，结果焦点丢失，
      属于自己引入的 bug）。`note_candidates` 是"刚列出来的清单"，
      一旦开始生成就没用了，清掉。
    """
    state["pending_note"] = None
    state["note_candidates"] = []


def _handle_note_gen(state: dict[str, Any], text: str) -> str:
    """「总结一下」→ 生成新笔记 + 发预览。

    ⚠️ **只预览、不落盘** —— 用户回「落」才写（需求 5）。
    """
    _clear_note_draft(state)          # 互斥：开新流程前先清掉衣物草稿
    topic = _note_topic(text) or (state.get("note_focus") or "")
    if not topic:
        return (
            "你想让我总结哪一块？\n"
            "先说个主题（例如「总结一下缓存」），或者先「我想学 XXX」挑一篇，聊完再总结。"
        )
    reply, pending = kb_session.generate_and_preview(
        topic=topic, focus=_focused_note(state)
    )
    if pending is None:
        return reply
    state["pending_note"] = pending
    return reply


def _handle_note_ask(state: dict[str, Any], text: str) -> str:
    """「RSI 和 BIAS 怎么区分」这种**直接提问**（需求 ③）。

    背景从哪来：**聚焦的那篇**优先；没有聚焦就拿检索命中的那一篇当背景。
    ⚠️ 检索不到就**如实说答不了** —— 不拿模型的通识去补，用户要的是
    复习他自己写的东西，一篇"看起来很像"的答案不是他要的。
    """
    from .knowledge import retrieval as kb_retrieval

    focus = _focused_note(state)
    if focus is not None:
        return kb_session.reply_answer(focus, text)

    cat = kb_session.load_catalog()
    if cat is None:
        return kb_session.NOT_CONFIGURED
    hits = kb_retrieval.find_related(cat, text, limit=1)
    if not hits:
        topic = kb_retrieval.normalize_topic(text) or text.strip()
        return (
            f"库里没有跟「{topic}」相关的笔记，我答不了 —— "
            f"不想凭印象编一份给你。\n"
            f"你先说「我想学 XXX」挑一篇，我照着那篇答。"
        )
    return kb_session.reply_answer(hits[0].note, text)


def _handle_note_save(state: dict[str, Any]) -> str:
    """「落」→ 真正写进知识库。"""
    pending = state.get("pending_note")
    if not pending:
        return "现在没有待写的东西。先说「我想学 XXX」，聊完再让我总结。"
    reply, written = kb_session.commit_pending(pending)
    if written:
        state["pending_note"] = None
    else:
        # ★ 一篇都没写成 → **保留草稿**（审查抓到的静默丢数据）：
        #   那是十几次模型调用的产物，清了就永久丢失，用户也没法重试。
        reply += "\n（一篇都没写成，我先留着这份草稿 —— 你可以再说一次「落」重试）"
    return reply


def _with_dropped(reply: str, dropped: Optional[str]) -> str:
    """在回复末尾补一句"上一件草稿没入库"，避免用户误以为它在库里。"""
    if not dropped:
        return reply
    return f"{reply}\n\n（注：上一件「{dropped}」还没确认，已经放掉了）"


def _handle_feedback(
    user_id: str,
    state: dict[str, Any],
    intent: str,
    text: str,
) -> str:
    """处理对搭配的反馈 —— **这条最容易静默出错，所以指代规则写死在这里。**

    指代规则（`docs/p3-design.md` §4.1）：

        「2」/「第2套」      → last_outfits[1]
        「这套行」/「不错」  → last_outfits[0]（最推荐那套）
        「1不行」            → last_outfits[0] 记 dislike
        「换一套」/「都不行」→ **不写反馈**，重新推荐
    """
    outfits = state.get("last_outfits") or []

    if intent == "swap":
        # 换一套 = 这套都不合适，但不等于"不喜欢里面的每件"
        # → 不写 dislike 进偏好（那会污染单品偏好），只重新推荐
        # 而且要**排除刚给过的那几套**（规格 §4.1），否则"换一套"换来一模一样的
        avoid = _outfit_item_sets(user_id, outfits)
        state["last_outfits"] = []
        return "好，重新给你挑：\n\n" + _reply_match(user_id, state, avoid=avoid)

    if not outfits:
        return "我不确定你说的是哪套 —— 先回「今天穿什么」让我挑几套吧。"

    idx = parse_index(text, len(outfits))
    if idx is None:
        m = _INDEX_TAIL_RE.match(text)
        if m:
            raw = m.group(1)
            n = int(raw) if raw.isdigit() else _CN_NUM.get(raw)
            if n:
                if 1 <= n <= len(outfits):
                    idx = n - 1
                else:
                    # ⚠️ 用户说「第 5 套」但牌面上只有 3 套。以前这里**兜底记到第 1 套**，
                    #    等于把反馈写给了另一套衣服（W4 审查抓到）。宁可什么都不记。
                    return (
                        f"这次只挑了 {len(outfits)} 套，没有第 {n} 套。\n"
                        "回「今天穿什么」我重新给你挑几套。"
                    )
    if idx is None:
        # 没给序号：默认指最推荐那套（"这套行"的口语习惯）
        idx = 0

    outfit_id = outfits[idx]
    fb_type = "dislike" if intent == "dislike" else "like"
    try:
        result = feedback_svc.submit(user_id, outfit_id=outfit_id, feedback_type=fb_type)
    except ValueError as exc:
        # 典型情形：这套里的单品后来被删/归档了 → 没有可学习的对象。
        # 这是**已知的业务状态**，不是 bug：不该让用户收到一坨报错，也不该静默丢弃。
        return (
            f"这套里的衣服好像已经不在了（{exc}）。\n"
            "回「今天穿什么」我按现在的衣橱重新挑。"
        )

    label = f"第 {idx + 1} 套"
    if result.get("duplicate"):
        return f"{label}已经记过「{'这套行' if fb_type == 'like' else '不行'}」了。"
    updated = result.get("recorded") or 0
    if fb_type == "like":
        tail = "（已经更新了你的偏好）" if updated else ""
        return f"记下了：{label} ✅ {tail}\n想看看别的就说「换一套」。"
    return f"记下了：{label} ❌\n回「换一套」我再挑几套。"
