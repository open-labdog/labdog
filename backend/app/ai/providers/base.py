"""The provider interface every LLM backend implements.

The wire formats differ (OpenAI's ``tool_calls`` deltas, Anthropic's
``tool_use`` content blocks, the Claude CLI's stream-json envelopes), so
each provider translates to the normalised shapes here. Everything above
this module — the loop, the transcript, the UI — only ever sees these.

``NormalizedMessage`` is also the shape persisted in ``ai_messages``, so
a session survives being pointed at a different backend mid-conversation.
"""

from __future__ import annotations

import ssl
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

Role = Literal["system", "user", "assistant", "tool"]


class LLMProviderError(Exception):
    """Transport, protocol, or provider-side error.

    Never carries the API key: message text is built from status codes and
    provider error bodies, both of which the caller is free to log.
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class ToolCall:
    """One resolved tool invocation requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class NormalizedMessage:
    """A single turn of conversation, in the shape stored in ``ai_messages``."""

    role: Role
    content: str = ""
    # Populated on assistant turns that requested tools.
    tool_calls: list[ToolCall] = field(default_factory=list)
    # Set on role="tool" turns — which call this result answers.
    tool_call_id: str | None = None


@dataclass(frozen=True)
class ToolSpec:
    """A capability offered to the model, as JSON Schema."""

    name: str
    description: str
    parameters: dict[str, Any]


# --- stream events -------------------------------------------------------
# The loop consumes these; a subset is forwarded to the UI over SSE.


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolCallEnd:
    """A tool call finished streaming and its arguments parsed cleanly."""

    call: ToolCall


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int
    # True when the backend could not report usage, so the loop can mark the
    # session's cost as a floor rather than an estimate.
    unknown: bool = False


@dataclass(frozen=True)
class TurnEnd:
    """The assistant turn is complete.

    ``stop_reason`` is the provider's own value, normalised only far enough
    to distinguish "wants to run tools" from everything else.
    """

    stop_reason: str
    wants_tools: bool = False


StreamEvent = TextDelta | ToolCallEnd | Usage | TurnEnd


@runtime_checkable
class LLMProvider(Protocol):
    """A backend that can run one assistant turn and stream the result."""

    #: Human-readable backend name, for error messages and the UI.
    provider_type: str

    #: False when the backend cannot round-trip tool calls, in which case the
    #: loop restricts it to single-shot use (reports and verify verdicts).
    supports_tools: bool

    def stream_turn(
        self,
        messages: list[NormalizedMessage],
        tools: list[ToolSpec],
        *,
        max_tokens: int,
        temperature: float,
    ) -> AsyncIterator[StreamEvent]:
        """Run one assistant turn, yielding events as they arrive."""
        ...


def build_ssl_verify(verify_ssl: bool, ca_cert_pem: str | None) -> bool | ssl.SSLContext:
    """Resolve httpx's ``verify`` argument from the stored TLS settings.

    Mirrors :meth:`app.grafana.client.PrometheusClient._get_ssl_context` so
    self-signed internal endpoints work the same way across integrations.
    """
    if not verify_ssl:
        return False
    if ca_cert_pem:
        try:
            return ssl.create_default_context(cadata=ca_cert_pem)
        except Exception as exc:
            raise LLMProviderError(f"Invalid CA certificate: {exc}") from exc
    return True
