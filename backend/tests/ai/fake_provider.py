"""A scripted provider for driving the agent loop in tests.

Implements the same streaming interface as the real backends, so loop
tests exercise the real control flow — tool dispatch, usage accounting,
cap checks — without a network call or a running model.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field

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
)


@dataclass
class ScriptedTurn:
    """One assistant turn the fake provider will produce."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 100
    completion_tokens: int = 50
    usage_unknown: bool = False
    stop_reason: str = "end_turn"
    #: Raised instead of yielding, to exercise provider-failure handling.
    error: str | None = None


class FakeProvider:
    """Replays ``turns`` in order, one per ``stream_turn`` call."""

    provider_type = "fake"

    def __init__(
        self, turns: Sequence[ScriptedTurn], *, supports_tools: bool = True
    ) -> None:
        self._turns = list(turns)
        self.supports_tools = supports_tools
        #: Every (messages, tools) pair the loop sent, for assertions.
        self.calls: list[tuple[list[NormalizedMessage], list[ToolSpec]]] = []

    async def stream_turn(
        self,
        messages: list[NormalizedMessage],
        tools: list[ToolSpec],
        *,
        max_tokens: int,
        temperature: float,
    ) -> AsyncIterator[StreamEvent]:
        self.calls.append((list(messages), list(tools)))

        if not self._turns:
            # Running dry means the loop iterated more than the test scripted;
            # ending the turn cleanly keeps that visible as an assertion
            # failure rather than a hang.
            yield TextDelta("(no further scripted turns)")
            yield Usage(prompt_tokens=0, completion_tokens=0)
            yield TurnEnd(stop_reason="end_turn")
            return

        turn = self._turns.pop(0)
        if turn.error:
            raise LLMProviderError(turn.error)

        if turn.text:
            yield TextDelta(turn.text)
        for call in turn.tool_calls:
            yield ToolCallEnd(call)
        yield Usage(
            prompt_tokens=turn.prompt_tokens,
            completion_tokens=turn.completion_tokens,
            unknown=turn.usage_unknown,
        )
        yield TurnEnd(
            stop_reason=turn.stop_reason, wants_tools=bool(turn.tool_calls)
        )

    async def test_connection(self) -> str:
        return "fake provider"


def call(name: str, call_id: str = "call_1", **arguments) -> ToolCall:
    """Shorthand for building a scripted tool call."""
    return ToolCall(id=call_id, name=name, arguments=arguments)
