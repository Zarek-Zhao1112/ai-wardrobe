"""iLink / ClawBot 协议层。

**协议细节见 `docs/ilink-protocol.md`** —— 那份文档标了「✅ 已实测」与「📄 来自资料」，
实现时**凡是标 📄 的都当待验证**，先发一次真实请求看响应，别照抄资料里的字段名。

---

## 这一层要解决的三件事

1. **绑定**：拿二维码 → 用户扫码 → 轮询到 `confirmed` → 存 `bot_token`
2. **收**：长轮询 `getupdates`（服务端 hold 最多 ~35s）
3. **发**：`sendmessage`，**必须原样回传入站的 `context_token`**

## 两个硬约束

**① 长轮询不能跑在 HTTP 请求线程里。** 一次 hold 35 秒，会把 Web 请求全堵住。
所以有 `ClawBotWorker`：后台线程循环拉消息 → 交给 handler → 回复。

**② `context_token` 必须原样回传。** 不回传则消息**关联不到正确的会话窗口**，
而且服务端**不报错** —— 这是本协议最容易踩且最难查的坑。

## 存储

`bot_token` 与 `get_updates_buf` 游标存在 `data/clawbot_session.json`（已 gitignore）。
- 为什么不用数据库：**删文件即可重置绑定**，跟业务数据解耦
- ⚠️ 游标**必须持久化**：丢了会从头拉，导致**重复回复**
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Protocol, runtime_checkable

import httpx

from .. import config

log = logging.getLogger(__name__)

#: 官方网关。**不在 `config.py` 里**（未获授权改那个文件）—— 目前只有这一个环境。
DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"

#: 请求体 `base_info.channel_version`。📄 来自资料，**本机未验证**。
CHANNEL_VERSION = "1.0.2"

#: 会话文件名。**目录从 `config.DATA_DIR` 推导**（与 `db.py` 同一套路径），不硬编码。
SESSION_FILENAME = "clawbot_session.json"

#: `item_list[].type` → `InboundMessage.kind`。📄 来自资料，**本机未验证**。
_ITEM_KIND = {1: "text", 2: "image", 3: "voice", 4: "file", 5: "video"}


def _now_iso() -> str:
    """本地时间戳（秒精度，带时区），与 `db.now_iso()` 同格式。"""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _random_uint32_b64() -> str:
    """`X-WECHAT-UIN` 的值：`base64(random_uint32)`。📄 资料说必填，本机未验证。"""
    return base64.b64encode(os.urandom(4)).decode("ascii")


class ChannelError(Exception):
    """通道层的统一异常。

    ⚠️ 和 `llm/client.py` 同样的规矩：**不要把 httpx 的原始异常类型漏出去**，
    调用方只该认识这一种。
    """


# ---------------------------------------------------------------------------
# 数据契约（这些就是"冻结的边界"，字段名不许改）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LoginTicket:
    """一次绑定尝试。`qr_content` 是要**渲染成二维码**的内容（不是图片 URL）。"""

    qrcode: str
    qr_content: str


@dataclass(frozen=True)
class LoginStatus:
    """轮询绑定的结果。

    `state` 取值：`wait`（还没扫）/ `scanned`（扫了没确认）/ `confirmed`（成功）/ `expired`
    ⚠️ 具体字符串以**实测响应**为准（`docs/ilink-protocol.md` 标了 📄）。
    """

    state: str
    session: Optional["ClawBotSession"] = None


@dataclass
class ClawBotSession:
    """绑定凭据。`token` 过期后要重新扫码。"""

    token: str
    bot_id: str = ""
    user_id: str = ""
    base_url: str = ""
    #: 长轮询游标。**原样保存、原样回传**，不要解析它的内部结构。
    updates_buf: str = ""
    saved_at: str = ""


@dataclass(frozen=True)
class InboundMessage:
    """一条入站消息，**已剥掉所有协议细节** —— 业务层只该看见这个。

    ⚠️ `context_token` 是唯一的例外：它必须一路带到 `send_text`。
    这是协议的硬要求，不是设计缺陷；但业务层不该**解释**它，只该原样传递。
    """

    peer_id: str                 # 发消息的人
    text: str                    # 文本内容（语音消息已转文字）
    context_token: str           # 回复时必须原样带回
    message_id: str = ""
    created_at: str = ""
    kind: str = "text"           # text | image | voice | file | video
    media: dict[str, Any] = field(default_factory=dict)   # 媒体信息（第二阶段用）


@runtime_checkable
class MessageHandler(Protocol):
    """业务层要实现的东西：**给一条消息，返回要回复的文本**（`None` = 不回复）。

    就这一个方法 —— 这是通道层与业务层之间**唯一的**接口。
    """

    def __call__(self, message: InboundMessage) -> Optional[str]: ...


# ---------------------------------------------------------------------------
# 通道
# ---------------------------------------------------------------------------


class ClawBotChannel:
    """iLink 客户端。**不要自己 new**，走 `get_channel()`（便于测试注入）。

    实现要点（W1 填实现时逐条落实）：

    1. **`start_login()`**：`GET /ilink/bot/get_bot_qrcode?bot_type=3`，**不需要鉴权**。
       返回的 `qrcode_img_content` 是扫码内容，要自己渲染成二维码（前端/CLI 都行）。
    2. **`poll_login(qrcode)`**：`GET /ilink/bot/get_qrcode_status?qrcode=...`。
       状态流转 `wait → scanned → confirmed`；`confirmed` 时**把 token 存下来**。
       二维码约 120 秒过期 → 过期后要能重新 `start_login()`。
    3. **`fetch_updates()`**：`POST /ilink/bot/getupdates`，带 `get_updates_buf`。
       ⚠️ 服务端会 hold 最多 35 秒，所以**超时要设到 45 秒以上**，否则永远超时。
       ⚠️ 返回的 `get_updates_buf` **原样存回 session**。
    4. **`send_text()`**：`POST /ilink/bot/sendmessage`。
       `message_type=2`、`message_state=2`、`client_id` 自生成（用于去重）、
       **`context_token` 原样带上**。
    5. **请求头**：`Authorization: Bearer <token>` + `AuthorizationType: ilink_bot_token`
       + `X-WECHAT-UIN`（📄 资料说必填，格式 `base64(random_uint32)`；**先按必填做，
       如果服务端报错再调**）。
    6. **异常**一律包成 `ChannelError`；**未绑定**（没有 token）时也要抛 `ChannelError`，
       不要抛别的类型。
    7. **`transport` 参数**：为了让测试能用 `httpx.MockTransport` 伪造响应，
       `__init__` 要接受一个可选的 `transport`。**这是测试协议层的唯一手段**。
    """

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        timeout: float = 45.0,          # > 长轮询的 35s hold
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        # 长轮询一次 hold 最多 ~35s，所以默认 45s；`__init__` 的签名由契约冻结，
        # 这里不做额外校验（非法值让 httpx 自己报）。
        self.timeout = timeout
        # transport 只为可测试性存在：注入 MockTransport 才能在不联网的前提下
        # 验证响应解析与异常收敛。生产路径传 None，走 httpx 默认实现。
        self._transport = transport

    # ------------------------------------------------------------------
    # HTTP 细节
    # ------------------------------------------------------------------
    def _base_for(self, session: Optional["ClawBotSession"]) -> str:
        """实际该打哪个 host。

        ⚠️ 绑定响应里可能下发 `baseurl`（`poll_login` 已存进 session）——
        **必须用它**。否则服务端要求换 host 时我们会一直打到旧域名上，
        而且失败是静默的（W4 审查抓到）。
        """
        url = session.base_url if session and session.base_url else self.base_url
        return url.rstrip("/")

    def _endpoint(self, path: str, *, session: Optional["ClawBotSession"] = None) -> str:
        return f"{self._base_for(session)}{path}"

    def _new_client(self, timeout: float) -> httpx.Client:
        return httpx.Client(transport=self._transport, timeout=timeout)

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "Authorization": f"Bearer {token}",
            "X-WECHAT-UIN": _random_uint32_b64(),
        }

    @staticmethod
    def _body(resp: httpx.Response) -> dict[str, Any]:
        """校验状态码 + 解析 JSON。**任何失败都收敛成 `ChannelError`。**"""
        if resp.status_code >= 400:
            raise ChannelError(
                f"服务端返回 HTTP {resp.status_code}：{resp.text[:200]}"
            )
        try:
            body = resp.json()
        except ValueError as e:
            raise ChannelError(f"服务端响应不是合法 JSON：{resp.text[:200]}") from e
        if not isinstance(body, dict):
            raise ChannelError(f"服务端响应不是 JSON 对象：{resp.text[:200]}")
        ret = body.get("ret", 0)
        if ret not in (0, None):
            raise ChannelError(f"服务端返回 ret={ret}：{body}")
        return body

    # ---- 绑定 ----

    def start_login(self) -> LoginTicket:
        """拿一个新的二维码。**不需要鉴权**（还没登录自然没有 token）。"""
        try:
            with self._new_client(self.timeout) as client:
                resp = client.get(
                    self._endpoint("/ilink/bot/get_bot_qrcode"),
                    params={"bot_type": 3},
                )
        except httpx.HTTPError as e:
            raise ChannelError(f"获取二维码失败：{e}") from e

        body = self._body(resp)
        qrcode = body.get("qrcode")
        qr_content = body.get("qrcode_img_content")
        if not qrcode or not qr_content:
            raise ChannelError(f"二维码响应缺少 qrcode / qrcode_img_content：{body}")
        return LoginTicket(qrcode=str(qrcode), qr_content=str(qr_content))

    def poll_login(self, qrcode: str) -> LoginStatus:
        """查扫码进度。`confirmed` 时把 token 存下来。"""
        try:
            with self._new_client(self.timeout) as client:
                resp = client.get(
                    self._endpoint("/ilink/bot/get_qrcode_status"),
                    params={"qrcode": qrcode},
                )
        except httpx.HTTPError as e:
            raise ChannelError(f"查询扫码状态失败：{e}") from e

        body = self._body(resp)
        # ✅ 实测：字段名是 `status`，取值 `wait` / `scanned` / `confirmed` / `expired`。
        state = str(body.get("status") or "wait")
        if state != "confirmed":
            return LoginStatus(state=state)

        # ⚠️ confirmed 分支的字段名（bot_token / ilink_bot_id / ilink_user_id /
        # baseurl）是 📄 资料所述，**本机无法验证**（验证需要真扫码，明确禁止）。
        # 因此这里做别名兼容，两种命名都认，避免照抄错名导致整条链路拿不到 token。
        token = body.get("bot_token") or body.get("token")
        if not token:
            raise ChannelError(f"confirmed 响应里没有 bot_token：{body}")
        session = ClawBotSession(
            token=str(token),
            bot_id=str(body.get("ilink_bot_id") or body.get("bot_id") or ""),
            user_id=str(body.get("ilink_user_id") or body.get("user_id") or ""),
            base_url=str(body.get("baseurl") or body.get("base_url") or self.base_url),
            updates_buf=str(body.get("get_updates_buf") or ""),
            saved_at=_now_iso(),
        )
        self.save_session(session)
        return LoginStatus(state=state, session=session)

    # ---- 凭据 ----

    def _session_path(self):
        """会话文件路径：`<DATA_DIR>/clawbot_session.json`（同 `db.py` 的推导方式）。"""
        return config.DATA_DIR / SESSION_FILENAME

    def load_session(self) -> Optional[ClawBotSession]:
        """读 `data/clawbot_session.json`；没有或损坏返回 `None`（**不抛异常**）。"""
        try:
            text = self._session_path().read_text(encoding="utf-8")
        except OSError:
            # 文件不存在 / 无权限 / 目录不存在 —— 一律当"未绑定"，不抛
            return None
        try:
            data = json.loads(text)
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        token = data.get("token")
        if not token:
            return None
        return ClawBotSession(
            token=str(token),
            bot_id=str(data.get("bot_id") or ""),
            user_id=str(data.get("user_id") or ""),
            base_url=str(data.get("base_url") or ""),
            updates_buf=str(data.get("updates_buf") or ""),
            saved_at=str(data.get("saved_at") or ""),
        )

    def save_session(self, session: ClawBotSession) -> None:
        path = self._session_path()
        if not session.saved_at:
            session.saved_at = _now_iso()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(asdict(session), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            raise ChannelError(f"写入会话文件失败：{e}") from e

    def clear_session(self) -> None:
        """解绑：删掉会话文件。"""
        try:
            self._session_path().unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            raise ChannelError(f"删除会话文件失败：{e}") from e

    def is_bound(self) -> bool:
        return self.load_session() is not None

    # ---- 收发 ----

    def fetch_updates(self, *, timeout: Optional[float] = None) -> list[InboundMessage]:
        """长轮询拉一批消息（可能为空）。

        ⚠️ 会把 `get_updates_buf` 存回 session —— **必须先存游标再返回消息**，
        否则处理消息时崩了会导致下次重复拉同一批（重复回复）。
        未绑定时抛 `ChannelError`。
        """
        session = self.load_session()
        if session is None:
            raise ChannelError("未绑定：没有会话 token，请先扫码绑定")

        # 先解析（无副作用），再落游标，最后返回 —— 落游标一定在返回之前。
        payload = {
            "get_updates_buf": session.updates_buf,
            "base_info": {"channel_version": CHANNEL_VERSION},
        }
        try:
            with self._new_client(timeout if timeout is not None else self.timeout) as client:
                resp = client.post(
                    self._endpoint("/ilink/bot/getupdates", session=session),
                    headers=self._headers(session.token),
                    json=payload,
                )
        except httpx.HTTPError as e:
            raise ChannelError(f"拉取消息失败：{e}") from e

        body = self._body(resp)

        # ⚠️ 硬要求：**先存游标再返回消息**。丢了游标 → 下次重复拉同一批 → 重复回复。
        # ⚠️ 而且必须在**解析消息之前**存（W4 审查抓到）：解析曾经会抛非
        #    `ChannelError` 的异常（见 `_to_inbound` 的时间戳），游标就永远推不动 →
        #    同一批消息反复抛 → 通道**焊死**，而且只有日志看得出来。
        buf = body.get("get_updates_buf")
        if buf is not None:
            session.updates_buf = str(buf)
            self.save_session(session)

        # 逐条解析：**一条坏消息不能拖掉整批**，更不能挡住游标推进。
        messages: list[InboundMessage] = []
        for raw in body.get("msgs") or []:
            try:
                messages.append(self._to_inbound(raw))
            except Exception:        # noqa: BLE001 —— 兜底是刻意的，理由见上
                log.exception("解析入站消息失败，已跳过：%r", raw)
        return messages

    def send_text(
        self,
        peer_id: str,
        text: str,
        *,
        context_token: str,
    ) -> None:
        """发一条文本。**`context_token` 必须原样来自那条入站消息。**"""
        session = self.load_session()
        if session is None:
            raise ChannelError("未绑定：没有会话 token，请先扫码绑定")

        payload = {
            "msg": {
                "to_user_id": peer_id,
                # client_id 自生成、唯一即可，服务端据此去重（重发不会被当成两条）
                "client_id": uuid.uuid4().hex,
                "message_type": 2,       # 2 = BOT 发出的消息
                "message_state": 2,      # 2 = FINISH
                # ★ 原样回传入站的 context_token，否则消息关联不到会话窗口（且不报错）
                "context_token": context_token,
                "item_list": [{"type": 1, "text_item": {"text": text}}],
            },
            "base_info": {"channel_version": CHANNEL_VERSION},
        }
        try:
            with self._new_client(self.timeout) as client:
                resp = client.post(
                    self._endpoint("/ilink/bot/sendmessage", session=session),
                    headers=self._headers(session.token),
                    json=payload,
                )
        except httpx.HTTPError as e:
            raise ChannelError(f"发送消息失败：{e}") from e

        self._body(resp)

    @staticmethod
    def _to_inbound(msg: Any) -> InboundMessage:
        """把一条协议原始消息剥成 `InboundMessage`。字段名见 `docs/ilink-protocol.md` §4。"""
        if not isinstance(msg, dict):
            return InboundMessage(peer_id="", text="", context_token="")
        items = msg.get("item_list") or []
        kind = "text"
        text = ""
        media: dict[str, Any] = {}
        first = items[0] if items and isinstance(items[0], dict) else None
        if first is not None:
            raw_type = first.get("type")
            # `_ITEM_KIND.get(不可哈希的值)` 会抛 TypeError —— 先过一道类型闸
            kind = _ITEM_KIND.get(raw_type) if isinstance(raw_type, int) else None
            if kind is None:
                kind = "text"
            if raw_type == 1:
                text = str((first.get("text_item") or {}).get("text") or "")
            elif first.get("type") == 3:
                # 语音自带转文字 —— 用户可以直接说话，不用打字
                text = str((first.get("voice_item") or {}).get("text") or "")
            media = {k: v for k, v in first.items() if k != "type"}

        created = ""
        ms = msg.get("create_time_ms")
        if isinstance(ms, (int, float)) and not isinstance(ms, bool):
            # ⚠️ 单位与量级**都还没实测过**（协议文档标 📄）→ 任何异常值都不许往上抛：
            #    本函数在 `fetch_updates` 的解析循环里，抛出去会拖掉整批消息
            #    （曾经连游标都推不动）。解析不出来就留空，不影响正文处理。
            try:
                created = datetime.fromtimestamp(
                    ms / 1000, tz=timezone.utc
                ).astimezone().isoformat(timespec="seconds")
            except (OSError, OverflowError, ValueError):
                log.warning("create_time_ms 解析失败，已忽略：%r", ms)

        return InboundMessage(
            peer_id=str(msg.get("from_user_id") or ""),
            text=text,
            context_token=str(msg.get("context_token") or ""),
            message_id=str(msg.get("msg_id") or ""),
            created_at=created,
            kind=kind,
            media=media,
        )


class ClawBotWorker:
    """后台线程：循环 `fetch_updates` → 交给 handler → 把返回值发回去。

    **为什么需要它**：长轮询一次 hold 35 秒，跑在请求线程里会把 Web 全堵住。

    实现要点：

    1. 用 `threading.Thread(daemon=True)`，提供 `start()` / `stop()`（`stop` 要能让
       循环在**最多一次超时内**退出，不能等 35 秒）
    2. **单条消息处理失败不能拖垮整个循环** —— 捕获异常、记日志、继续拉。
       （否则一条坏消息就让微信永久失灵，而且用户看不出为什么）
    3. 处理失败时**要不要回复用户"出错了"**：要。静默失败比报错糟得多。
    4. 空批次是正常的（长轮询超时返回空），不要当异常，也不要忙等 ——
       `fetch_updates` 本身就会阻塞，循环里**不需要 sleep**。
    """

    def __init__(
        self,
        channel: ClawBotChannel,
        handler: MessageHandler,
        *,
        poll_error_backoff: float = 5.0,
    ) -> None:
        self._channel = channel
        self._handler = handler
        self._poll_error_backoff = poll_error_backoff
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return  # 已在跑，重复 start 不叠加线程（两个轮询会重复收消息）
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="clawbot-worker", daemon=True
        )
        self._thread.start()

    def stop(self, *, timeout: float = 10.0) -> None:
        """停循环。置停止位后 join，**最多等一次在途 `fetch_updates` 的时间**。

        ⚠️ 长轮询一次 hold 最多 ~35s，无法中断一个正在阻塞的 socket —— 所以
        `join(timeout)` 就是给这个上限的闸门；`_stop` 让循环在**本次 fetch 返回后**
        立刻退出，不会再多拉一轮。
        """
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
            if not thread.is_alive():
                self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------
    # 循环
    # ------------------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                messages = self._channel.fetch_updates()
            except ChannelError as e:
                # 拉取失败（网络/鉴权/未绑定）不能把循环打死；退避一下再拉。
                # 用 `_stop.wait` 而不是 `time.sleep` —— 这样 stop() 能立刻打断退避。
                log.warning("拉取消息失败，%ss 后重试：%s", self._poll_error_backoff, e)
                self._stop.wait(self._poll_error_backoff)
                continue
            except Exception:
                # 兜底：任何没预料到的异常同样不能让循环死掉
                log.exception("拉取消息时发生未预期异常")
                self._stop.wait(self._poll_error_backoff)
                continue

            # 空批次是正常的（长轮询超时返回空），不当异常，也不 sleep ——
            # fetch_updates 本身会阻塞，天然限速。
            for msg in messages:
                if self._stop.is_set():
                    break
                self._handle_one(msg)

    def _handle_one(self, msg: InboundMessage) -> None:
        """处理一条消息：handler 抛异常**只影响这一条**，不拖垮循环。"""
        try:
            reply = self._handler(msg)
        except Exception:
            # 要点 3：静默失败比报错糟得多 —— 处理失败要主动回复用户"出错了"。
            log.exception("处理消息失败（peer=%s）", msg.peer_id)
            self._safe_send(
                msg,
                "抱歉，处理这条消息时出错了，请稍后再试。",
            )
            return

        if reply:
            self._safe_send(msg, reply)

    def _safe_send(self, msg: InboundMessage, text: str) -> None:
        """发回复；发送本身失败也只能记日志，不能再往上抛把循环带崩。"""
        try:
            # ★ context_token 必须原样回传（来自那条入站消息）
            self._channel.send_text(
                msg.peer_id, text, context_token=msg.context_token
            )
        except Exception:
            log.exception("发送回复失败（peer=%s）", msg.peer_id)


#: 进程级单例（与 `llm/client.py` 的 `get_client()` 同一套路）。
#: 原因：`ClawBotWorker` 只能有一个在跑 —— 两个轮询会**重复收同一批消息**，
#: 用户就会收到两条一样的回复。
_CHANNEL: Optional[ClawBotChannel] = None


def get_channel(*, transport: Optional[httpx.BaseTransport] = None) -> ClawBotChannel:
    """工厂。业务层与路由**必须**走它，不要自己 `ClawBotChannel()`。

    ⚠️ 未绑定时**不抛异常**（和 `get_client()` 不同）—— 没绑定是正常状态
    （用户还没扫码），`is_bound()` 会返回 False，调用方据此决定怎么提示。
    """
    global _CHANNEL
    if _CHANNEL is None:
        _CHANNEL = ClawBotChannel(transport=transport)
    return _CHANNEL


def reset_channel_cache() -> None:
    """清掉单例。**只在测试里用**。"""
    global _CHANNEL
    _CHANNEL = None
