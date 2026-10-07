"""微信通道的绑定与状态接口。

**只有这几件事**：扫码绑定、查绑定状态、解绑。
真正的消息收发由 `ClawBotWorker` 在后台线程里做（不需要 HTTP 接口）。

⚠️ **`/status` 会挂最多 30 秒** —— iLink 的查状态接口本身就是长轮询
（实测：有效二维码在 30.1s 后才返回 `wait`）。所以前端必须按**长轮询**用：
发一个请求、等它返回、再发下一个。**不要**每 2 秒调一次 —— 那会堆积请求。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..channels import ChannelError, get_channel

router = APIRouter(prefix="/channel/wechat", tags=["channel"])


def _qr_svg(content: str) -> str:
    """把扫码内容渲染成**内联 SVG**。

    用 SVG 而不是 PNG：`segno` 是纯 Python、**零额外依赖**（PNG 要拉 Pillow）。
    而且 SVG 直接嵌进 HTML 就行，不用走图片接口。
    """
    import segno

    qr = segno.make(content, error="m")
    return qr.svg_inline(scale=4, dark="#1a1a1a", light="#ffffff", border=2)


@router.get("")
def channel_status() -> dict:
    """当前绑定状态。前端用它决定显示"扫码绑定"还是"已连接"。"""
    ch = get_channel()
    session = ch.load_session()
    return {
        "channel": "clawbot",
        "bound": session is not None,
        "bot_id": session.bot_id if session else None,
        "saved_at": session.saved_at if session else None,
    }


@router.get("/qrcode")
def start_login() -> dict:
    """拿一个新的登录二维码。**不需要鉴权**（还没登录自然没有 token）。

    ⚠️ 二维码约 120 秒过期。过期后重新调这个接口即可（会作废旧的）。
    """
    try:
        ticket = get_channel().start_login()
    except ChannelError as exc:
        raise HTTPException(502, f"拿不到二维码：{exc}") from exc
    return {
        "qrcode": ticket.qrcode,
        "qr_content": ticket.qr_content,
        "qr_svg": _qr_svg(ticket.qr_content),
        "hint": "用手机微信扫这个码，然后点授权。",
    }


@router.get("/status")
def login_status(qrcode: str = Query(..., description="start_login 返回的 qrcode")) -> dict:
    """查扫码进度。

    ⚠️ **本接口会挂最多 30 秒**（上游是长轮询）。前端按长轮询用，别密集轮询。

    返回 `state`：`wait`（还没扫）/ `scanned`（扫了没确认）/
    `confirmed`（成功，token 已存下）/ `expired`（二维码过期了，要重新拿）
    """
    try:
        status = get_channel().poll_login(qrcode)
    except ChannelError as exc:
        raise HTTPException(502, f"查绑定状态失败：{exc}") from exc
    return {
        "state": status.state,
        "bound": status.session is not None,
        "bot_id": status.session.bot_id if status.session else None,
    }


@router.delete("")
def unbind() -> dict:
    """解绑：删掉本地会话文件。

    解绑后后台 worker 会自动停在"未绑定"状态（每 5 秒重试一次），
    重新扫码即恢复 —— **不需要重启服务**。
    """
    get_channel().clear_session()
    return {"ok": True, "bound": False}


@router.post("/send")
def send_test(
    peer_id: str = Query(..., description="要发给谁（形如 xxx@im.wechat）"),
    text: str = Query("这是一条测试消息", description="内容"),
    context_token: str = Query(
        "",
        description="必须原样来自那条入站消息；**留空通常发不出去**，这是协议要求",
    ),
) -> dict:
    """手动发一条消息（排障用 —— 验证"发送"这条链路是否通）。

    ⚠️ `context_token` 是协议硬要求：不回传则消息**关联不到会话窗口**，
    而且服务端不报错。所以这个接口主要用于**排查**，平时用不上
    （正常回复由 worker 自动带上 token）。
    """
    try:
        get_channel().send_text(peer_id, text, context_token=context_token)
    except ChannelError as exc:
        raise HTTPException(502, f"发送失败：{exc}") from exc
    return {"ok": True, "to": peer_id}
