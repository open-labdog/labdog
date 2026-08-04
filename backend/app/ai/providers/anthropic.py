"""Anthropic Messages API backend.

Speaks the Messages API over httpx rather than the ``anthropic`` SDK:
LabDog supports three backends behind one streaming interface, and a
vendor SDK would only serve one of them while adding a dependency to a
project whose CI gates on ``pip-audit``.

Two shape differences from the OpenAI-compatible backend drive the
translation here: the system prompt is a top-level ``system`` field
rather than a message role, and tool results go back as ``tool_result``
content blocks inside a *user* turn rather than a dedicated role.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.ai.providers.base import (
    LLMProviderError,
    NormalizedMessage,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolCallEnd,
    ToolSpec,
    TurnEnd,
    Usage,
    build_ssl_verify,
)

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.anthropic.com"
API_VERSION = "2023-06-01"
REQUEST_TIMEOUT = httpx.Timeout(connect=15.0, read=300.0, write=30.0, pool=15.0)


class AnthropicProvider:
    provider_type = "anthropic"
    supports_tools = True

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        verify_ssl: bool = True,
        ca_cert_pem: str | None = None,
    ) -> None:
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.model = model
        self._api_key = api_key
        self._verify = build_ssl_verify(verify_ssl, ca_cert_pem)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": API_VERSION,
        }
        if self._api_key:
            headers["x-api-key"] = self._api_key
        return headers

    def _to_wire(
        self, messages: list[NormalizedMessage]
    ) -> tuple[str | None, list[dict[str, Any]]]:
        """Split off the system prompt and build the ``messages`` array.

        Consecutive tool results are merged into a single user turn, which
        is what the API expects when the model requested several tools in
        one assistant turn.
        """
        system_parts: list[str] = []
        wire: list[dict[str, Any]] = []

        for msg in messages:
            if msg.role == "system":
                if msg.content:
                    system_parts.append(msg.content)
                continue

            if msg.role == "tool":
                block = {
                    "type": "tool_result",
                    "tool_use_id": msg.tool_call_id or "",
                    "content": msg.content,
                }
                if wire and wire[-1]["role"] == "user" and isinstance(wire[-1]["content"], list):
                    wire[-1]["content"].append(block)
                else:
                    wire.append({"role": "user", "content": [block]})
                continue

            if msg.role == "assistant":
                blocks: list[dict[str, Any]] = []
                if msg.content:
                    blocks.append({"type": "text", "text": msg.content})
                for call in msg.tool_calls:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": call.id,
                            "name": call.name,
                            "input": call.arguments,
                        }
                    )
                # An assistant turn must carry at least one block.
                wire.append(
                    {
                        "role": "assistant",
                        "content": blocks or [{"type": "text", "text": ""}],
                    }
                )
                continue

            wire.append({"role": "user", "content": msg.content})

        return ("\n\n".join(system_parts) or None), wire

    async def stream_turn(
        self,
        messages: list[NormalizedMessage],
        tools: list[ToolSpec],
        *,
        max_tokens: int,
        temperature: float,
    ) -> AsyncIterator[StreamEvent]:
        system, wire_messages = self._to_wire(messages)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": wire_messages,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.parameters,
                }
                for tool in tools
            ]
        # `temperature` is deliberately not sent: current Claude models reject
        # sampling parameters with a 400, and the older ones are fine at their
        # default. Steering happens through the system prompt instead.

        url = f"{self.base_url}/v1/messages"
        # Tool-use blocks arrive as a start event plus a stream of JSON
        # fragments, keyed by content-block index.
        pending: dict[int, dict[str, str]] = {}
        prompt_tokens = 0
        completion_tokens = 0
        stop_reason = "end_turn"

        try:
            async with httpx.AsyncClient(verify=self._verify, timeout=REQUEST_TIMEOUT) as client:
                async with client.stream(
                    "POST", url, json=payload, headers=self._headers()
                ) as response:
                    if response.status_code >= 400:
                        body = (await response.aread()).decode(errors="replace")[:500]
                        raise LLMProviderError(
                            f"Messages API returned HTTP {response.status_code}: {body}",
                            response.status_code,
                        )
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if not data:
                            continue
                        try:
                            event = json.loads(data)
                        except json.JSONDecodeError:
                            logger.warning("anthropic: skipping unparseable event")
                            continue

                        etype = event.get("type")

                        if etype == "message_start":
                            usage = (event.get("message") or {}).get("usage") or {}
                            prompt_tokens = int(usage.get("input_tokens") or 0)
                            completion_tokens = int(usage.get("output_tokens") or 0)

                        elif etype == "content_block_start":
                            block = event.get("content_block") or {}
                            if block.get("type") == "tool_use":
                                pending[int(event.get("index") or 0)] = {
                                    "id": block.get("id", ""),
                                    "name": block.get("name", ""),
                                    "args": "",
                                }

                        elif etype == "content_block_delta":
                            delta = event.get("delta") or {}
                            dtype = delta.get("type")
                            if dtype == "text_delta":
                                if text := delta.get("text"):
                                    yield TextDelta(text)
                            elif dtype == "input_json_delta":
                                slot = pending.get(int(event.get("index") or 0))
                                if slot is not None:
                                    slot["args"] += delta.get("partial_json") or ""

                        elif etype == "message_delta":
                            if reason := (event.get("delta") or {}).get("stop_reason"):
                                stop_reason = reason
                            usage = event.get("usage") or {}
                            if (out := usage.get("output_tokens")) is not None:
                                completion_tokens = int(out)

                        elif etype == "error":
                            err = event.get("error") or {}
                            raise LLMProviderError(
                                f"{err.get('type', 'error')}: {err.get('message', 'stream error')}"
                            )
        except httpx.HTTPError as exc:
            raise LLMProviderError(f"Request failed: {exc}") from exc

        wants_tools = False
        for idx in sorted(pending):
            slot = pending[idx]
            if not slot["name"]:
                continue
            try:
                arguments = json.loads(slot["args"]) if slot["args"].strip() else {}
            except json.JSONDecodeError:
                raise LLMProviderError(
                    f"Model produced invalid JSON arguments for tool {slot['name']!r}"
                ) from None
            wants_tools = True
            yield ToolCallEnd(
                ToolCall(id=slot["id"] or f"toolu_{idx}", name=slot["name"], arguments=arguments)
            )

        yield Usage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
        yield TurnEnd(
            stop_reason=stop_reason, wants_tools=wants_tools or stop_reason == "tool_use"
        )

    async def test_connection(self) -> str:
        """Send a one-token message; returns a short human-readable result."""
        payload = {
            "model": self.model,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "ping"}],
        }
        try:
            async with httpx.AsyncClient(verify=self._verify, timeout=30.0) as client:
                resp = await client.post(
                    f"{self.base_url}/v1/messages", json=payload, headers=self._headers()
                )
        except httpx.HTTPError as exc:
            raise LLMProviderError(f"Request failed: {exc}") from exc
        if resp.status_code in (401, 403):
            raise LLMProviderError("Authentication failed", resp.status_code)
        if resp.status_code >= 400:
            body = resp.text[:300]
            raise LLMProviderError(f"HTTP {resp.status_code}: {body}", resp.status_code)
        return f"Reachable; model {self.model} responded"
