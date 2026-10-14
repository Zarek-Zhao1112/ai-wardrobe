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
        assert fields["fit"] == "loose"


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
