"""W3 接口测试：`POST /items/parse`（粘贴录入的网页端入口）。

走**真实 HTTP 栈**（TestClient）—— service 层测试证明不了"端点接对了"，
尤其这条：`/parse` 必须声明在 `/{item_id}` **之前**，否则会被当成品单 id。

⚠️ 规格 `docs/设计文档_v0.2.md` §11.3。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

BLOB_JEANS = (
    "【淘宝】7天无理由退货 https://e.tb.cn/h.jaGM85wRqvc5RFe?tk=hibjTKoH6Go CZ007 "
    '「ALLFIT "2021M"原色黑牛仔裤 不褪色 宽松直筒微喇叭百搭牛仔裤男」\n'
    "点击链接直接打开 或者 淘宝搜索直接打开。"
)
BLOB_SWEATS = (
    "【淘宝】7天无理由退货 https://e.tb.cn/h.8BycubSyi283nlW?tk=V4TvTKoDnGj CZ193 "
    "「CatandMouse猫与小鼠25AW重磅400G7A炽芯索罗娜磨毛直筒宽松卫裤」\n"
    "点击链接直接打开 或者 淘宝搜索直接打开"
)
BLOB_BELT = (
    "【淘宝】7天无理由退货 https://e.tb.cn/h.jaGJ9EXCLdwKDm4?tk=0RC8TKow1We CZ057 "
    "「ALLFIT 简约皮带ins风百搭个性裤腰带韩版学生潮流男女同款黑色」\n"
    "点击链接直接打开 或者 淘宝搜索直接打开"
)
BLOB_UNKNOWN = (
    "【淘宝】7天无理由退货 https://e.tb.cn/h.zzz999?tk=qqq CZ999 "
    "「某品牌2026秋冬神秘新品首发」点击链接直接打开"
)

SWEATS_TITLE = "CatandMouse猫与小鼠25AW重磅400G7A炽芯索罗娜磨毛直筒宽松卫裤"


@pytest.fixture(scope="module")
def client():
    # `with` 是必需的 —— 只有进入上下文才执行 lifespan（db.init）
    with TestClient(app) as c:
        yield c


def _parse(client, text: str) -> dict:
    r = client.post("/items/parse", json={"text": text})
    assert r.status_code == 200, r.text
    return r.json()


def _count(client) -> int:
    return client.get("/items").json()["total"]


class TestRouteWiring:
    def test_parse_is_not_shadowed_by_item_id(self, client):
        """★ `/parse` 若声明在 `/{item_id}` 之后，会被当成 id=parse 的单品。"""
        r = client.post("/items/parse", json={"text": BLOB_JEANS})
        assert r.status_code == 200, f"被通配路径吃掉了：{r.status_code} {r.text[:200]}"

    def test_empty_text_is_422(self, client):
        assert client.post("/items/parse", json={"text": ""}).status_code == 422


class TestParseThreeRealBlobs:
    def test_all_three_parsed(self, client):
        data = _parse(client, BLOB_JEANS + BLOB_BELT + BLOB_SWEATS)
        assert data["total"] == 3
        assert [i["code"] for i in data["items"]] == ["CZ007", "CZ057", "CZ193"]

    def test_types_resolved_locally_without_model(self, client):
        """三条真实文案**全部本地可定**，一次模型都不该调。"""
        data = _parse(client, BLOB_JEANS + BLOB_BELT + BLOB_SWEATS)
        assert [i["type_key"] for i in data["items"]] == ["jeans", "belt", "sweatpants"]
        assert all(i["used_llm"] is False for i in data["items"])
        assert all(i["needs_type"] is False for i in data["items"])

    def test_type_labels_present_for_frontend(self, client):
        data = _parse(client, BLOB_SWEATS)
        (item,) = data["items"]
        assert item["type_label"] == "运动裤"

    def test_name_is_clean_title(self, client):
        """★ 前端拿 `fields.name` 当单品名提交 —— 必须是干净标题。"""
        data = _parse(client, BLOB_SWEATS)
        name = data["items"][0]["fields"]["name"]
        assert name == SWEATS_TITLE
        for bad in ("http", "【淘宝】", "7天无理由退货", "CZ193"):
            assert bad not in name

    def test_extra_attributes_are_extracted(self, client):
        data = _parse(client, BLOB_JEANS)
        fields = data["items"][0]["fields"]
        assert fields["type"] == "jeans"
        assert fields["colors"] == ["black"]
        # ★ 2026-10-20 起是 `straight`（原来是 `loose`）。
        #   原因：`FITS` 里的 `straight` 中文标签从「常规」改成了「直筒」——
        #   原标题 "…直筒宽松卫裤" 里「直筒」原先**认不出**（词表没有这个 label），
        #   只剩「宽松」命中；现在两者都能识别，且「直筒」描述得更具体。
        #   「常规」这个旧标签本身是错的（那是 `regular` 的意思）。
        assert fields["fit"] == "straight"


class TestParseDoesNotWrite:
    def test_parsing_creates_no_item(self, client):
        """★ 无状态设计：解析**不落库**，待确认清单由前端持有。"""
        before = _count(client)
        _parse(client, BLOB_JEANS + BLOB_SWEATS)
        assert _count(client) == before


class TestNeedsType:
    def test_unknown_category_is_flagged(self, client):
        data = _parse(client, BLOB_UNKNOWN)
        (item,) = data["items"]
        assert item["needs_type"] is True
        assert item["type_key"] == "other"

    def test_unknown_category_still_returns_title(self, client):
        """认不出品类也要把标题给出来 —— 前端要显示给用户看。"""
        data = _parse(client, BLOB_UNKNOWN)
        assert "神秘新品" in data["items"][0]["title"]


class TestEmptyResult:
    @pytest.mark.parametrize("text", ["？？？", "……", "——", "   "])
    def test_pure_symbols_give_no_item(self, client, text):
        """纯符号不可能是商品名 —— 兜底路径的最低要求是"含汉字或字母"。"""
        r = client.post("/items/parse", json={"text": text})
        if r.status_code == 422:      # 纯空白被 Pydantic 挡了，也算合理
            return
        data = r.json()
        assert data["total"] == 0
        assert data["message"]

    def test_ordinary_chat_is_accepted_by_the_loose_fallback(self, client):
        """⚠️ 记录一个**刻意保留的宽松行为**，别把它当 bug 修掉。

        没有 URL、没有书名号时，「今天穿什么」和「宽松直筒牛仔裤」在文本上
        本质分不开。靠启发式去猜"像不像商品名"只会**误伤真标题**（PRD 选项 A
        明确要支持"只粘标题"）。所以兜底选择放它过去，改由**前端让用户看得见**
        兜住：标题会原样显示，且 `needs_type=True` 逼用户选品类才能入库。

        微信端不受影响 —— 那边由 `is_share_like` 严格预筛，普通聊天进不来。
        """
        data = _parse(client, "今天穿什么")
        assert data["total"] == 1
        assert data["items"][0]["needs_type"] is True


class TestDuplicateHint:
    def test_hint_when_same_title_already_exists(self, client):
        """★ 疑似重复**只提示、不阻止**（§11.3）—— 用户确实可能买了两件一样的。"""
        first = _parse(client, BLOB_SWEATS)
        assert first["items"][0]["duplicate_hint"] is None

        # 真正入一件同名的
        created = client.post(
            "/items", json={"type": "sweatpants", "name": SWEATS_TITLE}
        )
        assert created.status_code < 300, created.text

        again = _parse(client, BLOB_SWEATS)
        assert again["items"][0]["duplicate_hint"]

    def test_hint_does_not_block_submission(self, client):
        """提示归提示，`/items/bulk` 照样收 —— 语义是"你可能已录过"，不是"不许录"。"""
        data = _parse(client, BLOB_SWEATS)
        item = data["items"][0]
        if item["duplicate_hint"]:
            payload = {**item["fields"], "type": item["type_key"]}
            r = client.post("/items/bulk", json={"items": [payload]})
            assert r.status_code == 200
            # 名字不同就不会撞唯一索引（唯一索引只管 image_hash，而这里没有图）
            assert r.json()["failed"] == 0


class TestBulkRoundTrip:
    def test_parse_then_bulk_then_bulk_again(self, client):
        """端到端：解析 → 提交 → **再提交一次**。

        ⚠️ 第二次**不会**被"跳过" —— 微信端和网页端都没有图片，
        `image_hash` 是 NULL，而唯一索引是**部分索引**
        （`WHERE image_hash IS NOT NULL`）。所以重复提交会真的建两条。
        这正是"必须有本地查重提示"的原因（审查发现 2）。
        """
        data = _parse(client, BLOB_BELT)
        item = data["items"][0]
        payload = {**item["fields"], "type": item["type_key"]}

        r1 = client.post("/items/bulk", json={"items": [payload]})
        assert r1.json()["created"] == 1
        assert r1.json()["skipped"] == 0

        r2 = client.post("/items/bulk", json={"items": [payload]})
        assert r2.json()["created"] == 1, "部分索引不覆盖 NULL image_hash → 确实会建第二条"
        assert r2.json()["skipped"] == 0


#: 用户实际粘过的识货商品链接（2026-10-20）。
#: 它**不含商品标题**，只有 goodsId/skuId —— 而本项目**刻意不抓网页**，
#: 所以必然识别不出来。这条样本是用来锁"识别不出来时提示得够清楚"的。
SHIHUO_URL = (
    "https://m.shihuo.cn/app/assets/h5app/ShihuoDetail.html"
    "?goodsId=7546254&goodsMode=4&kl=%C2%A5mdEBbbqgGX%C2%A5"
    "&platform=android&skuId=1910343927&styleId=577421175"
)


class TestUnparsedFeedback:
    """识别不出来时，**提示必须说清为什么、下一步做什么**。

    ⚠️ 这是用户 2026-10-20 反馈的真问题：粘了个商品链接，只看到
    "没读出商品名"，不知道是程序坏了还是自己用错了 —— 于是反复试。

    提示的设计目标不是"礼貌"，是**让用户知道该改什么**：
    - 只粘链接 → 必须讲明"我不打开网页"（否则他会以为这是 bug）
    - 各类情况都要给一个能照抄的例子
    """

    def test_bare_url_explains_it_does_not_open_webpages(self, client):
        """只粘链接：必须说明**不抓网页**这个设计决定，而不是含糊说"没读出"。"""
        data = _parse(client, SHIHUO_URL)

        assert data["total"] == 0
        assert data["items"] == []
        msg = data["message"]
        assert "链接" in msg, f"要承认这是链接：{msg!r}"
        assert "不会去打开" in msg or "不打开" in msg, f"要说明不抓网页：{msg!r}"
        assert "标题" in msg, f"要告诉用户粘标题：{msg!r}"
        assert "例如" in msg, f"要给可照抄的例子：{msg!r}"

    def test_link_plus_a_few_words_still_becomes_a_candidate(self, client):
        """⚠️ 实测的真实契约：**链接 + 任何文字**都会变成候选（title = 那点文字）。

        我原本以为"没商品名 → 零候选"，实测是 1 条（`"看看这个"` 被当标题）。
        这个设计**是对的**，不是缺陷：
        - 宁可把候选摆出来让用户自己判断，也不要静默丢掉他粘的东西
        - 品类认不出 → `needs_type=True` → **复选框默认不勾** +
          必须选一个品类才能入库（`doSubmit` 里卡住）→ 不会误入库

        所以这里锁的是"**不会静默入库**"，而不是"零候选"。
        """
        data = _parse(client, "看看这个 " + SHIHUO_URL)

        assert data["total"] == 1, "链接 + 文字会成为一个候选"
        row = data["items"][0]
        assert row["title"] == "看看这个", f"把链接外的文字当标题：{row['title']!r}"
        assert row["needs_type"] is True, "认不出品类 → 必须让用户选，不能硬猜"
        assert row["url"], "链接要保留下来"

    def test_nonsense_text_never_lands_in_wardrobe_silently(self, client):
        """"啊啊啊啊"这种输入同样会成候选，但**必须卡在"选品类"这一步**。

        这是防"静默入库"的最后一道：前端 `rowHtml` 对 `needs_type` 的行
        **默认不勾选**，且 `doSubmit` 会对未选品类的行中止提交。
        """
        data = _parse(client, "啊啊啊啊")

        assert data["total"] == 1
        row = data["items"][0]
        assert row["needs_type"] is True, "认不出品类必须标出来"
        assert row["type_key"] == "other"

    def test_url_with_a_real_title_parses(self, client):
        """★ 用户真正该怎么做：把标题一起粘进来 → 就能识别。

        这条是上一条提示的**闭环证明**：提示让他"补标题"，补了确实有用。
        """
        data = _parse(client, "特步冰感综训系列防晒衣 " + SHIHUO_URL)
        assert data["total"] == 1, f"补了标题还识别不出：{data}"
        assert data["items"][0]["title"], "标题不该为空"
        assert "防晒" in data["items"][0]["title"] or "特步" in data["items"][0]["title"]


class TestLengthExtraction:
    """粘贴解析要能认出裤长，但**绝不能给非裤装**写。

    ⚠️ 这个坑很隐蔽：`resolve_length` 只看词面，「九分袖衬衫」也含「九分」。
    所以解析时必须**先定品类、再决定要不要认长度**。
    """

    def test_trousers_get_length(self, client):
        data = _parse(client, "「九分运动裤 男 速干 篮球」")
        fields = data["items"][0]["fields"]
        assert fields["type"] == "sweatpants"
        assert fields["length"] == "nine_tenth"

    def test_upper_body_must_not_get_length(self, client):
        """★「九分袖衬衫」不能被写上裤长（Bug 猎手 2026-10-20 实测的静默错）。"""
        data = _parse(client, "「九分袖衬衫 男 商务 长袖」")
        fields = data["items"][0]["fields"]
        assert fields["type"] == "shirt", f"前置：应认成衬衫，实际 {fields['type']}"
        assert "length" not in fields, f"衬衫被写了裤长：{fields.get('length')}"

    def test_long_coat_must_not_get_length(self, client):
        """「长款风衣」同理 —— 「长」不是裤长。"""
        data = _parse(client, "「长款风衣 女 春秋 收腰」")
        fields = data["items"][0]["fields"]
        assert "length" not in fields, f"风衣被写了裤长：{fields.get('length')}"
