"""OpenAI-compatible ``/chat/completions`` backend.

Covers Ollama, vLLM, LM Studio, OpenRouter, and OpenAI itself — they all
speak the same streaming wire format, so one client serves both the
"local model on the homelab" and "hosted provider" cases.

Streaming quirk worth knowing: tool calls arrive as *indexed* fragments
(``tool_calls[i].function.arguments`` in pieces across many chunks), so
they have to be accumulated per index and parsed only at the end.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Any

import httpx

from app.ai.providers._streaming import parse_tool_calls, stream_json_events
from app.ai.providers.base import (
    LLMProviderError,
    NormalizedMessage,
    StreamEvent,
    TextDelta,
    ToolCallEnd,
    ToolSpec,
    TurnEnd,
    Usage,
    build_ssl_verify,
)

logger = logging.getLogger(__name__)

# Generous: a local model on cold start can take a while to load weights.
REQUEST_TIMEOUT = httpx.Timeout(connect=15.0, read=300.0, write=30.0, pool=15.0)


class OpenAICompatProvider:
    provider_type = "openai_compat"
    supports_tools = True

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        verify_ssl: bool = True,
        ca_cert_pem: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self._verify = build_ssl_verify(verify_ssl, ca_cert_pem)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _to_wire(self, messages: list[NormalizedMessage]) -> list[dict[str, Any]]:
        wire: list[dict[str, Any]] = []
        for msg in messages:
            if msg.role == "tool":
                wire.append(
                    {
                        "role": "tool",
                        "tool_call_id": msg.tool_call_id or "",
                        "content": msg.content,
                    }
                )
                continue
            entry: dict[str, Any] = {"role": msg.role, "content": msg.content}
            if msg.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments),
                        },
                    }
                    for call in msg.tool_calls
                ]
                # Some servers reject a null content alongside tool_calls.
                entry["content"] = msg.content or ""
            wire.append(entry)
        return wire

    def _payload(
        self,
        messages: list[NormalizedMessage],
        tools: list[ToolSpec],
        max_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._to_wire(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
            # Ollama and vLLM only emit a usage block when asked.
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in tools
            ]
            payload["tool_choice"] = "auto"
        return payload

    async def stream_turn(
        self,
        messages: list[NormalizedMessage],
        tools: list[ToolSpec],
        *,
        max_tokens: int,
        temperature: float,
    ) -> AsyncIterator[StreamEvent]:
        payload = self._payload(messages, tools, max_tokens, temperature)

        url = f"{self.base_url}/chat/completions"
        # Fragments keyed by the index the server assigns each tool call.
        pending: dict[int, dict[str, str]] = {}
        finish_reason = "stop"
        saw_usage = False

        chunks = stream_json_events(
            url,
            payload,
            self._headers(),
            verify=self._verify,
            timeout=REQUEST_TIMEOUT,
            api_name="Chat completions",
            log=logger,
            unparseable_message="openai_compat: skipping unparseable chunk",
            done_marker="[DONE]",
        )
        async with aclosing(chunks):
            async for chunk in chunks:
                if usage := chunk.get("usage"):
                    saw_usage = True
                    yield Usage(
                        prompt_tokens=int(usage.get("prompt_tokens") or 0),
                        completion_tokens=int(usage.get("completion_tokens") or 0),
                    )

                for choice in chunk.get("choices") or []:
                    if reason := choice.get("finish_reason"):
                        finish_reason = reason
                    delta = choice.get("delta") or {}
                    if text := delta.get("content"):
                        yield TextDelta(text)
                    for fragment in delta.get("tool_calls") or []:
                        idx = int(fragment.get("index") or 0)
                        slot = pending.setdefault(idx, {"id": "", "name": "", "args": ""})
                        if fragment_id := fragment.get("id"):
                            slot["id"] = fragment_id
                        fn = fragment.get("function") or {}
                        if name := fn.get("name"):
                            slot["name"] = name
                        if args := fn.get("arguments"):
                            slot["args"] += args

        wants_tools = False
        for call in parse_tool_calls(pending, "call_"):
            wants_tools = True
            yield ToolCallEnd(call)

        if not saw_usage:
            yield Usage(prompt_tokens=0, completion_tokens=0, unknown=True)
        yield TurnEnd(stop_reason=finish_reason, wants_tools=wants_tools)

    async def test_connection(self) -> str:
        """Fetch the model list; returns a short human-readable result."""
        try:
            async with httpx.AsyncClient(verify=self._verify, timeout=15.0) as client:
                resp = await client.get(f"{self.base_url}/models", headers=self._headers())
        except httpx.HTTPError as exc:
            raise LLMProviderError(f"Request failed: {exc}") from exc
        if resp.status_code in (401, 403):
            raise LLMProviderError("Authentication failed", resp.status_code)
        if resp.status_code >= 400:
            raise LLMProviderError(f"HTTP {resp.status_code}", resp.status_code)
        try:
            names = [m.get("id", "") for m in resp.json().get("data", [])]
        except Exception:
            return "Reachable"
        if self.model in names:
            return f"Reachable; model {self.model} available"
        if names:
            return f"Reachable, but {self.model} was not in the served model list"
        return "Reachable"
