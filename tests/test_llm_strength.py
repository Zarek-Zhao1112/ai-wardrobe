"""直连模型的"思考强度"：两家都用高强度，只有能力探测允许关掉。

## 为什么单独锁这一条

2026-11-17 定：本机直连（用户自己的 Key）只有一档高能力模型，文字分析与照片
识别共用它，强度也不分档 —— OpenAI 发 `reasoning_effort=high`，DeepSeek 发
`thinking={"type":"enabled"}` + `reasoning_effort=high`。

两个坑必须钉住：

1. **不能漏强度**：DeepSeek 的 `thinking` 默认就是开，但 OpenAI 不传
   `reasoning_effort` 时用的是模型默认档（实测 gpt-6-luna 只花 44 token 思考）。
   漏了就悄悄降级，没人会发现。
2. **探测必须关掉**：`_probe` 只给 32 个 token，开着思考时额度全被 reasoning
   吃掉、正文空着回来 → "支持结构化输出"被误判成"不支持" → 静默退化成纯文本抽
   JSON。这条比第 1 条更隐蔽。
"""

from __future__ import annotations

from app.services.llm import channel
from app.services.llm.client import (
    MAX_OUTPUT_TOKENS,
    PROBE_OUTPUT_TOKENS,
    STRONG_OUTPUT_TOKENS,
    OpenAICompatClient,
)

OPENAI = channel.PROVIDERS["openai"]
DEEPSEEK = channel.PROVIDERS["deepseek"]


class _Resp:
    status_code = 200


class _FakeHttp:
    """替掉 `_new_client()`，把 `_probe` 真正发出去的 payload 拦下来。"""

    def __init__(self, sink):
        self._sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, *, headers=None, json=None):
        self._sink.update(json)
        return _Resp()


def _direct(provider: str) -> OpenAICompatClient:
    return OpenAICompatClient(
        channel.PROVIDERS[provider],
        "sk-test",
        channel.DIRECT_MODELS[provider],
        device_direct=True,
    )


class TestStrongByDefault:
    def test_openai_direct_asks_for_high_effort(self):
        fields = _direct("openai")._generation_fields(0.2)
        assert fields["reasoning_effort"] == "high", "★ 漏了高强度 → 悄悄退回默认档"
        assert fields["max_completion_tokens"] == STRONG_OUTPUT_TOKENS
        assert "temperature" not in fields, "推理族不接受 temperature"

    def test_deepseek_direct_enables_thinking_at_high_effort(self):
        fields = _direct("deepseek")._generation_fields(0.2)
        assert fields["thinking"] == {"type": "enabled"}, "★ 两家都要高强度"
        assert fields["reasoning_effort"] == "high"
        assert fields["max_tokens"] == STRONG_OUTPUT_TOKENS

    def test_strong_budget_is_larger_than_plain_calls(self):
        """思考与正文共用额度：直连的额度必须比普通调用宽，否则正文会被挤空。"""
        assert STRONG_OUTPUT_TOKENS > MAX_OUTPUT_TOKENS


class TestProbeStaysCheap:
    def _probe_payload(self, provider, monkeypatch):
        client = _direct(provider)
        sink: dict = {}
        monkeypatch.setattr(client, "_new_client", lambda timeout: _FakeHttp(sink))
        assert client._probe({"type": "json_object"}) is True
        return sink

    def test_openai_probe_turns_reasoning_off(self, monkeypatch):
        sink = self._probe_payload("openai", monkeypatch)
        assert sink["reasoning_effort"] == "none", (
            "★ 探测开着思考 → 32 个 token 全被 reasoning 吃掉，正文空着回来，"
            "结构化输出会被误判成不支持"
        )
        assert sink["max_completion_tokens"] == PROBE_OUTPUT_TOKENS

    def test_deepseek_probe_disables_thinking(self, monkeypatch):
        sink = self._probe_payload("deepseek", monkeypatch)
        assert sink["thinking"] == {"type": "disabled"}
        assert "reasoning_effort" not in sink, "关掉思考时不该再带强度"
        assert sink["max_tokens"] == PROBE_OUTPUT_TOKENS


class TestNonDirectUnchanged:
    def test_env_non_reasoning_model_keeps_old_behaviour(self, monkeypatch):
        """`.env` 通道里的非推理族模型不受影响（旧端点不认 reasoning_effort）。"""
        from app import config
        monkeypatch.setattr(config, "LLM_THINKING_DISABLED", False)
        fields = OpenAICompatClient("https://example.test/v1", "k", "deepseek-chat")._generation_fields(0.2)
        assert fields["max_tokens"] == MAX_OUTPUT_TOKENS
        assert "reasoning_effort" not in fields

    def test_env_reasoning_model_also_goes_high(self, monkeypatch):
        """电脑现在跑的 `.env` 通道是 gpt-6-luna，这条也得是高强度。"""
        from app import config
        monkeypatch.setattr(config, "LLM_THINKING_DISABLED", False)
        fields = OpenAICompatClient("https://api.openai.com/v1", "k", "gpt-6-luna")._generation_fields(0.2)
        assert fields["reasoning_effort"] == "high"
        assert fields["max_completion_tokens"] == STRONG_OUTPUT_TOKENS

    def test_env_probe_does_not_ask_for_high_effort(self, monkeypatch):
        """探测在 `.env` 通道同样必须便宜：32 个 token 经不起一次高强度思考。"""
        from app import config
        monkeypatch.setattr(config, "LLM_THINKING_DISABLED", False)
        client = OpenAICompatClient("https://api.openai.com/v1", "k", "gpt-6-luna")
        sink: dict = {}
        monkeypatch.setattr(client, "_new_client", lambda timeout: _FakeHttp(sink))
        assert client._probe({"type": "json_object"}) is True
        assert "reasoning_effort" not in sink
        assert sink["max_completion_tokens"] == PROBE_OUTPUT_TOKENS
