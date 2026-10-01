# Provenance: ported from apn201/Virta virta/nebius_client.py @ 4d85c72 (2026-09-23),
# adapted for Vipunen 2026-10-01: async client, User-Agent header, Apertus
# reasoning fields, model listing. Same author.
"""Thin async OpenAI-compatible client for Apertus.

Where Apertus puts its thinking differs by endpoint, so every read goes through
``_extract`` and records which field the text came from:

- CSCS ``-thinking`` models: answer in ``content``, trace in ``reasoning``
- other servers: trace in ``reasoning_content`` (or parked in ``model_extra``)
- ``enable_thinking`` on a base model: trace inline in ``content`` between
  ``<|inner_prefix|>`` and ``<|inner_suffix|>`` - split out here
- reasoning-only replies: empty ``content``, answer only in the trace - fall back to it

Public AI requires a User-Agent header on every call; it is a default header here.
No tools, no response_format: plain chat completions only.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

import openai
from openai import AsyncOpenAI

from vipunen.config import Endpoint, model_id_for

INNER = re.compile(r"<\|inner_prefix\|>(.*?)(?:<\|inner_suffix\|>|$)", re.S)


class ApertusError(RuntimeError):
    """A call failed, with a message aimed at fixing the setup. Never contains the key."""


@dataclass(frozen=True)
class ChatResult:
    text: str                # the answer: content, or the trace when content is empty
    source: str              # 'content' | 'reasoning' | 'empty'
    content: str
    reasoning: str
    model: str
    usage: dict[str, Any] = field(default_factory=dict)
    finish_reason: str = ""
    latency_ms: int = 0

    @property
    def tokens_in(self) -> int:
        return int(self.usage.get("prompt_tokens", 0) or 0)

    @property
    def tokens_out(self) -> int:
        return int(self.usage.get("completion_tokens", 0) or 0)

    @property
    def truncated(self) -> bool:
        return self.finish_reason == "length"


def _extract(message: Any) -> tuple[str, str, str, str]:
    """Return (text, source, content, reasoning)."""
    content = getattr(message, "content", None) or ""
    extra = getattr(message, "model_extra", None) or {}
    reasoning = (getattr(message, "reasoning", None) or getattr(message, "reasoning_content", None)
                 or extra.get("reasoning") or extra.get("reasoning_content") or "")

    inline = INNER.search(content)
    if inline:
        reasoning = reasoning or inline.group(1)
        content = INNER.sub("", content)

    content, reasoning = content.strip(), reasoning.strip()
    if content:
        return content, "content", content, reasoning
    if reasoning:
        return reasoning, "reasoning", content, reasoning
    return "", "empty", content, reasoning


class ApertusClient:
    def __init__(self, endpoint: Endpoint, *, timeout_s: float = 180.0, max_retries: int = 3,
                 sdk: Any = None) -> None:
        self.endpoint = endpoint
        self.sdk = sdk or AsyncOpenAI(
            base_url=endpoint.base_url, api_key=endpoint.api_key, timeout=timeout_s,
            max_retries=max_retries,  # SDK backs off on 429 / 5xx / connection errors
            default_headers={"User-Agent": endpoint.user_agent},
        )

    async def chat(self, messages: list[dict[str, str]], *, model: str, max_tokens: int,
                   temperature: float, seed: int | None = None) -> ChatResult:
        model_id = model_id_for(self.endpoint, model)
        params: dict[str, Any] = dict(model=model_id, messages=messages,
                                      max_tokens=max_tokens, temperature=temperature)
        if seed is not None:
            params["seed"] = seed
        started = time.monotonic()
        try:
            resp = await self.sdk.chat.completions.create(**params)
        except openai.APIError as exc:
            raise self._explain(exc, model_id) from exc
        latency_ms = int((time.monotonic() - started) * 1000)

        if not resp.choices:
            raise ApertusError(f"{self.endpoint.name} returned no choices for {model_id!r}")
        text, source, content, reasoning = _extract(resp.choices[0].message)
        usage = resp.usage.model_dump() if getattr(resp, "usage", None) else {}
        return ChatResult(text=text, source=source, content=content, reasoning=reasoning,
                          model=getattr(resp, "model", None) or model_id, usage=usage,
                          finish_reason=str(getattr(resp.choices[0], "finish_reason", "") or ""),
                          latency_ms=latency_ms)

    async def aclose(self) -> None:
        close = getattr(self.sdk, "close", None)
        if close:
            await close()

    async def models(self) -> list[str]:
        try:
            page = await self.sdk.models.list()
        except openai.APIError as exc:
            raise self._explain(exc, "-") from exc
        return sorted(m.id for m in page.data)

    def _explain(self, exc: openai.APIError, model_id: str) -> ApertusError:
        ep = self.endpoint
        where = f"{ep.name} at {ep.base_url}"
        if isinstance(exc, openai.AuthenticationError):
            hint = f"the API key was rejected (HTTP 401). Key in use: {ep.redacted_key}."
        elif isinstance(exc, openai.NotFoundError):
            hint = f"unknown model id {model_id!r} (HTTP 404). Run `vipunen models` for the exact ids."
        elif isinstance(exc, openai.PermissionDeniedError):
            hint = f"access to {model_id!r} refused (HTTP 403): key valid but not entitled, or out of credits."
        elif isinstance(exc, openai.RateLimitError):
            hint = "rate-limited after retries (HTTP 429). Wait, or lower the pace."
        elif isinstance(exc, openai.APIConnectionError):
            hint = "could not connect. Check the URL (ends in /v1), network, proxy."
        elif isinstance(exc, openai.APIStatusError):
            hint = f"HTTP {exc.status_code} for {model_id!r}."
        else:
            hint = type(exc).__name__
        said = getattr(exc, "message", "") or ""
        return ApertusError(f"{where}: {hint}" + (f" Server said: {said[:300]}" if said else ""))
