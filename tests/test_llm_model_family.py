"""模型族参数契约：推理族（gpt-5/6、o 系列）与其它模型的字段不一样。

背景（2026-11-04 实测 OpenAI gpt-6-luna）：

    max_tokens  → 400 Unsupported parameter ... Use 'max_completion_tokens'
    temperature=0 → 400 Only the default (1) value is supported

如果这两条不按模型族自动切换，换一次供应商就要改一次代码，
"两条路随时切回去"就不成立了。这些测试把这个判断锁死。
"""
from __future__ import annotations

import pytest

from app.services.llm import client as client_mod
from app.services.llm.client import LLMError, OpenAICompatClient


class _Captured(Exception):
    """内部信号：已经抓到 payload，不用真发请求。"""


def _payloads(client, monkeypatch) -> list[dict]:
    """跑一次 `chat_json`，收集它发出的所有 payload（探测 + 正式）。"""
    seen: list[dict] = []

    def fake(self, payload, *, timeout=60.0):
        seen.append(dict(payload))
        raise _Captured()

    monkeypatch.setattr(OpenAICompatClient, "_post", fake)
    try:
        client.chat_json([{"role": "user", "content": "hi"}])
    except (_Captured, LLMError):
        pass
    return seen


class TestReasoningFamily:
    def test_uses_max_completion_tokens_and_drops_temperature(self, monkeypatch):
        seen = _payloads(OpenAICompatClient(model="gpt-6-luna"), monkeypatch)
        assert seen, "应当至少发出一次请求"
        for payload in seen:
            assert "max_tokens" not in payload, "★ 推理族收到 max_tokens 会直接 400"
            assert payload["max_completion_tokens"] == client_mod.MAX_OUTPUT_TOKENS
            assert "temperature" not in payload, "★ 推理族只接受默认温度，传 0 会 400"

    @pytest.mark.parametrize("model", ["gpt-5-mini", "gpt-6-astra", "o3", "o4-mini"])
    def test_prefix_detection_covers_family(self, model):
        assert client_mod._is_reasoning_model(model) is True

    @pytest.mark.parametrize("model", ["deepseek-flash", "gpt-4o-mini", "doubao-pro", "qwen2.5:7b"])
    def test_legacy_models_are_not_treated_as_reasoning(self, model):
        assert client_mod._is_reasoning_model(model) is False


class TestLegacyFamily:
    def test_keeps_max_tokens_and_temperature(self, monkeypatch):
        seen = _payloads(OpenAICompatClient(model="deepseek-flash"), monkeypatch)
        assert seen
        for payload in seen:
            assert payload["max_tokens"] == client_mod.MAX_OUTPUT_TOKENS
            assert "max_completion_tokens" not in payload
            assert payload["temperature"] == 0.0

    def test_probe_schema_is_strict_compatible(self):
        """探测用的空对象 schema 必须符合 strict 规则，否则被 400 误判成"不支持"。

        这里不能用 `_post` 打桩：探测走的是 `_probe()` 里直连 httpx，
        必须用 MockTransport 才能真正截到那一次请求。
        """
        import json

        import httpx

        seen: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

        client = OpenAICompatClient(
            model="gpt-4o-mini", transport=httpx.MockTransport(handler)
        )
        client.capabilities()

        assert seen, "探测应当发出过请求"
        schema = seen[0]["response_format"]["json_schema"]["schema"]
        assert schema["additionalProperties"] is False
        assert schema["required"] == []
        assert seen[0]["max_tokens"] == client_mod.PROBE_OUTPUT_TOKENS


class TestProxy:
    """`LLM_PROXY` 只影响模型调用，不能污染进程里其它请求。"""

    def test_reads_value(self, monkeypatch):
        monkeypatch.setenv("LLM_PROXY", "http://127.0.0.1:7897")
        assert client_mod._proxy_url() == "http://127.0.0.1:7897"

    def test_blank_means_none(self, monkeypatch):
        monkeypatch.setenv("LLM_PROXY", "   ")
        assert client_mod._proxy_url() is None

    def test_unset_means_none(self, monkeypatch):
        monkeypatch.delenv("LLM_PROXY", raising=False)
        assert client_mod._proxy_url() is None

    def test_injected_transport_wins_over_proxy(self, monkeypatch):
        """测试注入 MockTransport 时不能再传 proxy，否则 httpx 直接报错。"""
        import httpx

        monkeypatch.setenv("LLM_PROXY", "http://127.0.0.1:7897")
        client = OpenAICompatClient(model="gpt-4o-mini", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
        with client._new_client(5.0) as http:
            assert http is not None
