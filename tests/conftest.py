"""pytest 全局夹具与测试环境隔离。

⚠️ **`WARDROBE_DB` 必须在这里设，且必须在任何 `import app` 之前。**
`app/db.py` 在 import 期就把 `DB_PATH` 定死了，所以：

- `conftest.py` 是 pytest 最先导入的文件 → 这里是唯一可靠的位置
- 让每个测试文件自己设是**不可靠**的（谁先被收集谁说了算），而且**漏设的那个
  会打到 `data/wardrobe.db`** —— 那是用户的真实衣橱。这里设一道兜底，
  让"忘了设"变成无害而不是灾难。

临时库放**系统临时目录**（不是项目内）：跑完不用清理，也不会在仓库里留残留目录。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="wardrobe_test_"))
os.environ["WARDROBE_DB"] = str(_TMP / "test.db")

# 模型配置同理隔离：`config.py` 在 import 时会把项目根目录的 `.env` 读进来，
# 那样"测试结果取决于开发者本机有没有配 key" —— 不可接受（比如
# `test_without_key_is_503_not_500` 就会因开发机配了 key 而变红）。
# 显式置空，让"未配模型"成为测试里的**确定状态**；
# 需要模型的测试一律注入 `FakeLLM`，不碰真实网络。
for _key in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "VISION_MODEL"):
    os.environ[_key] = ""

# 微信通道的后台 worker **必须关掉**：`main.py` 的 lifespan 会启动它，
# 而每个起 `TestClient` 的测试都会触发 lifespan —— 不关就会拉一堆后台线程
# 去戳微信（未绑定时每 5 秒重试一次），测试变慢、日志变脏。
# 关掉后其他一切照常（协议层用 `httpx.MockTransport` 单独测）。
os.environ["CLAWBOT_WORKER_ENABLED"] = "false"

# 知识库（P4）**兜底防线**：指向一个不存在的路径。
#
# `knowledge/` 包目前只接受**显式传入**的 vault 路径（测试都用合成库），
# 但将来一旦有人让它去读 `config.KNOWLEDGE_VAULT_DIR`，
# 这一行能保证"测试永远摸不到使用者的真实知识库"——
# 跟上面 `WARDROBE_DB` 是同一个思路：让"忘了隔离"变成无害而不是灾难。
os.environ["KNOWLEDGE_VAULT_DIR"] = str(_TMP / "no_such_vault")


@pytest.fixture(scope="session", autouse=True)
def _db_ready():
    from app import db

    db.init(verbose=False)
    yield


@pytest.fixture(scope="session")
def uid() -> str:
    from app import db

    return db.get_default_user_id()


class FakeLLM:
    """假模型客户端：按队列返回预设响应，并记录收到的请求。

    **为什么必须有它**：这个项目没有 API key、不联网，所以 AI 相关模块
    只能靠伪造客户端验证。它同时是"确定性"的来源 —— 真模型每次输出都不同，
    没法写断言。
    """

    def __init__(
        self,
        responses: Optional[list[Any]] = None,
        *,
        error: Optional[Exception] = None,
    ) -> None:
        self.responses = list(responses or [])
        self.error = error
        self.calls: list[dict] = []

    def capabilities(self):
        from app.services.llm.client import Capabilities

        return Capabilities(model="fake", supports_json_schema=True, supports_json_mode=True)

    def chat_text(self, messages, *, timeout: float = 60.0, temperature: float = 0.2) -> str:
        self.calls.append({"messages": messages, "kind": "text"})
        if self.error is not None:
            raise self.error
        return str(self.responses.pop(0)) if self.responses else ""

    def chat_json(self, messages, *, schema=None, timeout: float = 60.0, temperature: float = 0.0):
        self.calls.append(
            {"messages": messages, "schema": schema, "temperature": temperature, "kind": "json"}
        )
        if self.error is not None:
            raise self.error
        if not self.responses:
            raise AssertionError("FakeLLM 没有更多预设响应了 —— 检查调用次数")
        return self.responses.pop(0)


@pytest.fixture
def fake_llm():
    """工厂夹具：`fake_llm([{...}])` 得到一个带预设响应的假客户端。"""

    def _make(responses=None, *, error=None) -> FakeLLM:
        return FakeLLM(responses, error=error)

    return _make
