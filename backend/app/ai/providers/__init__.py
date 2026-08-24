"""LLM provider backends.

One streaming interface (:mod:`app.ai.providers.base`), three
implementations:

- ``openai_compat`` — any OpenAI-compatible ``/chat/completions`` server
  (Ollama, vLLM, LM Studio, OpenRouter, OpenAI itself)
- ``anthropic`` — the Anthropic Messages API, with native tool_use blocks
- ``claude_cli`` — the locally installed Claude Code CLI

All three speak HTTP via httpx or a subprocess; no vendor SDK is
required, which keeps a three-backend integration on one dependency-free
code path rather than one SDK plus two hand-rolled clients.
"""

from app.ai.providers.base import (
    LLMProvider,
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
from app.ai.providers.factory import build_provider

__all__ = [
    "LLMProvider",
    "LLMProviderError",
    "NormalizedMessage",
    "StreamEvent",
    "TextDelta",
    "ToolCall",
    "ToolCallEnd",
    "ToolSpec",
    "TurnEnd",
    "Usage",
    "build_provider",
]
