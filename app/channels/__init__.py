"""通道层：把外部平台的消息变成项目内部认识的东西。

**这一层的存在理由**：微信只是入口。业务逻辑全在 `services/` 里，
换通道（企业微信 / Telegram / 命令行）时业务层一行不用改。

职责边界（硬约束，别打破）：

    channels/clawbot.py     认 iLink 协议、token、长轮询、媒体解密
                            **不认任何业务概念**（不知道"单品""搭配"）

    services/conversation.py 认意图、会话状态、怎么调业务
                            **不认任何协议细节**（不知道 context_token、不知道长轮询）

自检判据：`conversation.py` 里 grep 不到 `httpx` / `token` / `ilink` 任何字样。
"""

from __future__ import annotations

from .clawbot import (
    ClawBotChannel,
    ClawBotSession,
    ClawBotWorker,
    ChannelError,
    InboundMessage,
    LoginStatus,
    LoginTicket,
    get_channel,
    reset_channel_cache,
)

__all__ = [
    "ClawBotChannel",
    "ClawBotSession",
    "ClawBotWorker",
    "ChannelError",
    "InboundMessage",
    "LoginStatus",
    "LoginTicket",
    "get_channel",
    "reset_channel_cache",
]
