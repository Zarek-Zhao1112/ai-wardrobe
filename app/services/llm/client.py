"""跟模型说话的那一层：协议 + OpenAI 兼容实现 + 结构化输出降级链。

**为什么需要这一层**：要接的可能是豆包 / DeepSeek / OpenAI / vLLM / 本地 Ollama，
它们的 HTTP 细节一致但**能力不一致** —— 有的支持 `json_schema`（能强制输出结构），
有的只支持 `json_mode`，有的两者都不支持、只能纯文本里抽 JSON。
把这件事关在这一层里，业务代码（打标 / 排序）就不用关心供应商差异。

---
✅ **契约在 W0 冻结，实现于 W1（本文件已无 `NotImplementedError`）。**
⚠️ 验证程度要说清楚：本机**没有 API key、也不联网**，所以「能力探测 + 降级链」
是用 `httpx.MockTransport` **伪造 HTTP 响应**验的，没有对真实供应商端点跑过。
第一次接真 key 时，请重点确认 `capabilities()` 的探测结论与真实服务端行为一致。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx

from ... import config

# 这些状态码通常意味着"服务端不认这个 response_format 参数"，据此触发降级。
# 其余 4xx/5xx（401 鉴权 / 429 限流 / 5xx 故障）是调用本身失败，直接抛 LLMError，不做降级。
#: 服务端"不认这个 response_format 参数"的状态码 —— 收到这些才值得降级重试。
#:
#: ⚠️ **404 刻意不在里面**（W4 审查的结论）。404 的常见原因是 **`base_url` 配错**
#: （路径多写/少写一段），那是**配置错误**，不是"模型不支持结构化输出"。
#: 若把它归入这里，会连跑三级降级、最后报「所有结构化输出策略都被拒绝」——
#: **把配置问题伪装成模型能力问题**，排查方向直接跑偏。让它走下面的普通
#: `LLMError`（带 HTTP 状态码与响应体），一眼能看出是地址不对。
_REJECT_STATUS = {400, 415, 422}

#: 单次调用的输出 token 上限。
#:
#: ⚠️ **为什么必须显式给**（2026-10-03 实测）：省略 `max_tokens` 时，推理模型
#: 会把额度几乎全部花在 reasoning 上—— 713 字符的 prompt 触发了 5195 个
#: completion_token（7857 字思考），耗时 52s，而正文只有几十字。
#: 给了上限后被截断，**反而更快**（约 4s出正文）。
#:
#: 2048 足够本项目所有场景：打标输出受控字段、推荐只排 25 套并写 20~30 字理由。
MAX_OUTPUT_TOKENS = 2048

#: 能力探测用的输出上限。**不能给 1** —— 推理族模型有最小输出预算，
#: 1 个 token 会被服务端拒绝，于是"探测失败"被误读成"不支持结构化输出"。
PROBE_OUTPUT_TOKENS = 32

#: 本机直连走高强度思考时的输出上限。
#: 思考与正文**共用**这一份额度：DeepSeek 开 thinking 后，2048 很容易被 reasoning
#: 吃光、正文返回空串 —— 看起来像"模型没说话"。给到 8192 留足余量；
#: 计费按实际用量，不按上限，所以这个数字只是天花板。
STRONG_OUTPUT_TOKENS = 8192

#: 参数契约变了的模型族：`max_tokens` 换成 `max_completion_tokens`，
#: 且 temperature 只接受默认值 1（传 0 直接 400）。
#:
#: ⚠️ 刻意按**模型名前缀**判断而不是按供应商判断：同一个 base_url 下
#: 可能既有 gpt-4o（旧参数）又有 gpt-6（新参数），只有模型名能区分。
#: 不在这个前缀里的（deepseek / doubao / ollama / gpt-4o）保持原行为。
_REASONING_FAMILY = ("gpt-5", "gpt-6", "o1", "o3", "o4")


def _is_reasoning_model(model: str) -> bool:
    return (model or "").strip().lower().startswith(_REASONING_FAMILY)


def _token_fields(
    model: str,
    *,
    temperature: float | None = None,
    limit: int = MAX_OUTPUT_TOKENS,
) -> dict[str, Any]:
    """按模型族给出"输出上限 + 采样温度"这组参数。

    推理族只回 `max_completion_tokens`（不带 temperature）；
    其余模型回 `max_tokens`，有传温度才带上。
    """
    if _is_reasoning_model(model):
        return {"max_completion_tokens": limit}
    fields: dict[str, Any] = {"max_tokens": limit}
    if temperature is not None:
        fields["temperature"] = temperature
    return fields


def _proxy_url() -> str | None:
    """模型调用专用的显式代理，来自 `LLM_PROXY`。

    **为什么必须显式传**：httpx 默认 `trust_env=True` 会读 `HTTPS_PROXY`，
    而本机那个值被宿主程序设成了内部白名单代理 —— 连 OpenAI 只会拿到 502。
    `LLM_PROXY` 只在模型这一层生效，不污染进程里其他请求；留空则完全
    沿用 httpx 原有行为（系统环境变量 / 直连）。
    """
    return (os.environ.get("LLM_PROXY") or "").strip() or None


class _StrategyRejected(RuntimeError):
    """内部信号：服务端拒绝了当前这级结构化输出参数，提示 `chat_json` 降一级重试。

    刻意不对外暴露 —— 调用方只该认识 `LLMError`。
    """


class LLMError(RuntimeError):
    """模型调用失败：网络 / 鉴权 / 超时 / 返回不可解析。

    业务层捕获它做降级（例如打标失败就把单品留在 `tagging_status='pending'`，
    而不是让整个请求 500）。**不要把底层异常原样抛出去** —— 调用方只该认识这一个类型。
    """

    def __init__(self, message: str, *, code: str = "model_error", retryable: bool = True):
        super().__init__(message)
        self.code = code
        self.retryable = retryable

    def detail(self) -> dict[str, Any] | str:
        # 旧错误保留字符串契约；新增服务错误只暴露可操作的安全文案。
        if self.code == "model_error":
            return f"模型不可用：{self}"
        return {"code": self.code, "message": str(self), "retryable": self.retryable}


def _service_error(status: int) -> LLMError:
    if status == 402:
        return LLMError("AI识别服务余额或额度不足，请补充额度后重新检查服务。", code="insufficient_balance", retryable=False)
    if status in (401, 403):
        return LLMError("AI识别服务鉴权失败，请检查模型配置。", code="model_auth", retryable=False)
    if status == 429:
        return LLMError("AI识别请求过于频繁，请稍后重试。", code="model_rate_limit")
    return LLMError(f"AI识别服务暂不可用（HTTP {status}），请稍后重试。", code="model_unavailable")


@dataclass(frozen=True)
class Capabilities:
    """供应商能力探测结果。**第一次调用会被缓存**，不要在每次请求里重新探测。

    `chat_json` 的降级链顺序由它决定（能 schema 就直接上 schema）。
    """

    model: str
    supports_json_schema: bool = False
    supports_json_mode: bool = False

    def best_strategy(self) -> str:
        """选一个最靠谱的结构化输出策略。

        返回 `"json_schema"` | `"json_mode"` | `"text"`，按可靠性递减。
        """
        if self.supports_json_schema:
            return "json_schema"
        if self.supports_json_mode:
            return "json_mode"
        return "text"


@dataclass
class ChatResult:
    """一次调用的原始产物。调用方一般不用它，走 `chat_json` / `chat_text` 即可。

    留着 txt 是为了排查"模型明明返回了内容，为什么解析不出来"。
    """

    text: str
    strategy: str                      # 实际用了哪条路径（见 Capabilities.best_strategy）
    model: str
    raw: dict[str, Any] = field(default_factory=dict)   # 原始响应体，排障用


@runtime_checkable
class LLMClient(Protocol):
    """**这是冻结的跨模块边界。** 业务层只依赖它，不依赖任何具体供应商。

    谁在用它：
        - `services/tagging.py`  —— 视觉打标（用 `chat_json`）
        - `services/recommend.py` —— 给搭配写理由（用 `chat_json`）
    """

    def capabilities(self) -> Capabilities:
        """供应商能力。首次调用做探测，之后走缓存。"""
        ...

    def chat_text(
        self,
        messages: list[dict[str, Any]],
        *,
        timeout: float = 60.0,
        temperature: float = 0.2,
    ) -> str:
        """要一段纯文本。模板：
            [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]
        """
        ...

    def chat_json(
        self,
        messages: list[dict[str, Any]],
        *,
        schema: dict[str, Any] | None = None,
        timeout: float = 60.0,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        """要一个**已解析好的 dict**。

        内部按 `json_schema → json_mode → 纯文本里抽 JSON` 依次降级
        （从哪一级开始由 `capabilities().best_strategy()` 决定）。

        **契约**：
        - 成功 → 返回 dict
        - 无论如何都拿不到可解析的对象 → 抛 `LLMError`（不要返回 `{}` 让调用方误判成"模型说没有"）
        - ⚠️ **本层不做词表归一化**。返回什么就是什么，`resolve_enum` 由调用方做 ——
          因为"合法的取值集合"是业务知识（在 `taxonomy.py`），不该塞进这一层。
        """
        ...


class OpenAICompatClient:
    """OpenAI 兼容实现。覆盖 OpenAI / DeepSeek / 豆包 / vLLM / Ollama。

    实现要点（取自设计文档 §10，W1 已逐条落实）：

    1. `capabilities()` 探测后缓存；普通探测失败退化为不支持。
       额度不足立即抛安全服务错误，不把服务故障缓存为能力缺失。
    2. `chat_json()` 逐级兜底：`json_schema` 被服务端拒绝 → `json_mode` → 纯文本 + 正则抽。
    3. 抽出来的东西过 `json.loads`，失败抛 `LLMError` —— **绝不返回 `{}`**，
       那会让调用方误判成"模型说没有"。
    4. 一次性失败不重试（宁快勿拖，让调用方去降级）。
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        *,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
        device_direct: bool = False,
    ) -> None:
        self.base_url = (base_url if base_url is not None else config.LLM_BASE_URL).rstrip("/")
        self.api_key = api_key if api_key is not None else config.LLM_API_KEY
        self.model = model if model is not None else config.LLM_MODEL
        self.timeout = timeout
        self._caps: Capabilities | None = None
        # transport 只为可测试性存在：注入 MockTransport 才能在不联网的情况下验证降级链。
        # 生产路径传 None，走 httpx 默认实现。
        self._transport = transport
        self._device_direct = device_direct

    # ------------------------------------------------------------------
    # HTTP 细节
    # ------------------------------------------------------------------
    def _endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _generation_fields(
        self,
        temperature: float,
        limit: int | None = None,
        *,
        strong: bool = True,
    ) -> dict[str, Any]:
        """按通道给出"输出上限 + 采样温度 + 思考强度"。

        **两条通道都走高强度**（2026-11-17 定）：两家都只有一档高能力模型，文字分析
        和照片识别共用它，强度也不分档 —— OpenAI 发 `reasoning_effort=high`，
        DeepSeek 发 `thinking={"type":"enabled"}` + `reasoning_effort=high`。

        - 本机直连（用户自己的 Key，`device_direct=True`）：按供应商显式给。
        - `.env` 直配（电脑上跑的那条）：推理族模型也给 `reasoning_effort=high`；
          非推理族保持原样，避免往不认这个字段的端点塞参数。

        实测 gpt-6-luna 高强度打标 5.6s / reasoning 84 token（关思考 3.7s / 0 token），
        质量换这点延迟是划算的。

        ⚠️ 例外是能力探测：它只有 `PROBE_OUTPUT_TOKENS` 的预算，开着思考时预算可能被
        reasoning 吃光、响应里没有正文，有的服务端还会因为预算过小而直接 400；
        探测只看状态码，一 400 就成了"不支持结构化输出"，静默退化到纯文本抽 JSON。
        所以探测显式传 `strong=False`。
        """
        from .channel import DIRECT_MODELS, PROVIDERS

        strength: dict[str, Any] = {}
        if self._device_direct:
            if self.base_url == PROVIDERS["openai"] and self.model == DIRECT_MODELS["openai"]:
                strength["reasoning_effort"] = "high" if strong else "none"
            elif self.base_url == PROVIDERS["deepseek"] and self.model == DIRECT_MODELS["deepseek"]:
                strength["thinking"] = {"type": "enabled" if strong else "disabled"}
                if strong:
                    strength["reasoning_effort"] = "high"
        elif config.LLM_THINKING_DISABLED:
            strength["thinking"] = {"type": "disabled"}
        elif strong and _is_reasoning_model(self.model):
            strength["reasoning_effort"] = "high"

        if limit is None:
            high = strength.get("reasoning_effort") == "high" or strength.get("thinking", {}).get("type") == "enabled"
            limit = STRONG_OUTPUT_TOKENS if high else MAX_OUTPUT_TOKENS
        return {**_token_fields(self.model, temperature=temperature, limit=limit), **strength}

    def _new_client(self, timeout: float) -> httpx.Client:
        # transport 只给测试注入 MockTransport 用；httpx 不允许它和 proxy 同时出现
        if self._transport is not None:
            return httpx.Client(transport=self._transport, timeout=timeout)
        if self._device_direct:
            return httpx.Client(timeout=timeout, trust_env=False)
        proxy = _proxy_url()
        if proxy:
            return httpx.Client(timeout=timeout, proxy=proxy)
        return httpx.Client(timeout=timeout)

    def _post(self, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        """发一次 chat/completions。**只发一次，失败不重试** —— 降级交给调用方。"""
        try:
            with self._new_client(timeout) as client:
                resp = client.post(self._endpoint(), headers=self._headers(), json=payload)
        except httpx.TimeoutException as e:
            raise LLMError("AI识别服务响应超时，请稍后重试。", code="model_timeout") from e
        except httpx.HTTPError as e:
            # 连接 / DNS / 超时等底层异常一律收敛成 LLMError，调用方只需认识这一种类型
            raise LLMError("暂时连不上AI识别服务，请检查网络后重试。", code="model_network") from e

        if resp.status_code in _REJECT_STATUS:
            # 多半是"不认这个 response_format 参数"，用内部信号让 chat_json 降级
            raise _StrategyRejected(
                f"服务端拒绝该输出模式（HTTP {resp.status_code}）"
            )
        if resp.status_code >= 400:
            raise _service_error(resp.status_code)

        try:
            return resp.json()
        except ValueError as e:
            raise LLMError("模型响应的 body 不是合法 JSON") from e

    @staticmethod
    def _content_of(body: dict[str, Any]) -> str:
        """从 OpenAI 兼容响应里取出首个 choice 的文本内容。"""
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMError("模型响应里没有 choices")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise LLMError("模型响应里没有可用的文本内容")
        return content

    @staticmethod
    def _loads_object(text: str) -> dict[str, Any] | None:
        """把文本解析成 dict；不是对象（或解析失败）就返回 None。

        单独抽出来是为了让三级抽取共用同一套"必须过 json.loads"的判定。
        """
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            return None
        return data if isinstance(data, dict) else None

    @classmethod
    def _parse_json_text(cls, text: str) -> dict[str, Any]:
        """从自由文本里抽一个 JSON 对象：先整体，再 ```json 代码块，最后首个 `{` 到末个 `}`。"""
        # 1) 模型守规矩时整段就是 JSON
        data = cls._loads_object(text.strip())
        if data is not None:
            return data
        # 2) 常见的 markdown 代码块包裹
        fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S | re.I)
        if fenced:
            data = cls._loads_object(fenced.group(1).strip())
            if data is not None:
                return data
        # 3) 最宽松：首个 { 到末个 }
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            data = cls._loads_object(text[start : end + 1])
            if data is not None:
                return data
        raise LLMError("模型返回的内容无法解析成 JSON 对象")

    # ------------------------------------------------------------------
    # 能力探测
    # ------------------------------------------------------------------
    def _probe(self, response_format: dict[str, Any]) -> bool:
        """发一个极小请求，看服务端是否接受该 response_format。"""
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": "Return an empty JSON object."}],
            "response_format": response_format,
            **self._generation_fields(0, PROBE_OUTPUT_TOKENS, strong=False),
        }
        try:
            with self._new_client(self.timeout) as client:
                resp = client.post(self._endpoint(), headers=self._headers(), json=payload)
        except Exception:
            # 探测失败（无网 / 鉴权错 / 超时）绝不能把异常抛给调用方；
            # 退化成"不支持"最安全：chat_json 会走纯文本，最多损失结构化约束。
            return False
        # 余额不足是服务状态，不能当作结构化输出不支持并继续消耗请求。
        if resp.status_code == 402:
            raise _service_error(resp.status_code)
        return resp.status_code == 200

    def capabilities(self) -> Capabilities:
        if self._caps is not None:
            return self._caps
        schema_ok = self._probe(
            {
                "type": "json_schema",
                # ⚠️ strict 模式要求 `additionalProperties: false` 且 `required` 列全字段。
                # 只写 `{"type":"object"}` 会被 OpenAI 判 400 —— 于是"支持 schema"被
                # 误判成"不支持"，静默退化成纯文本抽 JSON（能用但更脆）。
                "json_schema": {
                    "name": "probe",
                    "schema": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                        "additionalProperties": False,
                    },
                    "strict": True,
                },
            }
        )
        # 接受 json_schema 的供应商必然也接受 json_object，省掉一次探测往返；
        # 只在 schema 被拒时才需要单独确认 json_mode。
        mode_ok = True if schema_ok else self._probe({"type": "json_object"})
        self._caps = Capabilities(
            model=self.model,
            supports_json_schema=schema_ok,
            supports_json_mode=mode_ok,
        )
        return self._caps

    def _post_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        strategy: str,
        schema: dict[str, Any] | None,
        timeout: float,
        temperature: float,
    ) -> dict[str, Any]:
        # ⚠️ 输出额度**必须给上限**，不能省略。
        # 省略时服务端用默认值，而推理模型的 reasoning 与正文**共用**这个额度 ——
        # 实测：不传上限时它为一个 713 字符的 prompt 写了 7857 字思考，耗时 52s；
        # 给上限后被截断，反而能在几秒内交出正文。
        # 字段名（max_tokens / max_completion_tokens）与 temperature 能否传，
        # 按模型族切换，见 `_token_fields`。
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            **self._generation_fields(temperature),
        }
        if strategy == "json_schema":
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "result",
                    # 调用方没给 schema 时也给一个宽松对象约束，行为上仍是结构化模式
                    "schema": schema if schema is not None else {"type": "object"},
                    "strict": True,
                },
            }
        elif strategy == "json_mode":
            payload["response_format"] = {"type": "json_object"}
        # strategy == "text"：不加 response_format，让模型自由输出，稍后正则抽
        return self._post(payload, timeout=timeout)

    def chat_text(
        self,
        messages: list[dict[str, Any]],
        *,
        timeout: float = 60.0,
        temperature: float = 0.2,
    ) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            # 与 `_post_chat` 同理：给上限 + 抑制思考，理由见 MAX_OUTPUT_TOKENS 的注释
            **self._generation_fields(temperature),
        }
        try:
            body = self._post(payload, timeout=timeout)
        except _StrategyRejected as e:
            # 纯文本没有可降级的余地，内部信号在这里统一收敛成 LLMError
            raise LLMError(str(e)) from e
        return self._content_of(body)

    def chat_json(
        self,
        messages: list[dict[str, Any]],
        *,
        schema: dict[str, Any] | None = None,
        timeout: float = 60.0,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        ladder = ["json_schema", "json_mode", "text"]
        # 起点由能力探测决定；探测失败会退化成 text，所以这里一定有值
        start = self.capabilities().best_strategy()
        last_error: Exception | None = None

        for strategy in ladder[ladder.index(start) :]:
            try:
                body = self._post_chat(
                    messages,
                    strategy=strategy,
                    schema=schema,
                    timeout=timeout,
                    temperature=temperature,
                )
            except _StrategyRejected as e:
                # 服务端不认这一级的 response_format → 降一级再试
                last_error = e
                continue

            try:
                return self._parse_json_text(self._content_of(body))
            except LLMError as e:
                # 响应拿到了却解析不出对象：再降级也没意义（模型已用最强约束），直接失败
                raise LLMError(f"策略 {strategy} 未能返回可解析的 JSON 对象") from e

        # 每一级都被服务端拒绝 → 抛 LLMError（不返回 {}，否则调用方会误判成"模型说没有"）
        raise LLMError(f"所有结构化输出策略都被拒绝：{last_error}") from last_error


#: 进程级客户端缓存。
#:
#: ⚠️ **为什么必须缓存**：`get_client()` 每次新建对象，而 `capabilities()` 的探测
#: 结果（`self._caps`）是**挂在实例上的**。不缓存的话，每次推荐都要重发 1~2 个
#: 探测请求 —— 实测一次推荐会发出 **3 个 HTTP 请求**（探测 ×2 + 正式 ×1），
#: 既白烧配额又加延迟；在限流时更糟（探测被拒 → 误判"不支持" → 多发一次）。
#: 按 `vision` 分开缓存，视觉与文本用不同模型名，不能混。
_CLIENTS: dict[bool, "OpenAICompatClient"] = {}
_DIRECT_CLIENTS: dict[bool, "OpenAICompatClient"] = {}


def get_client(*, vision: bool = False) -> LLMClient:
    """工厂：按配置返回客户端（**进程内单例**）。

    `vision=True` 时用 `VISION_MODEL`（可与文本模型不同供应商）。

    ⚠️ **未配置 key 时抛 `LLMError`，不要抛别的类型** —— 让调用方用同一套
    try/except 处理"没配模型"和"模型炸了"两种情况。

    也可以在这里返回别的实现（例如测试用的假客户端），所以业务层**必须**走这个工厂，
    不要自己 `OpenAICompatClient()`。

    **单例的原因**：能力探测结果存在实例上，不复用就等于每次调用都重新探测。
    测试要隔离时用 `reset_client_cache()`，不要绕过工厂直接 new。
    """
    from . import channel
    from .desktop import ACCOUNT, DesktopClient, load_settings
    state = channel.load()
    mode = state.get("mode")
    if mode == "direct":
        current = channel.profile(state)
        if state.get("invalid"):
            raise LLMError("API 直连配置无法读取，请重新保存。", code="model_config")
        if current:
            model = current["vision_model" if vision else "model"]
            if not model:
                # 一个模型管两用之后，视觉与文本取的是同一个值；这里只是防御性兜底。
                raise LLMError("模型没有保存成功，请重新保存 API 直连设置。", code="model_config")
            base_url = channel.PROVIDERS[current["provider"]]
            cached = _DIRECT_CLIENTS.get(vision)
            if cached and (cached.model, cached.base_url, cached.api_key) == (model, base_url, current["key"]):
                return cached
            client = OpenAICompatClient(base_url, current["key"], model, device_direct=True)
            _DIRECT_CLIENTS[vision] = client
            return client
    else:
        settings = load_settings()
        if settings is not None:
            return DesktopClient({**settings, "engine": ACCOUNT} if mode == "account" else settings)
        if mode == "account":
            raise LLMError("请在设置里连接电脑中转。", code="bridge_config")
    # LLM_READY / VISION_READY 已经把 base_url + key + model 三件套都验过了
    ready = config.VISION_READY if vision else config.LLM_READY
    if not ready:
        model_var = "VISION_MODEL" if vision else "LLM_MODEL"
        raise LLMError(
            f"未配置模型：需要 LLM_BASE_URL / LLM_API_KEY / {model_var} 三者齐全"
        )

    cached = _CLIENTS.get(vision)
    if cached is not None and cached.model == (
        config.VISION_MODEL if vision else config.LLM_MODEL
    ):
        return cached

    client = OpenAICompatClient(
        model=config.VISION_MODEL if vision else config.LLM_MODEL
    )
    _CLIENTS[vision] = client
    return client


def reset_client_cache() -> None:
    """清空客户端缓存。**只在测试里用**（改了 `.env` 或要重新探测时）。"""
    _CLIENTS.clear()
    _DIRECT_CLIENTS.clear()


def model_ready(*, vision: bool = False) -> bool:
    """§19：已配置中转也可调用；离线在真正请求时返回提示。"""
    from . import channel
    from .desktop import settings_path
    state = channel.load()
    if state.get("invalid"):
        return False
    if state.get("mode") == "direct":
        current = channel.profile(state)
        if current:
            return bool(current["vision_model" if vision else "model"])
    elif state.get("mode") == "account" or settings_path().exists():
        return settings_path().exists()
    return config.VISION_READY if vision else config.LLM_READY
