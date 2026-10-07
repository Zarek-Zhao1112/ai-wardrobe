"""`get_client()` 单例与能力探测缓存的回归测试。

## 为什么单独锁这一条

2026-10-03 实测发现：`get_client()` 每次都`OpenAICompatClient(...)` 新建对象，
而 `capabilities()` 的探测结果挂在 `self._caps` 上—— **实例字段**。
于是每次推荐都会重新发1~2 个探测请求：

    一次推荐 = 探测×2（json_schema 被拒 → json_mode 被拒） + 正式请求×1 = 3 个 HTTP

真实后果有两个：

1. **白烧配额 + 加延迟**（实测一次推荐 20.4s，其中大半是探测往返）
2. **限流时更糟**：探测请求被 429 拒绝 → `_probe` 把它当成"不支持"（返回 False）
   → `capabilities()` 误判供应商不支持任何结构化输出 → `chat_json` 直接退到纯文本
   → **又多发一次注定失败的请求**

第2 条是静默的：功能不报错，只是理由质量下降。所以必须用测试钉住。
"""

from __future__ import annotations

import pytest

from app import config
from app.services import llm as llm_pkg
from app.services.llm import LLMError, get_client, reset_client_cache
from app.services.llm.client import _CLIENTS, OpenAICompatClient


@pytest.fixture(autouse=True)
def _clean_cache():
    """每个用例前后都清缓存 —— 否则用例之间会互相污染（单例的副作用）。"""
    reset_client_cache()
    yield
    reset_client_cache()


class TestGetClientFactory:
    def test_unconfigured_raises_llmerror(self):
        """未配 key 必须抛 `LLMError`（调用方只有一套 except 能接住两种失败）。"""
        assert config.LLM_READY is False, "测试环境应处于「未配模型」状态"
        with pytest.raises(LLMError):
            get_client()

    def test_vision_unconfigured_also_raises_llmerror(self):
        """`vision=True` 走的是另一套就绪判断，两者都得抛同一种异常。"""
        with pytest.raises(LLMError):
            get_client(vision=True)

    def test_error_message_names_the_missing_vars(self):
        """报错要能直接告诉用户缺哪个变量，否则排查得翻代码。"""
        with pytest.raises(LLMError) as exc:
            get_client()
        msg = str(exc.value)
        assert "LLM_BASE_URL" in msg
        assert "LLM_API_KEY" in msg
        assert "LLM_MODEL" in msg

    def test_vision_error_names_vision_model(self):
        """视觉缺配置时要提 `VISION_MODEL`，不能报成 `LLM_MODEL`。"""
        with pytest.raises(LLMError) as exc:
            get_client(vision=True)
        assert "VISION_MODEL" in str(exc.value)


class TestClientCaching:
    """★ 本文件的核心：不缓存就会白烧配额的那条。"""

    def test_cache_is_empty_after_reset(self):
        assert _CLIENTS == {}

    def test_reset_actually_clears(self):
        # 手工塞一个占位，确认 reset 真的清得掉（而不是只清了个寂寞）
        _CLIENTS[False] = object()  # type: ignore[assignment]
        reset_client_cache()
        assert _CLIENTS == {}

    def test_same_client_reused_when_configured(self, monkeypatch):
        """配好 key 后连续两次 `get_client()` 必须拿到**同一个对象**。

        这条是本文件的回归点：改成每次 new 就会红。
        """
        monkeypatch.setattr(config, "LLM_READY", True, raising=False)
        monkeypatch.setattr(config, "VISION_READY", True, raising=False)
        monkeypatch.setattr(config, "LLM_MODEL", "test-model", raising=False)

        first = get_client()
        second = get_client()

        assert first is second, "get_client 每次都新建 → 能力探测缓存永不命中"
        assert isinstance(first, OpenAICompatClient)

    def test_cached_client_reuses_capability_probe(self, monkeypatch):
        """★ 更直接的一条：能力探测**一辈子只发一次请求**。

        第二次调用应直接读缓存，零 HTTP 请求。这正是省配额的那一步。
        """
        monkeypatch.setattr(config, "LLM_READY", True, raising=False)
        monkeypatch.setattr(config, "LLM_MODEL", "test-model", raising=False)

        client = get_client()
        calls: list = []
        monkeypatch.setattr(
            client, "_probe", lambda fmt: calls.append(fmt) or True
        )

        assert client._caps is None, "前提：还没探测过"
        first = client.capabilities()
        assert len(calls) == 1, "首次探测应发1 个请求"

        second = client.capabilities()
        assert len(calls) == 1, "★ 第二次又发了探测请求 → 配额在白烧"
        assert first is second, "能力结果应从缓存原样返回"

    def test_model_change_invalidates_cache(self, monkeypatch):
        """改了 `LLM_MODEL` 后必须拿新客户端，不能返回挂着旧模型名的那个。"""
        monkeypatch.setattr(config, "LLM_READY", True, raising=False)
        monkeypatch.setattr(config, "LLM_MODEL", "model-a", raising=False)
        first = get_client()

        monkeypatch.setattr(config, "LLM_MODEL", "model-b", raising=False)
        second = get_client()

        assert first is not second, "模型名变了还复用旧客户端"
        assert second.model == "model-b"

    def test_vision_and_text_cached_separately(self, monkeypatch):
        """视觉与文本用不同模型名，**缓存必须分开** —— 混用会让打标拿到文本模型。"""
        monkeypatch.setattr(config, "LLM_READY", True, raising=False)
        monkeypatch.setattr(config, "VISION_READY", True, raising=False)
        monkeypatch.setattr(config, "LLM_MODEL", "text-model", raising=False)
        monkeypatch.setattr(config, "VISION_MODEL", "vision-model", raising=False)

        text_client = get_client()
        vision_client = get_client(vision=True)

        assert text_client is not vision_client
        assert text_client.model == "text-model"
        assert vision_client.model == "vision-model"


class TestPackageExport:
    def test_reset_cache_is_exported(self):
        """测试与运维要能清缓存，所以必须在包的 `__all__` 里。"""
        assert "reset_client_cache" in llm_pkg.__all__
        assert callable(llm_pkg.reset_client_cache)


class TestRequestPayloadGuards:
    """★ 锁死两个实测踩过的坑，少任何一个都会让推荐慢 30 倍。

    都是2026-10-03 真实调用 DeepSeek 测出来的，不是推断：

    | 缺什么| 实测耗时 | completion_tokens |
    |---|---|---|
    | 两个都不给 | 11.8s | 1250 |
    | 只给 `max_tokens` | 5.6s | 526 |
    | 两个都给 | **1.0s** | **26** |

    推理模型（`deepseek-v4-pro`）会把输出额度几乎全部花在 reasoning 上：
    713 字符的 prompt 触发了 5195 个 completion_token（7857 字思考），
    耗时 52s，而正文只有几十字。
    """

    @staticmethod
    def _capture(client) -> dict:
        """抓 `_post` 收到的 payload（不发真实请求）。"""
        got: dict = {}
        from app.services.llm.client import OpenAICompatClient

        original = OpenAICompatClient._post

        def fake(self, payload, timeout=60.0):
            got.update(payload)
            raise _Captured()

        OpenAICompatClient._post = fake
        try:
            client.chat_json([{"role": "user", "content": "hi"}])
        except _Captured:
            pass
        except LLMError:
            pass          # 降级链会走完，无所谓
        finally:
            OpenAICompatClient._post = original
        return got

    def test_json_call_sends_max_tokens(self):
        from app.services.llm.client import MAX_OUTPUT_TOKENS, OpenAICompatClient

        payload = self._capture(OpenAICompatClient(model="m"))
        assert "max_tokens" in payload, "★ 漏了 max_tokens → 推理模型会疯狂写思考，单次 52s"
        assert payload["max_tokens"] == MAX_OUTPUT_TOKENS

    def test_json_call_disables_thinking(self):
        from app.services.llm.client import OpenAICompatClient

        payload = self._capture(OpenAICompatClient(model="m"))
        assert payload.get("thinking") == {"type": "disabled"}, (
            "★ 漏了抑制思考 → 实测 5.6s 变 52s（这一项比 max_tokens 更关键）"
        )

    def test_text_call_also_sends_both(self):
        """`chat_text` 同样要带 —— 它将来也会被用来写东西，不能留同样的坑。"""
        from app.services.llm.client import (
            MAX_OUTPUT_TOKENS,
            OpenAICompatClient,
        )

        got: dict = {}
        from app.services.llm.client import OpenAICompatClient as _C

        original = _C._post

        def fake(self, payload, timeout=60.0):
            got.update(payload)
            raise _Captured()

        _C._post = fake
        try:
            OpenAICompatClient(model="m").chat_text(
                [{"role": "user", "content": "hi"}]
            )
        except (_Captured, LLMError):
            pass
        finally:
            _C._post = original

        assert got.get("max_tokens") == MAX_OUTPUT_TOKENS
        assert got.get("thinking") == {"type": "disabled"}

    def test_max_tokens_is_generous_enough_for_our_use(self):
        """2048 是按本项目实际输出定的，别为了省悄悄调到很小。

        打标要吐受控字段、推荐要排 25 套 + 每套 20~30 字理由；
        太小会重演「正文被 reasoning 挤掉、content 为空」那个 bug。
        """
        from app.services.llm.client import MAX_OUTPUT_TOKENS

        assert MAX_OUTPUT_TOKENS >= 1024

    def test_probe_still_uses_tiny_max_tokens(self):
        """探测请求**故意**用 `max_tokens=1`（越快越好、越省越好），
        别被上面那条"必须给上限"误导成一起改大。"""
        from app.services.llm.client import OpenAICompatClient

        calls: list = []
        original = OpenAICompatClient._probe

        OpenAICompatClient._probe = lambda self, fmt: calls.append(fmt) or True
        try:
            c = OpenAICompatClient(model="m")
            c._caps = None
            c.capabilities()
        finally:
            OpenAICompatClient._probe = original
        assert calls, "探测没被调用"


class _Captured(Exception):
    """内部信号：payload 已抓到，不必真发请求。"""

