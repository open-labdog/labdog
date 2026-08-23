"""A scripted stand-in for ``ClaudeSDKClient``.

The runner is injected with a client factory so these tests exercise the
real control flow — the permission gate, tool dispatch, transcript
writing, usage accounting and cancellation — without spawning the Claude
Code binary or reaching the network.

The fake speaks the same four things the runner uses: the async context
manager, :meth:`query`, :meth:`receive_response`, and :meth:`interrupt`.
Messages come from the SDK's real classes, so a change to their shape
fails here rather than in production.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock


def assistant(text: str, usage: dict | None = None) -> AssistantMessage:
    """One assistant turn.

    ``usage`` mirrors the real per-response block the SDK copies out of
    ``data["message"]["usage"]``. It defaults to absent because most tests
    do not care, but it is the only way to exercise the token cap: the
    runner's live estimate is summed from these, and a script whose turns
    all report nothing can never reach a limit.
    """
    return AssistantMessage(content=[TextBlock(text=text)], model="fake-model", usage=usage)


#: Distinguishes "caller said nothing about usage" from "the backend
#: reported none". The second is a real case — it sets ``cost_unknown`` —
#: so a plain ``None`` default would make it untestable.
_UNSET = object()


def result(
    session_id: str = "fake-session-id",
    usage: dict | None | Any = _UNSET,
    is_error: bool = False,
    result_text: str | None = None,
    subtype: str = "success",
    num_turns: int = 1,
) -> ResultMessage:
    return ResultMessage(
        subtype=subtype,
        duration_ms=1,
        duration_api_ms=1,
        is_error=is_error,
        num_turns=num_turns,
        session_id=session_id,
        total_cost_usd=0.0,
        usage={"input_tokens": 10, "output_tokens": 5} if usage is _UNSET else usage,
        result=result_text,
    )


class FakeSDKClient:
    """Replays a fixed message script.

    ``on_query`` lets a test drive tool calls: it runs once the prompt is
    submitted, which is where the real SDK would decide to call a tool.
    """

    def __init__(
        self,
        messages: list[Any],
        *,
        on_query: Callable[[], Any] | None = None,
    ) -> None:
        self._messages = messages
        self._on_query = on_query
        self.options: Any = None
        self.prompts: list[str] = []
        self.interrupted = False
        self.entered = False

    def factory(self, options: Any = None) -> FakeSDKClient:
        """Match ``ClaudeSDKClient(options=...)``."""
        self.options = options
        return self

    async def __aenter__(self) -> FakeSDKClient:
        self.entered = True
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def query(self, prompt: str) -> None:
        self.prompts.append(prompt)
        if self._on_query is not None:
            await self._on_query()

    async def receive_response(self):
        for message in self._messages:
            yield message

    async def interrupt(self) -> None:
        self.interrupted = True
