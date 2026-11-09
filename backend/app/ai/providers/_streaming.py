"""Plumbing shared by the two HTTP backends' ``stream_turn``.

The Messages API and ``/chat/completions`` both stream server-sent events
from a POST, and both deliver a tool call's arguments as fragments keyed
by index that can only be parsed once the stream ends. Only the shape of
each event differs, so that is all each backend keeps for itself.
"""

from __future__ import annotations

import json
import logging
import ssl
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx

from app.ai.providers.base import LLMProviderError, ToolCall


async def stream_json_events(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    *,
    verify: bool | ssl.SSLContext,
    timeout: httpx.Timeout,
    api_name: str,
    log: logging.Logger,
    unparseable_message: str,
    done_marker: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """POST ``payload`` and yield the JSON of each ``data:`` line as it arrives.

    Lines that carry no data, blank data and ``done_marker`` are skipped;
    data that is not JSON is logged on ``log`` as ``unparseable_message``
    and skipped. An HTTP error status or a transport failure raises
    :class:`LLMProviderError`.

    Callers iterate it under :func:`contextlib.aclosing`, so a consumer
    that stops early releases the response at once rather than when the
    generator is collected.
    """
    try:
        async with httpx.AsyncClient(verify=verify, timeout=timeout) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode(errors="replace")[:500]
                    raise LLMProviderError(
                        f"{api_name} returned HTTP {response.status_code}: {body}",
                        response.status_code,
                    )
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == done_marker:
                        continue
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        log.warning(unparseable_message)
                        continue
                    yield event
    except httpx.HTTPError as exc:
        raise LLMProviderError(f"Request failed: {exc}") from exc


def parse_tool_calls(pending: dict[int, dict[str, str]], id_prefix: str) -> Iterator[ToolCall]:
    """The tool calls accumulated in ``pending``, in index order.

    Each slot holds ``id``, ``name`` and the joined ``args`` fragments. A
    slot that never got a name is dropped, and one without an id is named
    ``<id_prefix><index>``. Malformed arguments raise
    :class:`LLMProviderError`, which fails the turn; the generator is
    lazy, so the calls before the malformed one have been yielded by then.
    """
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
        yield ToolCall(id=slot["id"] or f"{id_prefix}{idx}", name=slot["name"], arguments=arguments)
