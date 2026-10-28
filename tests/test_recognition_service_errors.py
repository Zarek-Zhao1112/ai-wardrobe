"""§18：安全服务错误、402立即停止探测、不污染能力缓存；全部使用假HTTP。"""
import httpx
import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.services import tagging
from app.services.llm.client import Capabilities, LLMError, OpenAICompatClient


def model(handler):
    return OpenAICompatClient(base_url="https://example.invalid/v1", api_key="test-key", model="fixture", transport=httpx.MockTransport(handler))


def test_balance_probe_stops_first_request_without_poisoning_cache():
    calls = []
    balance = [False]

    def handler(request):
        calls.append(request)
        if not balance[0]:
            return httpx.Response(402, json={"error": {"message": "private upstream account info"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"type":"tshirt"}'}}]})

    client = model(handler)
    with pytest.raises(LLMError) as error:
        client.chat_json([{"role": "user", "content": "fixture"}])
    assert error.value.code == "insufficient_balance"
    assert error.value.retryable is False
    assert len(calls) == 1
    assert client._caps is None
    assert "private" not in str(error.value)
    balance[0] = True
    assert client.chat_json([{"role": "user", "content": "fixture"}]) == {"type": "tshirt"}
    assert len(calls) == 3  # 恢复后重新探测一次，再调用一次。
    assert client._caps.supports_json_schema


@pytest.mark.parametrize(("status", "code"), [(402, "insufficient_balance"), (401, "model_auth"), (403, "model_auth"), (429, "model_rate_limit"), (503, "model_unavailable")])
def test_post_error_is_safe_and_machine_readable(status, code):
    client = model(lambda request: httpx.Response(status, text="private provider response"))
    client._caps = Capabilities(model="fixture")
    with pytest.raises(LLMError) as error:
        client.chat_json([{"role": "user", "content": "fixture"}])
    assert error.value.code == code
    assert error.value.detail()["code"] == code
    assert "private" not in str(error.value)


@pytest.mark.parametrize(("exception", "code"), [(httpx.ReadTimeout, "model_timeout"), (httpx.ConnectError, "model_network")])
def test_network_and_timeout_are_different(exception, code):
    def handler(request):
        raise exception("private endpoint", request=request)
    client = model(handler)
    client._caps = Capabilities(model="fixture")
    with pytest.raises(LLMError) as error:
        client.chat_json([{"role": "user", "content": "fixture"}])
    assert error.value.code == code
    assert "private" not in str(error.value)


def test_preview_returns_service_error_without_creating_item(monkeypatch):
    monkeypatch.setattr(config, "VISION_READY", True)
    def unavailable(*args, **kwargs):
        raise LLMError("AI识别服务余额或额度不足", code="insufficient_balance", retryable=False)
    monkeypatch.setattr(tagging, "tag_image", unavailable)
    with TestClient(app) as client:
        before = client.get("/health").json()["item_count"]
        response = client.post("/tagging/preview", files={"file": ("fixture.jpg", b"\xff\xd8\xfffixture", "image/jpeg")})
        assert response.status_code == 503
        assert response.json()["detail"] == {"code": "insufficient_balance", "message": "AI识别服务余额或额度不足", "retryable": False}
        assert client.get("/health").json()["item_count"] == before


def test_legacy_errors_keep_string_detail():
    assert LLMError("boom").detail() == "模型不可用：boom"
