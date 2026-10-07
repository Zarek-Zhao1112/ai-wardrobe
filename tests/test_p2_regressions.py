"""P2 审查（W4）发现的问题的回归测试。

每条都对应一个**实测复现过**的缺陷，不是"预防性"断言。
按来源分组，方便将来看"这一波修了什么"。
"""

from __future__ import annotations

import pytest

from app import config
from app.services import images


class TestImagePathSanitising:
    """① `save_image` 的写路径未消毒 → 500（且写操作发生在归属校验之前）。

    复现（修复前）：`POST /items/a%5Cb/image` → 500 FileNotFoundError，
    因为 `Path("a\\b")` 在 Windows 上被当成子目录，暂存文件指向不存在的目录。

    修法：在 `image_path_of`（唯一生成文件名的地方）拦下来抛 `LookupError`，
    路由层那层已经把它翻成 404。
    """

    @pytest.mark.parametrize(
        "bad",
        [
            "a\\b",            # 反斜杠（Windows 分隔符）—— 原始复现用例
            "..\\escaped",
            "a/b",             # 正斜杠
            "a:b",             # Windows 保留字符
            "a*b", "a?b", 'a"b', "a<b", "a>b", "a|b",
            "",                # 空
            ".", "..",         # 目录本身
        ],
    )
    def test_rejects_unsafe_ids(self, bad):
        with pytest.raises(LookupError):
            images.image_path_of(bad, ".png")

    def test_accepts_normal_id(self):
        assert images.image_path_of("it_abc123", ".png") == "it_abc123.png"

    def test_save_image_raises_lookup_not_oserror(self, tmp_path, monkeypatch):
        """★ 关键：异常类型必须是 `LookupError`（→ 404），不能是 `OSError`（→ 500）。

        只断言"抛了异常"是不够的 —— 修之前它也抛，抛的是 FileNotFoundError。
        """
        monkeypatch.setattr(config, "DB_PATH", tmp_path / "x.db")
        monkeypatch.setattr(images.db, "IMAGES_DIR", tmp_path / "images")

        png = _tiny_png()
        with pytest.raises(LookupError):
            images.save_image("u_1", "a\\b", png)

    def test_no_stray_file_left_behind(self, tmp_path, monkeypatch):
        """非法 id 不该在磁盘上留下任何东西。"""
        monkeypatch.setattr(images.db, "IMAGES_DIR", tmp_path / "images")
        with pytest.raises(LookupError):
            images.save_image("u_1", "..\\escape", _tiny_png())
        assert not (tmp_path / "images").exists() or not list(
            (tmp_path / "images").iterdir()
        )

    def test_read_image_still_sanitises(self):
        """`read_image` 早就消毒了，别在重构里弄丢。"""
        assert images.read_image("../../etc/passwd") is None
        assert images.read_image("") is None


class TestThinkingFlag:
    """② "抑制思考"是 DeepSeek 专有字段，硬编码会绑死供应商。

    它本身没错（实测 52s → 1.8s），但**必须能关掉** —— 严格校验未知参数的
    端点会对它返回 400，导致所有调用失败。
    """

    def test_default_is_enabled(self):
        """默认开：本项目当前供应商就是推理模型，不开会慢 30 倍。"""
        assert config.LLM_THINKING_DISABLED is True

    def test_payload_includes_thinking_when_enabled(self, monkeypatch):
        from app.services.llm.client import OpenAICompatClient

        monkeypatch.setattr(config, "LLM_THINKING_DISABLED", True)
        payload = _capture_payload(OpenAICompatClient(model="m"))
        assert payload.get("thinking") == {"type": "disabled"}

    def test_payload_omits_thinking_when_disabled(self, monkeypatch):
        """★ 关掉之后 payload 里**不能有**这个键（不是设成 null，是完全没有）。"""
        from app.services.llm.client import OpenAICompatClient

        monkeypatch.setattr(config, "LLM_THINKING_DISABLED", False)
        payload = _capture_payload(OpenAICompatClient(model="m"))
        assert "thinking" not in payload, "严格端点会因为未知字段返回 400"

    def test_text_call_respects_flag_too(self, monkeypatch):
        """`chat_text` 是同一条规则，别只改一处。"""
        from app.services.llm.client import OpenAICompatClient

        monkeypatch.setattr(config, "LLM_THINKING_DISABLED", False)
        payload = _capture_payload(
            OpenAICompatClient(model="m"), text=True
        )
        assert "thinking" not in payload

    def test_env_flag_parser(self, monkeypatch):
        """`_env_flag` 的取值口径（大小写、假值集合）。"""
        for raw, expected in [
            ("0", False), ("false", False), ("FALSE", False), ("no", False),
            ("off", False), ("1", True), ("true", True), ("yes", True),
            (" anything ", True),
        ]:
            monkeypatch.setenv("X_TEST_FLAG", raw)
            assert config._env_flag("X_TEST_FLAG", True) is expected, raw
        monkeypatch.delenv("X_TEST_FLAG", raising=False)
        assert config._env_flag("X_TEST_FLAG", True) is True
        assert config._env_flag("X_TEST_FLAG", False) is False


class TestRejectStatus:
    """③ 404 不该被当成"服务端拒绝输出模式"。

    404 的常见原因是 `base_url` 配错 —— 那是**配置错误**。归入 `_REJECT_STATUS`
    会让 `chat_json` 连跑三级降级、最后报「所有结构化输出策略都被拒绝」，
    **把配置问题伪装成模型能力问题**。
    """

    def test_404_not_treated_as_rejection(self):
        from app.services.llm.client import _REJECT_STATUS

        assert 404 not in _REJECT_STATUS

    def test_400_still_triggers_downgrade(self):
        """400 才是"不认这个参数"的典型信号，降级链靠它工作。"""
        from app.services.llm.client import _REJECT_STATUS

        assert 400 in _REJECT_STATUS

    def test_404_surfaces_as_llmerror_with_status(self, monkeypatch):
        """404 要变成带状态码的 `LLMError`，让人一眼看出是地址不对。"""
        import httpx

        from app.services.llm.client import LLMError, OpenAICompatClient

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="Not Found")

        client = OpenAICompatClient(
            base_url="https://example.invalid/v1",   # 必须是绝对地址，否则 httpx 收不了相对路径
            api_key="test-key",
            model="m",
            transport=httpx.MockTransport(handler),
        )
        client._caps = _text_only()
        with pytest.raises(LLMError) as exc:
            client.chat_json([{"role": "user", "content": "hi"}])
        assert "404" in str(exc.value)


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _text_only():
    """伪造"不支持任何结构化输出"的探测结果，跳过真实探测。"""
    from app.services.llm.client import Capabilities

    return Capabilities(model="m", supports_json_schema=False, supports_json_mode=False)


def _capture_payload(client, *, text: bool = False) -> dict:
    """抓 `_post` / `_post_chat` 实际发出的 payload，不发真实请求。"""
    from app.services.llm.client import OpenAICompatClient

    got: dict = {}
    original = OpenAICompatClient._post

    def fake(self, payload, timeout=60.0):
        got.update(payload)
        raise _Stop()

    OpenAICompatClient._post = fake
    try:
        if text:
            client.chat_text([{"role": "user", "content": "hi"}])
        else:
            client._caps = _text_only()
            client.chat_json([{"role": "user", "content": "hi"}])
    except (_Stop, Exception):
        pass
    finally:
        OpenAICompatClient._post = original
    return got


class _Stop(Exception):
    """内部信号：payload 已抓到。"""


def _tiny_png(width: int = 8, height: int = 8) -> bytes:
    """造一张真 PNG（图片服务靠**读文件头**判断格式，不能随便给字节）。"""
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + bytes([9, 9, 9]) * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
