"""Provider wire-format tests.

Each backend is fed recorded streaming output and checked for the two
things the loop depends on: text arrives as deltas, and tool calls are
reassembled from fragments with their arguments parsed.
"""

from __future__ import annotations

import json

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
from app.ai.providers.factory import is_local_endpoint
from app.ai.providers.openai_compat import OpenAICompatProvider

TOOLS = [
    ToolSpec(
        name="list_hosts",
        description="List hosts",
        parameters={"type": "object", "properties": {}},
    )
]


def _sse(*chunks: dict) -> bytes:
    return b"".join(f"data: {json.dumps(c)}\n\n".encode() for c in chunks)


def _mount(provider, body: bytes, status: int = 200):
    """Point a provider's httpx client at a canned response."""
    json_captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        json_captured["body"] = json.loads(request.content)
        json_captured["headers"] = dict(request.headers)
        return httpx.Response(status, content=body)

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        kwargs.pop("verify", None)
        return original(*args, **kwargs)

    return factory, json_captured


async def _collect(provider, monkeypatch, body: bytes, status: int = 200):
    factory, captured = _mount(provider, body, status)
    monkeypatch.setattr(httpx, "AsyncClient", factory)
    events = []
    async for event in provider.stream_turn(
        [NormalizedMessage(role="user", content="hello")],
        TOOLS,
        max_tokens=1024,
        temperature=0.0,
    ):
        events.append(event)
    return events, captured


class TestOpenAICompat:
    async def test_text_deltas(self, monkeypatch):
        provider = OpenAICompatProvider("http://localhost:11434/v1", "llama3")
        body = _sse(
            {"choices": [{"delta": {"content": "Hello"}}]},
            {"choices": [{"delta": {"content": " world"}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            {"usage": {"prompt_tokens": 12, "completion_tokens": 3}},
        )
        events, _ = await _collect(provider, monkeypatch, body)

        text = "".join(e.text for e in events if isinstance(e, TextDelta))
        assert text == "Hello world"
        usage = next(e for e in events if isinstance(e, Usage))
        assert (usage.prompt_tokens, usage.completion_tokens) == (12, 3)
        assert events[-1] == TurnEnd(stop_reason="stop", wants_tools=False)

    async def test_tool_call_fragments_are_reassembled(self, monkeypatch):
        """Arguments stream in pieces and must be joined before parsing."""
        provider = OpenAICompatProvider("http://localhost:11434/v1", "llama3")
        body = _sse(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_abc",
                                    "function": {"name": "list_hosts", "arguments": ""},
                                }
                            ]
                        }
                    }
                ]
            },
            {
                "choices": [
                    {"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"a'}}]}}
                ]
            },
            {
                "choices": [
                    {"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '":1}'}}]}}
                ]
            },
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        )
        events, _ = await _collect(provider, monkeypatch, body)

        calls = [e.call for e in events if isinstance(e, ToolCallEnd)]
        assert calls == [ToolCall(id="call_abc", name="list_hosts", arguments={"a": 1})]
        assert events[-1].wants_tools is True

    async def test_malformed_tool_arguments_raise(self, monkeypatch):
        provider = OpenAICompatProvider("http://localhost:11434/v1", "llama3")
        body = _sse(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "c1",
                                    "function": {"name": "list_hosts", "arguments": "{broken"},
                                }
                            ]
                        }
                    }
                ]
            },
        )
        with pytest.raises(LLMProviderError, match="invalid JSON"):
            await _collect(provider, monkeypatch, body)

    async def test_http_error_is_wrapped(self, monkeypatch):
        provider = OpenAICompatProvider("http://localhost:11434/v1", "llama3")
        with pytest.raises(LLMProviderError, match="HTTP 500"):
            await _collect(provider, monkeypatch, b"upstream exploded", status=500)

    async def test_missing_usage_is_flagged_unknown(self, monkeypatch):
        """A server that reports no usage must not silently bill as zero."""
        provider = OpenAICompatProvider("http://localhost:11434/v1", "llama3")
        body = _sse({"choices": [{"delta": {"content": "hi"}, "finish_reason": "stop"}]})
        events, _ = await _collect(provider, monkeypatch, body)
        assert next(e for e in events if isinstance(e, Usage)).unknown is True

    async def test_api_key_is_sent_as_bearer(self, monkeypatch):
        provider = OpenAICompatProvider("http://localhost:11434/v1", "llama3", api_key="secret-key")
        body = _sse({"choices": [{"delta": {}, "finish_reason": "stop"}]})
        _, captured = await _collect(provider, monkeypatch, body)
        assert captured["headers"]["authorization"] == "Bearer secret-key"


class TestAnthropic:
    async def test_text_deltas_and_usage(self, monkeypatch):
        provider = AnthropicProvider("claude-opus-5", api_key="sk-ant-test")
        body = _sse(
            {
                "type": "message_start",
                "message": {"usage": {"input_tokens": 20, "output_tokens": 0}},
            },
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "All "},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "healthy."},
            },
            {"type": "content_block_stop", "index": 0},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 7},
            },
            {"type": "message_stop"},
        )
        events, captured = await _collect(provider, monkeypatch, body)

        assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "All healthy."
        usage = next(e for e in events if isinstance(e, Usage))
        assert (usage.prompt_tokens, usage.completion_tokens) == (20, 7)
        assert captured["headers"]["x-api-key"] == "sk-ant-test"
        assert captured["headers"]["anthropic-version"] == "2023-06-01"

    async def test_tool_use_block(self, monkeypatch):
        provider = AnthropicProvider("claude-opus-5", api_key="sk-ant-test")
        body = _sse(
            {"type": "message_start", "message": {"usage": {"input_tokens": 5}}},
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "id": "toolu_1", "name": "list_hosts"},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '{"x"'},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": ": 2}"},
            },
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use"},
                "usage": {"output_tokens": 9},
            },
        )
        events, _ = await _collect(provider, monkeypatch, body)

        calls = [e.call for e in events if isinstance(e, ToolCallEnd)]
        assert calls == [ToolCall(id="toolu_1", name="list_hosts", arguments={"x": 2})]
        assert events[-1].wants_tools is True

    async def test_system_prompt_is_hoisted_out_of_messages(self):
        """Anthropic takes the system prompt as a top-level field."""
        provider = AnthropicProvider("claude-opus-5")
        system, wire = provider._to_wire(
            [
                NormalizedMessage(role="system", content="You are LabDog."),
                NormalizedMessage(role="user", content="hi"),
            ]
        )
        assert system == "You are LabDog."
        assert wire == [{"role": "user", "content": "hi"}]

    async def test_tool_results_merge_into_one_user_turn(self):
        """Parallel tool results belong in a single user message."""
        provider = AnthropicProvider("claude-opus-5")
        _, wire = provider._to_wire(
            [
                NormalizedMessage(role="user", content="check"),
                NormalizedMessage(
                    role="assistant",
                    tool_calls=[
                        ToolCall(id="t1", name="a", arguments={}),
                        ToolCall(id="t2", name="b", arguments={}),
                    ],
                ),
                NormalizedMessage(role="tool", content="r1", tool_call_id="t1"),
                NormalizedMessage(role="tool", content="r2", tool_call_id="t2"),
            ]
        )
        assert wire[-1]["role"] == "user"
        assert len(wire[-1]["content"]) == 2
        assert [b["tool_use_id"] for b in wire[-1]["content"]] == ["t1", "t2"]

    async def test_sampling_params_are_not_sent(self, monkeypatch):
        """Current Claude models reject temperature with a 400."""
        provider = AnthropicProvider("claude-opus-5", api_key="sk-ant-test")
        body = _sse({"type": "message_delta", "delta": {"stop_reason": "end_turn"}})
        _, captured = await _collect(provider, monkeypatch, body)
        assert "temperature" not in captured["body"]
        assert "top_p" not in captured["body"]


class TestEgressDetection:
    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:11434/v1",
            "http://127.0.0.1:8000/v1",
            "http://192.168.1.50:11434/v1",
            "http://10.0.0.5/v1",
            "http://172.16.0.1/v1",
            "http://172.31.255.1/v1",
            "http://ollama.local:11434/v1",
            "http://gpu.lan/v1",
        ],
    )
    def test_local_endpoints(self, url: str) -> None:
        assert is_local_endpoint(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://api.openai.com/v1",
            "https://openrouter.ai/api/v1",
            "https://api.anthropic.com",
            "http://172.32.0.1/v1",  # just outside RFC1918
            "http://172.15.0.1/v1",  # just below RFC1918
            "",
        ],
    )
    def test_remote_endpoints(self, url: str) -> None:
        assert is_local_endpoint(url) is False

    def test_none_is_treated_as_remote(self) -> None:
        """Unknown must not be mistaken for safe."""
        assert is_local_endpoint(None) is False
