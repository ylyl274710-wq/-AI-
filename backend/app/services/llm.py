"""大模型接入层（OpenAI 兼容）。

设计要点（对应文档难点 2 / 难点 11 / 第七章「LLM 服务失败」）：
1. 主模型失败自动切备用模型（LLM_FALLBACK_*），两者都失败则抛 LLMUnavailable，
   由上层降级到本地规则引擎，绝不让接口 500 把用户卡死。
2. 结构化输出：农业诊断必须返回固定 schema，不能是一段散文，
   否则后面的置信度计算、规则校验都无从下手。
3. 所有调用都记录 model / usage / 是否降级，便于成本统计（难点 11）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from app.core.config import settings

logger = logging.getLogger(__name__)

try:  # openai SDK 是可选依赖，缺失时整层降级
    from openai import (APIConnectionError, APIStatusError, APITimeoutError,
                        AsyncOpenAI, RateLimitError)
    _OPENAI_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # noqa: BLE001
    AsyncOpenAI = None  # type: ignore[assignment]
    _OPENAI_IMPORT_ERROR = exc


class LLMUnavailable(RuntimeError):
    """没有任何可用模型（未配置 Key，或全部调用失败）。"""


@dataclass
class LLMResult:
    text: str
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    degraded: bool = False
    error: str | None = None


_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S)


def extract_json(text: str) -> dict | list | None:
    """从模型输出里抠出 JSON。

    现实情况是模型经常裹一层 ```json 代码块，或者在前后加解释性文字，
    所以这里做三级兜底：直接解析 → 去代码块 → 括号配对截取。
    """
    if not text:
        return None
    candidates: list[str] = [text.strip()]
    m = _JSON_FENCE.search(text)
    if m:
        candidates.append(m.group(1).strip())
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            candidates.append(text[start:end + 1])
    for cand in candidates:
        try:
            return json.loads(cand)
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def _client(base_url: str, api_key: str) -> "AsyncOpenAI | None":
    if AsyncOpenAI is None or not api_key:
        return None
    return AsyncOpenAI(base_url=base_url or None, api_key=api_key,
                       timeout=settings.llm_timeout_seconds, max_retries=0)


def _endpoints() -> list[tuple[str, str, str]]:
    """返回 [(base_url, api_key, model)]，主模型在前、备用在后。"""
    eps: list[tuple[str, str, str]] = []
    if settings.llm_api_key:
        eps.append((settings.llm_base_url, settings.llm_api_key, settings.llm_model))
    if settings.llm_fallback_api_key:
        eps.append((settings.llm_fallback_base_url, settings.llm_fallback_api_key,
                    settings.llm_fallback_model or settings.llm_model))
    return eps


class LLMClient:
    """异步 LLM 客户端。未配置 Key 时所有方法抛 LLMUnavailable，由调用方降级。"""

    def __init__(self) -> None:
        self._clients: dict[tuple[str, str], Any] = {}

    # ------------------------------------------------------------------ #
    def _get(self, base_url: str, api_key: str):
        key = (base_url, api_key)
        if key not in self._clients:
            self._clients[key] = _client(base_url, api_key)
        return self._clients[key]

    @property
    def available(self) -> bool:
        return bool(_endpoints())

    # ------------------------------------------------------------------ #
    async def _call(self, *, messages: list[dict], model: str | None = None,
                    temperature: float = 0.2, max_tokens: int | None = None,
                    json_mode: bool = False) -> LLMResult:
        eps = _endpoints()
        if not eps:
            raise LLMUnavailable("未配置 LLM_API_KEY，进入降级模式")

        last_err: Exception | None = None
        for base_url, api_key, default_model in eps:
            client = self._get(base_url, api_key)
            if client is None:
                continue
            use_model = model or default_model
            for attempt in range(settings.llm_max_retries + 1):
                t0 = time.monotonic()
                try:
                    kwargs: dict[str, Any] = {
                        "model": use_model,
                        "messages": messages,
                        "temperature": temperature,
                    }
                    if max_tokens:
                        kwargs["max_tokens"] = max_tokens
                    if json_mode:
                        kwargs["response_format"] = {"type": "json_object"}
                    resp = await client.chat.completions.create(**kwargs)
                    text = (resp.choices[0].message.content or "").strip()
                    usage = {}
                    if getattr(resp, "usage", None):
                        usage = resp.usage.model_dump()
                    logger.info("LLM ok model=%s %.0fms", use_model,
                                (time.monotonic() - t0) * 1000)
                    return LLMResult(text=text, model=use_model, usage=usage)
                except Exception as exc:  # noqa: BLE001
                    last_err = exc
                    retriable = isinstance(
                        exc, (APIConnectionError, APITimeoutError, RateLimitError)) \
                        if _OPENAI_IMPORT_ERROR is None else True
                    if isinstance(exc, APIStatusError) and exc.status_code < 500:
                        retriable = False   # 4xx 是配置问题，重试没意义
                    logger.warning("LLM 调用失败 model=%s attempt=%d: %s",
                                   use_model, attempt, exc)
                    if not retriable or attempt >= settings.llm_max_retries:
                        break
                    await asyncio.sleep(0.8 * (2 ** attempt))
        raise LLMUnavailable(f"全部模型调用失败: {last_err}")

    # ------------------------------------------------------------------ #
    async def chat(self, messages: list[dict], **kw) -> LLMResult:
        return await self._call(messages=messages, **kw)

    async def chat_json(self, messages: list[dict], *, schema_hint: str = "",
                        **kw) -> tuple[dict | list | None, LLMResult]:
        """要求模型返回 JSON。schema_hint 会作为约束追加到最后一条 user 消息。"""
        msgs = list(messages)
        if schema_hint:
            msgs = msgs + [{"role": "user",
                            "content": f"严格按以下 JSON 结构输出，不要输出任何解释文字：\n{schema_hint}"}]
        result = await self._call(messages=msgs, json_mode=True, **kw)
        return extract_json(result.text), result

    async def vision(self, *, prompt: str, image_data_urls: list[str],
                     model: str | None = None, temperature: float = 0.1,
                     max_tokens: int = 1200, json_mode: bool = False,
                     system: str | None = None) -> LLMResult:
        """多模态调用。image_data_urls 是 data:image/jpeg;base64,... 形式。

        ★ system 参数是后加的，之前漏了 —— 后果很严重：
          调用方 tool_impl.vision_diagnose 只发一条 user 消息，
          而项目里精心写的 VISION_SYSTEM（植保专家角色 + 安全规则 +
          照片质量评估要求）和 VISION_SCHEMA（代码正在解析的那套 JSON 结构）
          就成了死代码，一个字都传不进模型。
          模型既不知道自己的角色，也不知道要按什么结构输出 ——
          即使配上 Key，candidates / per_image 解析出来大概率是空的。
        """
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for url in image_data_urls:
            content.append({"type": "image_url", "image_url": {"url": url}})
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": content})
        return await self._call(messages=messages,
                                model=model or settings.effective_vision_model,
                                temperature=temperature, max_tokens=max_tokens,
                                json_mode=json_mode)

    async def stream(self, messages: list[dict], **kw) -> AsyncIterator[str]:
        """SSE 流式输出。失败时抛 LLMUnavailable，由上层切到本地回答。"""
        eps = _endpoints()
        if not eps:
            raise LLMUnavailable("未配置 LLM_API_KEY")
        base_url, api_key, default_model = eps[0]
        client = self._get(base_url, api_key)
        if client is None:
            raise LLMUnavailable("客户端不可用")
        model = kw.get("model") or default_model
        stream = await client.chat.completions.create(
            model=model, messages=messages, stream=True,
            temperature=kw.get("temperature", 0.3))
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content

    # ------------------------------------------------------------------ #
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embedding。只在 EMBEDDING_PROVIDER=openai 时走这里。"""
        if not texts:
            return []
        base_url = settings.embedding_base_url or settings.llm_base_url
        api_key = settings.embedding_api_key or settings.llm_api_key
        client = self._get(base_url, api_key)
        if client is None:
            raise LLMUnavailable("Embedding 服务未配置")
        resp = await client.embeddings.create(model=settings.embedding_model,
                                              input=texts)
        return [d.embedding for d in resp.data]

    async def chat_with_tools(self, messages: list[dict], tools: list[dict],
                              **kw) -> dict:
        """Function calling 一轮：返回原始消息结构（含 tool_calls），供工具循环续写。

        ★ 为什么不复用 chat()：chat() 只回文本，而 OpenAI 工具协议要求把
          assistant 的 tool_calls（含 id）原样塞回 messages、再逐个跟 tool
          结果消息 —— 必须保留 id / name / arguments 的原始结构。
        端点不支持 tools 参数时（部分兼容网关），openai SDK 会抛
        BadRequestError，这里统一转成 LLMUnavailable，让上层走固定管线降级。
        """
        eps = _endpoints()
        if not eps:
            raise LLMUnavailable("未配置 LLM_API_KEY")
        base_url, api_key, default_model = eps[0]
        client = self._get(base_url, api_key)
        if client is None:
            raise LLMUnavailable("客户端不可用")
        model = kw.get("model") or default_model
        try:
            resp = await client.chat.completions.create(
                model=model, messages=messages, tools=tools,
                tool_choice=kw.get("tool_choice", "auto"),
                temperature=kw.get("temperature", 0.2),
                max_tokens=kw.get("max_tokens", 500))
        except Exception as exc:  # noqa: BLE001
            raise LLMUnavailable(f"工具调用失败（端点可能不支持 tools）: {exc}") from exc
        msg = resp.choices[0].message
        tool_calls = [
            {"id": tc.id, "name": tc.function.name,
             "arguments": tc.function.arguments or "{}"}
            for tc in (msg.tool_calls or [])
        ]
        return {"content": msg.content or "", "tool_calls": tool_calls,
                "model": model}


llm = LLMClient()
