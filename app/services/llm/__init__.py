"""模型接入层（供应商无关）。

对外只暴露这几样：

    from ..llm import LLMClient, OpenAICompatClient, get_client, LLMError, AliasMap

职责边界：
    client.py   —— **怎么跟模型说话**：协议 + HTTP + 降级链
    prompts.py  —— **说什么**：把衣橱裁成紧凑行 + 短别名映射

刻意不放在这个包里的：
    - MMR 多样性重排（在 `services/recommend.py`，它是"选哪些"的业务决策）
    - 搭配里 item_id 的最终归属校验（复用 `services/wear.py` 已有的反查校验）

---

✅ **两个文件都已实现**：`client.py` 在 W1 填、`prompts.py` 在 W2 填，包里已无 `NotImplementedError`。
✅ **已对真实端点跑通**（2026-10-03 配了 DeepSeek）：降级链实测退到「纯文本抽 JSON」
   ——该端点 `json_schema` / `json_mode` 都不支持。降级设计如设计般生效。
⚠️ 已知限制：`get_client()` 返回**进程级单例**（能力探测结果挂在实例上，
   不复用就等于每次调用都重新探测，白烧配额）。改 `.env` 后要调
   `reset_client_cache()`，或重启服务。
"""

from __future__ import annotations

from .client import (
    Capabilities,
    LLMClient,
    LLMError,
    OpenAICompatClient,
    get_client,
    reset_client_cache,
)
from .prompts import AliasMap

__all__ = [
    "AliasMap",
    "Capabilities",
    "LLMClient",
    "LLMError",
    "OpenAICompatClient",
    "get_client",
    "reset_client_cache",
]
