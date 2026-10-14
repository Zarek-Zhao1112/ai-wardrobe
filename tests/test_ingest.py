"""W1 测试：粘贴录入的解析器 + 品类受控选择。

规格：`docs/设计文档_v0.2.md` §11.3 / §10.6。

fixture 用的是 **Zarek 2026-10-07 给的三条真实淘宝分享文案**（逐字照抄，
含 `【淘宝】`、`?tk=`、`CZ007`、换行位置）—— 不用简化版，
因为"fixture 简化了真实情况 = 一种测试谎言"（P4 踩过这个坑）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import config, taxonomy
from app.services import ingest
from app.services.llm.client import LLMError

# ---- 真实文案（逐字照抄，勿"美化"）----

BLOB_JEANS = (
    "【淘宝】7天无理由退货 https://e.tb.cn/h.jaGM85wRqvc5RFe?tk=hibjTKoH6Go CZ007 "
    '「ALLFIT "2021M"原色黑牛仔裤 不褪色 宽松直筒微喇叭百搭牛仔裤男」\n'
    "点击链接直接打开 或者 淘宝搜索直接打开。"
)
BLOB_BELT = (
    "【淘宝】7天无理由退货 https://e.tb.cn/h.jaGJ9EXCLdwKDm4?tk=0RC8TKow1We CZ057 "
    "「ALLFIT 简约皮带ins风百搭个性裤腰带韩版学生潮流男女同款黑色」\n"
    "点击链接直接打开 或者 淘宝搜索直接打开"
)
BLOB_SWEATS = (
    "【淘宝】7天无理由退货 https://e.tb.cn/h.8BycubSyi283nlW?tk=V4TvTKoDnGj CZ193 "
    "「CatandMouse猫与小鼠25AW重磅400G7A炽芯索罗娜磨毛直筒宽松卫裤」\n"
    "点击链接直接打开 或者 淘宝搜索直接打开"
)
BLOB_ALL = BLOB_JEANS + BLOB_BELT + BLOB_SWEATS


class _FakeClient:
    """假模型。`calls` 用来断言"本地能定就绝不调模型"。"""

    def __init__(self, payload=None, *, raises: Exception | None = None):
        self.payload = payload
        self.raises = raises
        self.calls = 0

    def chat_json(self, messages, *, schema=None, temperature=0.0):  # noqa: ANN001
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return self.payload


# ---------------------------------------------------------------------------
# ① 预筛
# ---------------------------------------------------------------------------

class TestIsShareLike:
    def test_real_blob_is_share(self):
        assert ingest.is_share_like(BLOB_JEANS)

    def test_url_without_quotes_is_share(self):
        """★ 只覆盖 URL 分支。

        真实分享文案里 URL 和「」**都有** —— 所以单砍掉任一支，`test_real_blob_is_share`
        都还会绿（两条判据是互为冗余的）。变异测试（W1）抓出了这个覆盖缺口：
        必须有输入**只有 URL、没有书名号**，才能单独锁住 URL 判据。
        """
        assert ingest.is_share_like("快去看看 https://e.tb.cn/h.abc123?tk=xyz 这个")
        assert ingest.is_share_like("https://item.taobao.com/item.htm?id=123")

    def test_quoted_title_without_url_is_share(self):
        """★ 只覆盖「」分支（对应上一条）。

        `test_long_quoted_title_without_url_is_share` 是它的细版，
        拆成两条是为了每条变异都能被单独抓到。
        """
        assert ingest.is_share_like("「宽松直筒牛仔裤」")

    def test_three_blobs_is_share(self):
        assert ingest.is_share_like(BLOB_ALL)

    @pytest.mark.parametrize(
        "chat",
        [
            "今天穿什么",
            "换一套",
            "这样搭好吗？",          # ★ 有问号但没 URL —— 别判成分享
            "深蓝配什么鞋?",
            "我想学缓存",
            "「对」",                 # 书名号太短，不算商品标题
            "",
            "   ",
        ],
    )
    def test_ordinary_chat_is_not_share(self, chat):
        assert not ingest.is_share_like(chat)

    def test_long_quoted_title_without_url_is_share(self):
        """没有 URL，但「」里是像样的标题 → 也算（网页端只贴标题的场景）。"""
        assert ingest.is_share_like("「ALLFIT 原色黑牛仔裤 宽松直筒」")

    def test_prefilter_routes_blobs_to_ingest_not_ask(self):
        """★ 发现 3 的守卫：`_QUESTION_HINTS` 含 `'?'`，分享链接形如 `?tk=xxx` ——
        当标题里的品类**词表不认识**时，会一路掉到结尾的提问兜底，被判成
        `ask`（知识库提问）：跑去答非所问，还白烧一次模型。

        W2 起，预筛在 `classify_intent` 的**最前面**把它拦成 `ingest`。
        下面两条断言合起来说明：拦住了分享链接，但**没错伤真提问**。
        """
        from app.services import conversation as C

        blob = (
            "【淘宝】7天无理由退货 https://e.tb.cn/h.abc123?tk=xyz CZ999 "
            "「某品牌2026秋冬神秘新品首发」点击链接直接打开"
        )
        assert ingest.is_share_like(blob)              # 预筛认得出
        assert C.classify_intent(blob) == "ingest"     # 不再掉进 ask
        assert C.classify_intent("这个和那个有什么区别？") == "ask"

    def test_w0_alias_makes_the_sweats_blob_resolvable(self):
        """记录一次**行为变更**（2026-10-08）。

        W0 补「卫裤」别名后，这条真实文案在**词表层**就能定品类了。
        在 W2 之前它还会让 `classify_intent` 从 `ask` 变成 `describe`；
        W2 上了预筛之后先判成 `ingest`，那个现象已经观察不到。
        所以这条锁的是**词表层**的事实（`resolve_type`），不是意图分类。
        """
        from app import taxonomy
        from app.services import conversation as C

        assert taxonomy.resolve_type(BLOB_SWEATS) == "sweatpants"
        assert C.classify_intent(BLOB_SWEATS) == "ingest"


# ---------------------------------------------------------------------------
# ② 解析
# ---------------------------------------------------------------------------

class TestParseShare:
    def test_three_real_blobs(self):
        items = ingest.parse_share(BLOB_ALL)
        assert len(items) == 3

        assert items[0].title.startswith("ALLFIT")
        assert items[0].code == "CZ007"
        assert items[0].url.startswith("https://e.tb.cn/h.jaGM85wRqvc5RFe")
        assert items[0].platform == "淘宝"

        assert "皮带" in items[1].title
        assert items[1].code == "CZ057"

        assert items[2].title.startswith("CatandMouse")
        assert items[2].code == "CZ193"

    def test_title_has_no_noise_and_no_newline(self):
        """★ 入库时 `name` 用这个 title —— 它必须是干净的。

        现状 bug：`conversation` 的 confirm 分支
        `payload.setdefault("name", draft["raw_text"])`，
        粘贴场景下 `raw_text` 是**整段**（含 URL / "7天无理由退货" / 分享码）。
        """
        for it in ingest.parse_share(BLOB_ALL):
            assert "http" not in it.title
            assert "7天无理由退货" not in it.title
            assert "点击链接" not in it.title
            assert "\n" not in it.title
            assert not it.title.startswith("【")

    def test_noise_after_last_title_does_not_leak(self):
        items = ingest.parse_share(BLOB_SWEATS)
        assert len(items) == 1
        assert items[0].title.endswith("卫裤")

    def test_plain_title_without_quotes(self):
        items = ingest.parse_share("CatandMouse 磨毛直筒宽松卫裤")
        assert len(items) == 1
        assert "卫裤" in items[0].title

    def test_multiline_plain_titles(self):
        items = ingest.parse_share("纯棉圆领T恤 深灰\n宽松直筒牛仔裤\n小白鞋")
        assert [i.title for i in items] == ["纯棉圆领T恤 深灰", "宽松直筒牛仔裤", "小白鞋"]

    def test_empty_and_whitespace(self):
        assert ingest.parse_share("") == []
        assert ingest.parse_share("   \n  ") == []

    def test_short_quoted_text_is_kept(self):
        """「对」这类短引用不该被当成商品，但 parse_share 本身是宽松的 ——
        微信端靠 `is_share_like` 挡在前面。这里只确认不会崩。"""
        assert isinstance(ingest.parse_share("「对」"), list)


class TestParseThenClassify:
    """解析结果接上 W0 的词表 —— 这是这条链路真正的价值。"""

    @pytest.mark.parametrize(
        ("blob", "want_key", "want_slot"),
        [
            (BLOB_JEANS, "jeans", "bottom"),
            (BLOB_SWEATS, "sweatpants", "bottom"),
            (BLOB_BELT, "belt", "accessory"),
        ],
    )
    def test_real_blobs_resolve_locally(self, blob, want_key, want_slot):
        """★ 三条真实文案**全部本地可定**，一次模型都不用调。

        第 3 条（卫裤）在 W0 补别名之前是 `other` —— 详见设计文档 §3.4 补记。
        """
        (item,) = ingest.parse_share(blob)
        key = taxonomy.resolve_type(item.title)
        assert key == want_key
        assert taxonomy.slot_of(key) == want_slot


class TestNormalizeTitle:
    def test_ignores_spacing_case_punctuation(self):
        a = ingest.normalize_title("ALLFIT 原色黑牛仔裤")
        b = ingest.normalize_title("allfit原色黑牛仔裤  ")
        assert a == b and a

    def test_different_titles_differ(self):
        assert ingest.normalize_title("黑色直筒裤") != ingest.normalize_title("黑色阔腿裤")

    def test_empty(self):
        assert ingest.normalize_title("") == ""


# ---------------------------------------------------------------------------
# ③ 品类：本地优先，模型兜底
# ---------------------------------------------------------------------------

class TestPickTypeLocalFirst:
    def test_local_hit_never_calls_model(self):
        """★ 省钱的核心断言：本地能定就绝不调模型。"""
        fake = _FakeClient(payload={"type": "pants"})
        pick = ingest.pick_type("宽松直筒牛仔裤", client=fake)
        assert pick.key == "jeans"
        assert pick.used_llm is False
        assert fake.calls == 0, "本地命中了却还是调了模型"

    def test_local_hit_via_alias_added_in_w0(self):
        fake = _FakeClient(payload={"type": "pants"})
        pick = ingest.pick_type("磨毛直筒宽松卫裤", client=fake)
        assert pick.key == "sweatpants"
        assert fake.calls == 0


class TestPickTypeModelFallback:
    def test_model_returns_valid_key(self):
        fake = _FakeClient(payload={"type": "pants"})
        pick = ingest.pick_type("某个词表完全没有的怪东西", client=fake)
        assert pick.used_llm is True
        assert pick.key == "pants"

    def test_model_returns_colloquial_word_gets_renormalized(self):
        """★ 发现 1 的直接延续：模型回「卫裤」这种口语词，本地再归一能救回来。

        若没补别名，这里会得到 `other` —— 白调一次还失败。
        所以 §10.6 要求"本地再归一一次"是硬约束，不是装饰。
        """
        fake = _FakeClient(payload={"type": "卫裤"})
        pick = ingest.pick_type("某个词表完全没有的怪东西", client=fake)
        assert pick.key == "sweatpants"
        assert pick.used_llm is True

    def test_model_returns_garbage_stays_other(self):
        fake = _FakeClient(payload={"type": "摩托车"})
        pick = ingest.pick_type("某个词表完全没有的怪东西", client=fake)
        assert pick.key == "other"
        assert pick.used_llm is True

    def test_model_returns_empty(self):
        fake = _FakeClient(payload={})
        pick = ingest.pick_type("某个词表完全没有的怪东西", client=fake)
        assert pick.key == "other"

    def test_model_raises_is_reported_not_swallowed(self):
        """模型挂了要能**如实告知**，不能静默当成"认不出"。"""
        fake = _FakeClient(raises=LLMError("401 unauthorized"))
        pick = ingest.pick_type("某个词表完全没有的怪东西", client=fake)
        assert pick.key == "other"
        assert pick.used_llm is True
        assert "401" in pick.error

    def test_unexpected_exception_also_degrades(self):
        fake = _FakeClient(raises=ValueError("boom"))
        pick = ingest.pick_type("某个词表完全没有的怪东西", client=fake)
        assert pick.key == "other"
        assert "ValueError" in pick.error


class TestPickTypeNoModelConfigured:
    def test_not_ready_returns_other_without_network(self, monkeypatch):
        monkeypatch.setattr(config, "LLM_READY", False)
        pick = ingest.pick_type("某个词表完全没有的怪东西")
        assert pick.key == "other"
        assert pick.used_llm is False

    def test_not_ready_is_reported_not_silent(self, monkeypatch):
        """★ W4 审查抓到：原来这里 `error=""` → 用户只看到"没认出品类"，
        不知道是因为**压根没接模型**。

        规格 §11.3 失败语义表把"没 key / 401 / 超时"并列为**模型不可用**，
        都要求"降级为只用本地词表，**如实告知**"。静默降级正是本项目最怕的。
        """
        monkeypatch.setattr(config, "LLM_READY", False)
        pick = ingest.pick_type("某个词表完全没有的怪东西")
        assert pick.error, "没配模型必须给出原因，不能静默"
        assert "没配置模型" in pick.error


# ---------------------------------------------------------------------------
# ④ fill_type：就地补品类，不动别的
# ---------------------------------------------------------------------------

class TestFillType:
    def test_existing_type_untouched(self):
        """★ 已有 `type` 就**一次模型都不该调** —— 即使用一个词表不认识的标题。

        ⚠️ 标题刻意选"词表不认识"的：若用「牛仔裤」这类本地能直接认出的标题，
        `pick_type` 会本地命中，**看起来通过但其实没测到这条守卫**。
        W1 的变异测试抓出过这个假绿（去掉守卫后测试照样绿）。
        """
        fake = _FakeClient(payload={"type": "pants"})
        fields = {"type": "jeans", "colors": ["black"]}
        pick = ingest.fill_type(fields, "某个词表完全没有的怪东西", client=fake)
        assert fields == {"type": "jeans", "colors": ["black"]}
        assert pick.used_llm is False
        assert fake.calls == 0, "已经有 type 了还是去调了模型"

    def test_other_is_replaced(self):
        fake = _FakeClient(payload={"type": "jacket"})
        fields = {"type": "other", "colors": ["black"]}
        ingest.fill_type(fields, "某个词表完全没有的怪东西", client=fake)
        assert fields["type"] == "jacket"
        assert fields["colors"] == ["black"], "只该动 type，不该碰其它字段"

    def test_missing_key_is_filled(self):
        fake = _FakeClient(payload={"type": "skirt"})
        fields: dict = {}
        ingest.fill_type(fields, "某个词表完全没有的怪东西", client=fake)
        assert fields["type"] == "skirt"


# ---------------------------------------------------------------------------
# ⑤ 分层守卫
# ---------------------------------------------------------------------------

class TestLayering:
    """分层守卫：直接看**语法树**，不看感觉。

    ⚠️ 为什么用 AST 而不是字符串匹配：本模块**确实**出现过
    `from .conversation import extract_fields` —— 但它在**函数体内**，
    是刻意的懒加载（避开模块级循环 import）。字符串匹配会把这种正确写法
    也判成违规（W3 写这条测试时第一次就是这么红的）。
    真正要守的是**模块级**不 import。
    """

    @staticmethod
    def _top_level_imports() -> list[str]:
        import ast

        src = Path(ingest.__file__).read_text(encoding="utf-8")
        names: list[str] = []
        for node in ast.parse(src).body:          # 只看顶层，不看函数体
            if isinstance(node, ast.Import):
                names.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.append(node.module or "")
        return names

    def test_no_module_level_import_of_conversation(self):
        for name in self._top_level_imports():
            assert "conversation" not in name, (
                f"模块级 import 了 {name} —— conversation 会反过来 import 本模块，"
                "这会撞上半初始化模块（ImportError）"
            )

    def test_uses_lazy_import_instead(self):
        """懒加载必须**存在**：不然 `build_candidates()` 拿不到抽取函数。"""
        src = Path(ingest.__file__).read_text(encoding="utf-8")
        assert "from .conversation import extract_fields" in src

    def test_no_protocol_layer_leak(self):
        """`services/` 层不该出现协议层字样（同设计文档 §2 判据）。"""
        src = Path(ingest.__file__).read_text(encoding="utf-8")
        for word in ("httpx", "ilink", "context_token"):
            assert word not in src, f"ingest.py 里出现了协议层字样：{word}"
