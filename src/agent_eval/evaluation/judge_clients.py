"""Async LLM clients for configurable evaluators (LLM-as-judge).

The configurable evaluators (PR-B) need to call whatever provider the user
picked in the editor — OpenAI, Anthropic native, DeepSeek, Azure, or any
OpenAI-compatible endpoint. Each dialect speaks slightly different HTTP:

    * OpenAI-compatible (``openai`` / ``openai_compatible`` / ``deepseek`` /
      ``custom``): ``POST {base_url}/chat/completions``, Bearer auth,
      ``choices[0].message.content`` payload.
    * ``anthropic``:  ``POST {base_url or api.anthropic.com}/v1/messages``,
      ``x-api-key`` + ``anthropic-version`` headers, ``content[].text`` payload,
      and the system prompt is hoisted out of ``messages`` into a top-level
      ``system`` field.
    * ``azure``: same JSON shape as OpenAI but the URL is
      ``{base_url}/openai/deployments/{deployment}/chat/completions?api-version=...``
      and auth uses ``api-key`` instead of ``Authorization``.

We do NOT take a hard dep on ``langchain-openai`` or ``anthropic`` here —
``httpx`` covers all of it, the response shapes are stable, and avoiding an
extra abstraction keeps retries / timeouts / usage extraction in one place.
``orchestrator``-side scoring still uses ``ChatOpenAI`` because it predates
this module; configurable evaluators go through ``build_judge_client``.

Usage
-----

    from agent_eval.evaluation.judge_clients import build_judge_client

    async with build_judge_client(provider_row, model="gpt-4o-mini") as judge:
        result = await judge.ainvoke([
            {"role": "system", "content": "Score this response..."},
            {"role": "user", "content": prompt},
        ])
    # result.content -> str, result.usage -> {input_tokens, output_tokens}
"""
from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from typing import Any

import httpx

from agent_eval.db_models.tables import EvaluatorProviderRow
from agent_eval.evaluation.crypto import decrypt_secret

logger = logging.getLogger(__name__)


# Network-layer transients that are safe to retry. We deliberately exclude
# httpx.HTTPStatusError and any 4xx/5xx response — those mean the request
# reached the upstream LLM, retrying could double-bill or double-execute.
# ConnectError covers DNS failures (EAI_AGAIN: "Temporary failure in name
# resolution") which is the dominant failure mode under burst concurrency
# inside containers.
_TRANSIENT_HTTPX_EXC = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
    httpx.PoolTimeout,
)
_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY = 0.2  # seconds; exponential, jittered


class JudgeClientError(RuntimeError):
    """Raised when a judge HTTP call fails in a way the caller should surface
    (network error, 4xx/5xx, or unparseable response). The message is safe to
    show to the user — API keys are never echoed."""


@dataclass
class JudgeUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass
class JudgeInvocation:
    """Result of one ``ainvoke`` call.

    ``raw_response`` is included so callers (e.g. the dry-run endpoint)
    can show the verbatim provider response when debugging an evaluator
    template — but it is *not* persisted into evaluation_scores."""
    content: str
    usage: JudgeUsage = field(default_factory=JudgeUsage)
    model: str = ""
    raw_response: dict[str, Any] = field(default_factory=dict)


# ────────────────────────────────────────────────────────────────────────
# Base + dialects
# ────────────────────────────────────────────────────────────────────────


class _BaseJudgeClient:
    """Owns one ``httpx.AsyncClient`` and the dialect-agnostic glue.

    Subclasses implement ``_build_request`` (URL, headers, JSON body) and
    ``_parse_response`` (extract content + usage). Everything else — the
    AsyncClient lifecycle, error wrapping, retries — lives here so the
    dialect classes stay tiny.
    """

    provider_type: str = ""

    def __init__(
        self,
        *,
        base_url: str | None,
        api_key: str | None,
        model: str,
        timeout: float = 60.0,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        extra_config: dict[str, Any] | None = None,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.extra_config = extra_config or {}
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "_BaseJudgeClient":
        self._client = httpx.AsyncClient(timeout=self.timeout)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _build_request(
        self, messages: list[dict[str, Any]],
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        raise NotImplementedError

    def _parse_response(self, body: dict[str, Any]) -> JudgeInvocation:
        raise NotImplementedError

    async def ainvoke(self, messages: list[dict[str, Any]]) -> JudgeInvocation:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        url, headers, payload = self._build_request(messages)

        last_exc: Exception | None = None
        for attempt in range(1, _RETRY_ATTEMPTS + 1):
            try:
                resp = await self._client.post(url, headers=headers, json=payload)
                break
            except _TRANSIENT_HTTPX_EXC as e:
                last_exc = e
                if attempt >= _RETRY_ATTEMPTS:
                    raise JudgeClientError(
                        f"{self.provider_type}: connection error after "
                        f"{_RETRY_ATTEMPTS} attempts: {type(e).__name__}: {e}"
                    ) from e
                delay = _RETRY_BASE_DELAY * (3 ** (attempt - 1))
                delay *= 1 + random.random() * 0.3  # jitter to avoid sync retries
                logger.warning(
                    "judge_client transient failure (attempt %d/%d, retrying in %.2fs): "
                    "%s: %s",
                    attempt, _RETRY_ATTEMPTS, delay, type(e).__name__, e,
                )
                await asyncio.sleep(delay)
            except httpx.HTTPError as e:
                # Non-retryable httpx error (e.g. malformed URL, invalid cert) —
                # bubble up immediately, retrying won't help.
                raise JudgeClientError(
                    f"{self.provider_type}: connection error: {type(e).__name__}: {e}"
                ) from e

        if resp.status_code >= 400:
            preview = resp.text[:300].replace("\n", " ")
            raise JudgeClientError(
                f"{self.provider_type}: HTTP {resp.status_code}: {preview}"
            )

        try:
            body = resp.json()
        except ValueError as e:
            raise JudgeClientError(
                f"{self.provider_type}: response is not JSON: {resp.text[:200]}"
            ) from e

        try:
            return self._parse_response(body)
        except (KeyError, IndexError, TypeError) as e:
            raise JudgeClientError(
                f"{self.provider_type}: unexpected response shape: {e}"
            ) from e


class OpenAICompatJudgeClient(_BaseJudgeClient):
    """OpenAI ``/chat/completions`` dialect — covers OpenAI itself plus any
    third-party endpoint that mirrors the protocol (DeepSeek, OpenRouter,
    self-hosted vLLM, Kiro proxy, etc.)."""

    provider_type = "openai_compatible"

    def _build_request(
        self, messages: list[dict[str, Any]],
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        if not self.base_url:
            # OpenAI proper — fall back to public endpoint.
            base = "https://api.openai.com/v1"
        else:
            base = self.base_url
        url = f"{base}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        return url, headers, payload

    def _parse_response(self, body: dict[str, Any]) -> JudgeInvocation:
        choice = body["choices"][0]["message"]
        # Reasoning models (DeepSeek-R1, Kimi-thinking, mimo, QwQ, …) emit the
        # answer in `reasoning_content` and leave `content` empty when the
        # model has nothing left to say after the chain-of-thought. Fall back
        # so we don't lose the whole response.
        content = choice.get("content") or choice.get("reasoning_content") or ""
        usage_raw = body.get("usage") or {}
        usage = JudgeUsage(
            input_tokens=int(usage_raw.get("prompt_tokens") or 0),
            output_tokens=int(usage_raw.get("completion_tokens") or 0),
            total_tokens=int(usage_raw.get("total_tokens") or 0),
        )
        return JudgeInvocation(
            content=content,
            usage=usage,
            model=body.get("model") or self.model,
            raw_response=body,
        )


class AnthropicJudgeClient(_BaseJudgeClient):
    """Anthropic ``/v1/messages`` dialect.

    Differs from the OpenAI shape in two important ways:

      * The system prompt is *not* a message — it goes into a top-level
        ``system`` field. We extract any role==system messages from the
        passed-in list and join them.
      * Token usage lives at ``usage.input_tokens`` / ``usage.output_tokens``
        (no ``total_tokens``); we compute the total here.
    """

    provider_type = "anthropic"

    def _build_request(
        self, messages: list[dict[str, Any]],
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        base = self.base_url or "https://api.anthropic.com"
        url = f"{base}/v1/messages"
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": self.extra_config.get("anthropic_version") or "2023-06-01",
        }
        if self.api_key:
            headers["x-api-key"] = self.api_key

        system_parts: list[str] = []
        chat: list[dict[str, Any]] = []
        for m in messages:
            role = m.get("role")
            content = m.get("content") or ""
            if role == "system":
                if content:
                    system_parts.append(content)
            else:
                chat.append({"role": role, "content": content})

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": chat,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        return url, headers, payload

    def _parse_response(self, body: dict[str, Any]) -> JudgeInvocation:
        # content is a list of blocks: [{"type":"text","text":"..."}, ...]
        blocks = body.get("content") or []
        text_parts: list[str] = []
        for blk in blocks:
            if isinstance(blk, dict) and blk.get("type") == "text":
                text_parts.append(blk.get("text") or "")
        content = "".join(text_parts)
        usage_raw = body.get("usage") or {}
        in_tok = int(usage_raw.get("input_tokens") or 0)
        out_tok = int(usage_raw.get("output_tokens") or 0)
        usage = JudgeUsage(
            input_tokens=in_tok,
            output_tokens=out_tok,
            total_tokens=in_tok + out_tok,
        )
        return JudgeInvocation(
            content=content,
            usage=usage,
            model=body.get("model") or self.model,
            raw_response=body,
        )


class AzureOpenAIJudgeClient(_BaseJudgeClient):
    """Azure-hosted OpenAI dialect.

    URL pattern: ``{base_url}/openai/deployments/{deployment}/chat/completions
    ?api-version={api_version}``. Both ``deployment`` and ``api_version`` are
    pulled from ``extra_config``; if ``deployment`` is missing we fall back
    to ``model`` since Azure conventionally names deployments after models.
    Auth header is ``api-key``, not ``Authorization``."""

    provider_type = "azure"

    def _build_request(
        self, messages: list[dict[str, Any]],
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        if not self.base_url:
            raise JudgeClientError("azure: base_url is required (resource endpoint)")
        deployment = self.extra_config.get("deployment") or self.model
        api_version = self.extra_config.get("api_version") or "2024-02-01"
        url = (
            f"{self.base_url}/openai/deployments/{deployment}"
            f"/chat/completions?api-version={api_version}"
        )
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["api-key"] = self.api_key
        payload: dict[str, Any] = {
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        return url, headers, payload

    _parse_response = OpenAICompatJudgeClient._parse_response  # type: ignore[assignment]


# ────────────────────────────────────────────────────────────────────────
# Factory
# ────────────────────────────────────────────────────────────────────────

# provider_type -> client class. ``openai`` / ``openai_compatible`` /
# ``deepseek`` / ``custom`` all speak the same protocol; we map them to one
# class so a "provider" is a credential record, and "type" only matters for
# building the URL + auth header.
_DIALECTS: dict[str, type[_BaseJudgeClient]] = {
    "openai": OpenAICompatJudgeClient,
    "openai_compatible": OpenAICompatJudgeClient,
    "deepseek": OpenAICompatJudgeClient,
    "custom": OpenAICompatJudgeClient,
    "anthropic": AnthropicJudgeClient,
    "azure": AzureOpenAIJudgeClient,
}


def build_judge_client(
    provider: EvaluatorProviderRow,
    *,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 1024,
    timeout: float = 60.0,
) -> _BaseJudgeClient:
    """Construct an async judge client from a saved provider record.

    ``model`` overrides ``provider.default_model``; if both are missing we
    raise here rather than at call time so the editor's "save" button can
    surface the error. ``api_key`` is decrypted via the fernet helper —
    a missing/rotated key surfaces as a no-auth client (Anthropic / OpenAI
    will then 401, which the caller wraps in ``JudgeClientError`` with a
    useful message).
    """
    cls = _DIALECTS.get(provider.provider_type)
    if cls is None:
        raise JudgeClientError(
            f"unsupported provider_type '{provider.provider_type}'. "
            f"known: {', '.join(sorted(_DIALECTS))}"
        )

    resolved_model = model or provider.default_model
    if not resolved_model:
        raise JudgeClientError(
            f"provider '{provider.name}' has no default_model and the "
            "evaluator did not specify one — set one before saving."
        )

    api_key = decrypt_secret(provider.api_key_encrypted) if provider.api_key_encrypted else None

    return cls(
        base_url=provider.base_url,
        api_key=api_key,
        model=resolved_model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        extra_config=provider.extra_config or {},
    )
