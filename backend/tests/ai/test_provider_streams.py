"""Characterisation tests for the two HTTP backends' stream handling.

``test_providers`` checks the happy paths of each wire format. These pin
what both backends do with the rest of a server-sent-event stream (lines
that carry no data, blank and unparseable data, ``[DONE]``), HTTP and
transport failures, tool calls that fail to parse partway through, the
Messages API's error event, and an early close by the consumer, so moving
the shared parts into one reader has something to answer to.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from app.ai.providers.anthropic import AnthropicProvider
from app.ai.providers.base import (
    LLMProviderError,
    NormalizedMessage,
    TextDelta,
    ToolCall,
    ToolCallEnd,
    ToolSpec,
    TurnEnd,
    Usage,
)
from app.ai.providers.openai_compat import OpenAICompatProvider

TOOLS = [ToolSpec(name="list_hosts", description="List hosts", parameters={"type": "object"})]


def _openai() -> OpenAICompatProvider:
    return OpenAICompatProvider("http://localhost:11434/v1", "llama3")


def _anthropic() -> AnthropicProvider:
    return AnthropicProvider("claude-test", api_key="k")


def _data(*chunks: dict) -> list[str]:
    return [f"data: {json.dumps(c)}" for c in chunks]


class _Body(httpx.AsyncByteStream):
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.closed = False

    async def __aiter__(self):
        yield self.data

    async def aclose(self) -> None:
        self.closed = True


def _serve(monkeypatch, handler) -> None:
    original = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        kwargs.pop("verify", None)
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


def _serve_lines(monkeypatch, lines: list[str], status: int = 200) -> _Body:
    body = _Body(("\n".join(lines) + "\n").encode())
    _serve(monkeypatch, lambda request: httpx.Response(status, stream=body))
    return body


def _turn(provider):
    return provider.stream_turn(
        [NormalizedMessage(role="user", content="hello")], TOOLS, max_tokens=64, temperature=0.0
    )


async def _events(provider) -> list:
    return [event async for event in _turn(provider)]


async def _events_until_error(provider) -> tuple[list, LLMProviderError]:
    seen: list = []
    with pytest.raises(LLMProviderError) as info:
        async for event in _turn(provider):
            seen.append(event)
    return seen, info.value


class TestOpenAICompatStream:
    async def test_noise_in_the_stream_is_skipped_and_bad_json_logged(self, monkeypatch, caplog):
        _serve_lines(
            monkeypatch,
            [
                ": keep-alive",
                "event: ping",
                "data:",
                "data:    ",
                "data: {not json",
                "data: [DONE]",
                *_data({"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}),
            ],
        )
        with caplog.at_level(logging.WARNING):
            events = await _events(_openai())

        assert events == [
            TextDelta("ok"),
            Usage(prompt_tokens=0, completion_tokens=0, unknown=True),
            TurnEnd(stop_reason="stop", wants_tools=False),
        ]
        assert [(r.name, r.getMessage()) for r in caplog.records] == [
            ("app.ai.providers.openai_compat", "openai_compat: skipping unparseable chunk")
        ]

    async def test_an_http_error_carries_the_status_and_a_capped_body(self, monkeypatch):
        _serve_lines(monkeypatch, ["x" * 600], status=503)
        _, err = await _events_until_error(_openai())
        assert str(err) == f"Chat completions returned HTTP 503: {'x' * 500}"
        assert err.status_code == 503

    async def test_a_transport_failure_is_wrapped(self, monkeypatch):
        def refuse(request):
            raise httpx.ConnectError("connection refused", request=request)

        _serve(monkeypatch, refuse)
        _, err = await _events_until_error(_openai())
        assert str(err) == "Request failed: connection refused"
        assert isinstance(err.__cause__, httpx.ConnectError)

    async def test_tool_calls_are_parsed_in_index_order_up_to_a_bad_one(self, monkeypatch):
        def fragment(index, **fn):
            return {"choices": [{"delta": {"tool_calls": [{"index": index, "function": fn}]}}]}

        _serve_lines(
            monkeypatch,
            _data(
                fragment(2, name="list_hosts", arguments="{oops"),
                fragment(0, name="list_hosts", arguments=""),
                fragment(1, arguments='{"ignored": true}'),
                {"usage": {"prompt_tokens": 5, "completion_tokens": 1}},
            ),
        )
        seen, err = await _events_until_error(_openai())

        assert seen == [
            Usage(prompt_tokens=5, completion_tokens=1),
            ToolCallEnd(ToolCall(id="call_0", name="list_hosts", arguments={})),
        ]
        assert str(err) == "Model produced invalid JSON arguments for tool 'list_hosts'"
        assert err.__cause__ is None

    async def test_an_early_close_releases_the_response(self, monkeypatch):
        body = _serve_lines(
            monkeypatch,
            _data(
                {"choices": [{"delta": {"content": "first"}}]},
                {"choices": [{"delta": {"content": "second"}}]},
            ),
        )
        stream = _turn(_openai())
        assert await stream.__anext__() == TextDelta("first")
        await stream.aclose()
        assert body.closed


class TestAnthropicStream:
    async def test_noise_in_the_stream_is_skipped_and_bad_json_logged(self, monkeypatch, caplog):
        _serve_lines(
            monkeypatch,
            [
                "event: message_start",
                "data:",
                "data: {not json",
                "data: [DONE]",
                *_data(
                    {
                        "type": "message_start",
                        "message": {"usage": {"input_tokens": 7, "output_tokens": 1}},
                    },
                    {"type": "content_block_delta", "delta": {"type": "text_delta", "text": ""}},
                    {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}},
                    {"type": "message_delta", "delta": {}, "usage": {"output_tokens": 4}},
                ),
            ],
        )
        with caplog.at_level(logging.WARNING):
            events = await _events(_anthropic())

        assert events == [
            TextDelta("hi"),
            Usage(prompt_tokens=7, completion_tokens=4),
            TurnEnd(stop_reason="end_turn", wants_tools=False),
        ]
        assert [(r.name, r.getMessage()) for r in caplog.records] == [
            ("app.ai.providers.anthropic", "anthropic: skipping unparseable event"),
            ("app.ai.providers.anthropic", "anthropic: skipping unparseable event"),
        ]

    async def test_an_http_error_carries_the_status_and_a_capped_body(self, monkeypatch):
        _serve_lines(monkeypatch, ["y" * 600], status=529)
        _, err = await _events_until_error(_anthropic())
        assert str(err) == f"Messages API returned HTTP 529: {'y' * 500}"
        assert err.status_code == 529

    async def test_a_transport_failure_is_wrapped(self, monkeypatch):
        def refuse(request):
            raise httpx.ReadTimeout("timed out", request=request)

        _serve(monkeypatch, refuse)
        _, err = await _events_until_error(_anthropic())
        assert str(err) == "Request failed: timed out"
        assert isinstance(err.__cause__, httpx.ReadTimeout)

    async def test_an_error_event_ends_the_turn_after_the_text_before_it(self, monkeypatch):
        _serve_lines(
            monkeypatch,
            _data(
                {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "par"}},
                {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
                {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "never"}},
            ),
        )
        seen, err = await _events_until_error(_anthropic())
        assert seen == [TextDelta("par")]
        assert str(err) == "overloaded_error: Overloaded"
        assert err.status_code is None

    async def test_an_error_event_without_details_still_fails(self, monkeypatch):
        _serve_lines(monkeypatch, _data({"type": "error"}))
        _, err = await _events_until_error(_anthropic())
        assert str(err) == "error: stream error"

    async def test_tool_calls_are_parsed_in_index_order_up_to_a_bad_one(self, monkeypatch):
        def start(index, **block):
            return {"type": "content_block_start", "index": index, "content_block": block}

        def args(index, partial):
            return {
                "type": "content_block_delta",
                "index": index,
                "delta": {"type": "input_json_delta", "partial_json": partial},
            }

        _serve_lines(
            monkeypatch,
            _data(
                start(3, type="tool_use", id="toolu_x", name="list_hosts"),
                args(3, "{bad"),
                start(1, type="tool_use", name="list_hosts"),
                start(2, type="text"),
                args(2, '{"orphan": 1}'),
                start(0, type="tool_use", id="", name=""),
                {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
            ),
        )
        seen, err = await _events_until_error(_anthropic())

        assert seen == [ToolCallEnd(ToolCall(id="toolu_1", name="list_hosts", arguments={}))]
        assert str(err) == "Model produced invalid JSON arguments for tool 'list_hosts'"
        assert err.__cause__ is None

    async def test_a_tool_use_stop_wants_tools_even_without_a_call(self, monkeypatch):
        _serve_lines(
            monkeypatch,
            _data({"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {}}),
        )
        events = await _events(_anthropic())
        assert events == [
            Usage(prompt_tokens=0, completion_tokens=0),
            TurnEnd(stop_reason="tool_use", wants_tools=True),
        ]

    async def test_an_early_close_releases_the_response(self, monkeypatch):
        body = _serve_lines(
            monkeypatch,
            _data(
                {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "one"}},
                {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "two"}},
            ),
        )
        stream = _turn(_anthropic())
        assert await stream.__anext__() == TextDelta("one")
        await stream.aclose()
        assert body.closed
