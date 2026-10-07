"""`app/channels/clawbot.py` 协议层的契约测试。

## 为什么全靠 `httpx.MockTransport`

本机**不能联网验证收发消息**：`getupdates` / `sendmessage` 都要 token，
而拿到 token 就必须真扫码完成绑定 —— 那是明确禁止的操作（会占掉用户的微信绑定位）。
所以除了二维码端点（已单独真调验证），其余全部用 `MockTransport` 伪造响应。

覆盖的 10 个场景见任务书；最要命的是两条：
- `fetch_updates` **必须先存游标再返回**（丢了会重复回复）
- 单条消息处理失败**不能拖垮后台循环**（否则微信永久失灵且用户看不出为什么）
"""

from __future__ import annotations

import json
import time

import httpx
import pytest

from app import config
from app.channels.clawbot import (
    ClawBotChannel,
    ClawBotSession,
    ClawBotWorker,
    ChannelError,
    InboundMessage,
    get_channel,
    reset_channel_cache,
)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """每个用例一个干净的 DATA_DIR + 干净的单例。

    会话文件路径是从 `config.DATA_DIR` 现算的，所以把 DATA_DIR 指到 `tmp_path`
    就能把落盘隔离到系统临时目录，**不会碰到项目的 `data/`**。
    """
    monkeypatch.setattr(config, "DATA_DIR", tmp_path, raising=False)
    reset_channel_cache()
    yield
    reset_channel_cache()


def make_channel(handler, **kwargs) -> ClawBotChannel:
    """构造一个走 `MockTransport` 的通道（不联网）。"""
    return ClawBotChannel(
        base_url="https://example.test",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 1. start_login 解析
# ---------------------------------------------------------------------------
def test_start_login_parses_qrcode_and_content():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/ilink/bot/get_bot_qrcode"
        assert request.url.params.get("bot_type") == "3"
        return httpx.Response(
            200,
            json={
                "ret": 0,
                "qrcode": "QC-1",
                "qrcode_img_content": "https://liteapp.weixin.qq.com/q/xxx?qrcode=QC-1",
            },
        )

    ticket = make_channel(handler).start_login()

    assert ticket.qrcode == "QC-1"
    assert ticket.qr_content == "https://liteapp.weixin.qq.com/q/xxx?qrcode=QC-1"


# ---------------------------------------------------------------------------
# 2. poll_login 三态
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("status", ["wait", "scanned"])
def test_poll_login_pending_states_have_no_session(status):
    ch = make_channel(lambda req: httpx.Response(200, json={"ret": 0, "status": status}))

    result = ch.poll_login("QC-1")

    assert result.state == status
    assert result.session is None
    # 未 confirmed 就不能落盘
    assert ch.load_session() is None


def test_poll_login_confirmed_returns_and_persists_session():
    body = {
        "ret": 0,
        "status": "confirmed",
        "bot_token": "TOK-abc",
        "ilink_bot_id": "12345@im.bot",
        "ilink_user_id": "67890@im.wechat",
        "baseurl": "https://ilinkai.weixin.qq.com",
    }
    ch = make_channel(lambda req: httpx.Response(200, json=body))

    result = ch.poll_login("QC-1")

    assert result.state == "confirmed"
    assert result.session is not None
    assert result.session.token == "TOK-abc"
    # 已落盘
    on_disk = ch.load_session()
    assert on_disk is not None
    assert on_disk.token == "TOK-abc"
    assert on_disk.bot_id == "12345@im.bot"
    assert on_disk.user_id == "67890@im.wechat"


def test_poll_login_missing_status_defaults_to_wait():
    """实测 `expired` 是真实取值；`status` 缺失时退化成 `wait` 而不是崩。"""
    ch = make_channel(lambda req: httpx.Response(200, json={"ret": 0}))

    assert ch.poll_login("QC-1").state == "wait"


# ---------------------------------------------------------------------------
# 3 / 4. load_session 的容错
# ---------------------------------------------------------------------------
def test_load_session_missing_file_returns_none():
    assert ClawBotChannel().load_session() is None


def test_load_session_corrupt_json_returns_none():
    path = config.DATA_DIR / "clawbot_session.json"
    path.write_text("{ 这不是合法 JSON", encoding="utf-8")

    assert ClawBotChannel().load_session() is None  # 不抛异常


def test_load_session_without_token_returns_none():
    """合法 JSON 但没有 token，等同于未绑定。"""
    (config.DATA_DIR / "clawbot_session.json").write_text(
        json.dumps({"bot_id": "x"}), encoding="utf-8"
    )

    assert ClawBotChannel().load_session() is None


def test_clear_session_is_idempotent():
    ch = ClawBotChannel()
    ch.save_session(ClawBotSession(token="TOK"))
    assert ch.is_bound() is True

    ch.clear_session()
    assert ch.is_bound() is False
    ch.clear_session()  # 再删一次不能抛（文件已不在）


# ---------------------------------------------------------------------------
# 5. fetch_updates：先存游标再返回
# ---------------------------------------------------------------------------
def test_fetch_updates_advances_cursor_and_returns_messages():
    body = {
        "ret": 0,
        "get_updates_buf": "CURSOR-2",
        "msgs": [
            {
                "msg_id": "m1",
                "from_user_id": "u@im.wechat",
                "context_token": "CT-1",
                "create_time_ms": 1700000000000,
                "item_list": [{"type": 1, "text_item": {"text": "早上穿什么"}}],
            }
        ],
    }
    ch = make_channel(lambda req: httpx.Response(200, json=body))
    ch.save_session(ClawBotSession(token="TOK", updates_buf="CURSOR-1"))

    messages = ch.fetch_updates()

    assert [m.text for m in messages] == ["早上穿什么"]
    assert messages[0].peer_id == "u@im.wechat"
    assert messages[0].context_token == "CT-1"      # 必须一路带到 send_text
    assert messages[0].kind == "text"

    # ★ 关键：本次调用返回时，游标**已经**落盘成新的（先存游标再返回）
    assert ch.load_session().updates_buf == "CURSOR-2"


def test_fetch_updates_sends_previous_cursor():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"ret": 0, "get_updates_buf": "C2", "msgs": []})

    ch = make_channel(handler)
    ch.save_session(ClawBotSession(token="TOK", updates_buf="C1"))

    ch.fetch_updates()

    assert seen["get_updates_buf"] == "C1"          # 原样回传上次的游标


def test_fetch_updates_parses_voice_transcript():
    """语音消息自带转文字 —— 用户可以直接说话。"""
    body = {
        "ret": 0,
        "get_updates_buf": "C2",
        "msgs": [
            {
                "msg_id": "m1",
                "from_user_id": "u@im.wechat",
                "context_token": "CT-1",
                "item_list": [
                    {"type": 3, "voice_item": {"media": {"x": 1}, "text": "这件怎么搭"}}
                ],
            }
        ],
    }
    ch = make_channel(lambda req: httpx.Response(200, json=body))
    ch.save_session(ClawBotSession(token="TOK"))

    messages = ch.fetch_updates()

    assert messages[0].kind == "voice"
    assert messages[0].text == "这件怎么搭"


# ---------------------------------------------------------------------------
# 6. 未绑定
# ---------------------------------------------------------------------------
def test_fetch_updates_unbound_raises_channel_error():
    with pytest.raises(ChannelError):
        ClawBotChannel().fetch_updates()


def test_send_text_unbound_raises_channel_error():
    with pytest.raises(ChannelError):
        ClawBotChannel().send_text("u", "hi", context_token="CT")


# ---------------------------------------------------------------------------
# 7. send_text 的 payload
# ---------------------------------------------------------------------------
def test_send_text_payload_carries_context_token():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content)
        captured["headers"] = request.headers
        return httpx.Response(200, json={"ret": 0})

    ch = make_channel(handler)
    ch.save_session(ClawBotSession(token="TOK-xyz"))

    ch.send_text("u@im.wechat", "去穿那件风衣", context_token="CT-原样")

    msg = captured["json"]["msg"]
    assert msg["context_token"] == "CT-原样"          # ★ 原样回传
    assert msg["message_type"] == 2
    assert msg["message_state"] == 2
    assert msg["client_id"]                            # 非空
    assert msg["to_user_id"] == "u@im.wechat"
    assert msg["item_list"][0]["text_item"]["text"] == "去穿那件风衣"

    headers = captured["headers"]
    assert headers["Authorization"] == "Bearer TOK-xyz"
    assert headers["AuthorizationType"] == "ilink_bot_token"
    assert headers["X-WECHAT-UIN"]                      # 非空


def test_send_text_client_id_is_unique_per_call():
    ids: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        ids.append(json.loads(request.content)["msg"]["client_id"])
        return httpx.Response(200, json={"ret": 0})

    ch = make_channel(handler)
    ch.save_session(ClawBotSession(token="TOK"))

    ch.send_text("u", "a", context_token="c")
    ch.send_text("u", "a", context_token="c")

    assert len(set(ids)) == 2                           # client_id 用于去重，不能重


# ---------------------------------------------------------------------------
# 8. 异常统一收敛成 ChannelError
# ---------------------------------------------------------------------------
def test_http_500_becomes_channel_error():
    ch = make_channel(lambda req: httpx.Response(500, text="boom"))

    with pytest.raises(ChannelError):
        ch.start_login()


def test_network_error_becomes_channel_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    ch = make_channel(handler)

    with pytest.raises(ChannelError):
        ch.start_login()


def test_network_error_on_fetch_becomes_channel_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    ch = make_channel(handler)
    ch.save_session(ClawBotSession(token="TOK"))

    with pytest.raises(ChannelError):
        ch.fetch_updates()


def test_send_text_http_error_becomes_channel_error():
    ch = make_channel(lambda req: httpx.Response(500, text="boom"))
    ch.save_session(ClawBotSession(token="TOK"))

    with pytest.raises(ChannelError):
        ch.send_text("u", "hi", context_token="c")


def test_nonzero_ret_becomes_channel_error():
    ch = make_channel(lambda req: httpx.Response(200, json={"ret": 40001, "errmsg": "x"}))

    with pytest.raises(ChannelError):
        ch.start_login()


# ---------------------------------------------------------------------------
# 9. get_channel 单例
# ---------------------------------------------------------------------------
def test_get_channel_returns_same_instance():
    first = get_channel()
    second = get_channel()

    assert first is second


def test_reset_channel_cache_yields_new_instance():
    first = get_channel()
    reset_channel_cache()
    second = get_channel()

    assert second is not first


# ---------------------------------------------------------------------------
# 10. ClawBotWorker
# ---------------------------------------------------------------------------
class _FakeChannel:
    """给 worker 用的假通道：按批次吐消息，之后空转（模拟长轮询返回空）。"""

    def __init__(self, batches: list[list[InboundMessage]] | None = None) -> None:
        self._batches = list(batches or [])
        self.sent: list[tuple[str, str, str]] = []
        self.fetch_calls = 0

    def fetch_updates(self, *, timeout=None) -> list[InboundMessage]:
        self.fetch_calls += 1
        if self._batches:
            return self._batches.pop(0)
        time.sleep(0.02)               # 别忙等
        return []

    def send_text(self, peer_id: str, text: str, *, context_token: str) -> None:
        self.sent.append((peer_id, text, context_token))


def test_worker_start_stop_toggles_running():
    worker = ClawBotWorker(_FakeChannel(), lambda m: None)

    assert worker.running is False
    worker.start()
    assert worker.running is True
    worker.stop()
    assert worker.running is False


def test_worker_survives_handler_exception():
    """handler 第一次抛、第二次正常 —— 两次都必须被调用（循环没死）。"""
    calls: list[str] = []

    def handler(message: InboundMessage):
        calls.append(message.message_id)
        if len(calls) == 1:
            raise RuntimeError("第一条就炸")
        return "ok"

    batch = [
        InboundMessage(peer_id="u", text="a", context_token="c1", message_id="m1"),
        InboundMessage(peer_id="u", text="b", context_token="c2", message_id="m2"),
    ]
    fake = _FakeChannel([batch])
    worker = ClawBotWorker(fake, handler, poll_error_backoff=0.02)

    worker.start()
    deadline = time.time() + 3.0
    while len(calls) < 2 and time.time() < deadline:
        time.sleep(0.01)
    worker.stop()

    assert calls == ["m1", "m2"], "第一条抛异常后循环被打断，第二条没被处理"
    assert worker.running is False
    # 失败要主动回复"出错了"（静默失败比报错糟）
    assert any("出错" in text for _, text, _ in fake.sent)
    # 成功的第二条要回复，且 context_token 原样带走
    assert ("u", "ok", "c2") in fake.sent


def test_worker_survives_fetch_error():
    """fetch_updates 抛 ChannelError 不能让循环退出。"""
    errors = {"n": 0}

    class _FlakyChannel(_FakeChannel):
        def fetch_updates(self, *, timeout=None):
            errors["n"] += 1
            if errors["n"] == 1:
                raise ChannelError("boom")
            time.sleep(0.02)
            return []

    worker = ClawBotWorker(_FlakyChannel(), lambda m: None, poll_error_backoff=0.02)
    worker.start()
    deadline = time.time() + 3.0
    while errors["n"] < 2 and time.time() < deadline:
        time.sleep(0.01)
    worker.stop()

    assert errors["n"] >= 2, "拉取失败一次后循环就死了"
