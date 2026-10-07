"""路由层集成测试：走**真实 HTTP 栈**（TestClient），不只调 service。

为什么需要这一层：service 层测试证明不了"端点接对了"——
参数名写错、路径顺序反了、状态码不对，都要靠这层才暴露。

⚠️ 这些测试写进 conftest 的**临时库**，不碰 `data/wardrobe.db`。
⚠️ 每个用到图片的用例用**不同字节**（`png("tag")`）——
`image_hash` 有唯一索引（防重复录入），共用一副字节会互相撞。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


def png(tag: str) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + tag.encode() + b"\x00" * 60


@pytest.fixture(scope="module")
def client():
    # `with` 是必需的 —— 只有进入上下文才执行 lifespan（db.init）
    with TestClient(app) as c:
        yield c


class TestBasics:
    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "healthy"

    def test_taxonomy(self, client):
        assert client.get("/taxonomy").status_code == 200


class TestBulkEndpoint:
    def test_reports_skips_instead_of_rolling_back(self, client):
        items = [
            {"type": "tshirt", "name": f"路由批{i}", "image_hash": f"api_dup_{i}"}
            for i in range(3)
        ]
        items.append({"type": "tshirt", "name": "路由重复", "image_hash": "api_dup_0"})

        r = client.post("/items/bulk", json={"items": items})

        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 4
        assert data["created"] == 3
        assert data["skipped"] == 1
        assert data["outcomes"][3]["status"] == "skipped"
        assert "指纹重复" in data["outcomes"][3]["reason"]
        assert "跳过 1" in data["message"]
        # 只断言"不会变少"：库里可能已有别的测试造的下装和鞋，
        # 那么补上装是**会增加**组合数的。写死相等是错的。
        assert data["combination_delta"]["after"] >= data["combination_delta"]["before"]

    def test_empty_payload_is_rejected_by_validation(self, client):
        assert client.post("/items/bulk", json={"items": []}).status_code == 422


class TestImageEndpoint:
    def test_upload_then_serve_static(self, client):
        item = client.post("/items", json={"type": "tshirt", "name": "路由图测"}).json()["item"]
        payload = png("upload-static")

        r = client.post(
            f"/items/{item['id']}/image",
            files={"file": ("photo.png", payload, "image/png")},
        )

        assert r.status_code == 200
        path = r.json()["image_path"]
        assert path.endswith(".png")
        assert r.json()["item"]["image_path"] == path

        # 静态挂载必须真的能取到 —— 否则前端显示不出来，而接口却是 200
        served = client.get(f"/images/{path}")
        assert served.status_code == 200
        assert served.content == payload

    def test_non_image_is_a_400_not_a_500(self, client):
        item = client.post("/items", json={"type": "tshirt", "name": "传PDF"}).json()["item"]
        r = client.post(
            f"/items/{item['id']}/image",
            files={"file": ("a.pdf", b"%PDF-1.4 nope", "application/pdf")},
        )
        assert r.status_code == 400
        assert "认不出图片格式" in r.json()["detail"]

    def test_unknown_item_is_404(self, client):
        r = client.post(
            "/items/it_不存在/image",
            files={"file": ("a.png", png("unknown-item"), "image/png")},
        )
        assert r.status_code == 404


_wardrobe_ready = False


def _ensure_wardrobe(client) -> None:
    """凑齐三个基础槽位（否则推荐不出东西，会返回空列表）。幂等。

    刻意**不用 class 级 fixture** —— pytest 会把「定义在类里的 fixture」
    标成 deprecated（`Class-scoped fixture defined as instance method`），
    在当前版本只是警告，但 pytest 10 里会直接失效。
    """
    global _wardrobe_ready
    if _wardrobe_ready:
        return
    for _ in range(2):
        client.post("/items", json={"type": "tshirt", "name": "上装"})
    for _ in range(2):
        client.post("/items", json={"type": "jeans", "name": "下装"})
    client.post("/items", json={"type": "sneakers", "name": "鞋"})
    _wardrobe_ready = True


class TestRecommendAndFeedback:
    def test_recommend_without_model(self, client):
        """未配 key + `use_llm=false`：应当**正常返回**搭配，不是报错。"""
        _ensure_wardrobe(client)
        r = client.post("/outfits/recommend", params={"limit": 3, "use_llm": "false"})

        assert r.status_code == 200
        data = r.json()
        assert data["outfits"], "衣橱里已经有基础槽位，应该能推荐出搭配"
        assert data["degraded"] is False
        for outfit in data["outfits"]:
            assert outfit["item_ids"]
            assert outfit["status"] == "proposed"
            assert outfit["reason"] is None

    def test_feedback_updates_memory_and_is_visible_in_preferences(self, client):
        """★ 闭环的端到端证据：反馈 → 记忆 → 能被查出来。"""
        _ensure_wardrobe(client)
        rec = client.post("/outfits/recommend", params={"limit": 1, "use_llm": "false"}).json()
        outfit_id = rec["outfits"][0]["id"]

        r = client.post(
            "/outfits/feedback",
            json={"outfit_id": outfit_id, "feedback_type": "like", "rating": 5},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["recorded"] > 0
        assert body["duplicate"] is False

        prefs = client.get("/preferences").json()
        assert prefs, "反馈之后偏好记忆不该是空的"
        assert all("confidence" in p and "decayed_score" in p for p in prefs)

        contexts = client.get("/preferences/contexts").json()["contexts"]
        assert contexts, "至少该有一个记忆桶"
        assert contexts[0]["evidence"] > 0

    def test_duplicate_feedback_is_idempotent_over_http(self, client):
        _ensure_wardrobe(client)
        listed = client.get("/outfits").json()["outfits"]
        accepted = [o for o in listed if o["status"] == "accepted"]
        assert accepted, "前置测试应当已经产生一套 accepted"

        again = client.post(
            "/outfits/feedback",
            json={"outfit_id": accepted[0]["id"], "feedback_type": "like"},
        )
        assert again.status_code == 200
        assert again.json()["duplicate"] is True
        assert again.json()["recorded"] == 0

    def test_outfit_history_and_detail(self, client):
        _ensure_wardrobe(client)
        listed = client.get("/outfits").json()["outfits"]
        assert listed

        one = client.get(f"/outfits/{listed[0]['id']}")
        assert one.status_code == 200
        assert one.json()["id"] == listed[0]["id"]
        assert one.json()["items"], "详情里应当带出单品明细"

    def test_unknown_outfit_is_404(self, client):
        assert client.get("/outfits/of_根本不存在").status_code == 404

    def test_feedback_on_unknown_outfit_is_400(self, client):
        r = client.post(
            "/outfits/feedback",
            json={"outfit_id": "of_不存在", "feedback_type": "like"},
        )
        assert r.status_code == 400


class TestTaggingEndpoint:
    def test_without_key_is_503_not_500(self, client):
        """未配 key 是**配置问题**，不是代码问题 —— 状态码必须能区分这两者。"""
        item = client.post("/items", json={"type": "tshirt", "name": "待打标"}).json()["item"]
        uploaded = client.post(
            f"/items/{item['id']}/image",
            files={"file": ("p.png", png("tagging-503"), "image/png")},
        )
        assert uploaded.status_code == 200, "前置：图片要传成功，否则测的是另一条分支"

        r = client.post(f"/tagging/{item['id']}")

        assert r.status_code == 503
        assert "模型不可用" in r.json()["detail"]

    def test_item_without_photo_is_400(self, client):
        item = client.post("/items", json={"type": "tshirt", "name": "没图"}).json()["item"]
        r = client.post(f"/tagging/{item['id']}")
        assert r.status_code == 400
        assert "还没有照片" in r.json()["detail"]

    def test_unknown_item_is_404(self, client):
        assert client.post("/tagging/it_不存在").status_code == 404
