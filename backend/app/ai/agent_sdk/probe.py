"""Does this provider actually work?

The Test button's job is to answer that before a session does. It is
worth being strict about what "work" means here, because the single-shot
backend already taught this lesson the expensive way: a Test that only
checked the binary was on PATH reported green while the stored token was
not a token at all, and the failure surfaced later as a 401 in the middle
of a session an operator had been told was fine.

So this runs a real prompt through the real binary with the real stored
credentials, along the same path a session takes — the same environment
overlay, the same withheld built-ins. Anything less is a claim about
configuration rather than about whether it works.
"""

from __future__ import annotations

import asyncio
import logging

from app.ai.agent_sdk.bridge import NO_BUILTIN_TOOLS
from app.ai.agent_sdk.environment import build_sdk_env, ensure_state_dir
from app.ai.models import AIProvider
from app.ai.providers.base import LLMProviderError
from app.ai.providers.claude_cli import DEFAULT_CONFIG_DIR
from app.ai.providers.factory import decrypt_api_key

logger = logging.getLogger(__name__)

#: Cheap and unambiguous: one word back means the whole chain worked.
PROBE_PROMPT = "Reply with the single word: ok"

#: Generous. A cold start pays for spawning the binary and the first
#: round-trip, and a Test that times out on a slow link reads as broken.
PROBE_TIMEOUT_SECONDS = 120


def bundled_cli_version() -> str:
    """The Claude Code build the installed SDK ships and expects."""
    try:
        from claude_agent_sdk._cli_version import __cli_version__

        return str(__cli_version__)
    except Exception:  # pragma: no cover - version metadata is best-effort
        return "unknown version"


async def test_connection(provider: AIProvider) -> str:
    """Run one real prompt with the stored credentials.

    Returns the line shown next to the provider on success. Raises
    :class:`LLMProviderError` with something an operator can act on
    otherwise.
    """
    from app.ai.agent_sdk import UNAVAILABLE_MESSAGE, sdk_available

    if not sdk_available():
        raise LLMProviderError(UNAVAILABLE_MESSAGE)

    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ClaudeSDKClient,
        ResultMessage,
        TextBlock,
    )

    options = ClaudeAgentOptions(
        # No tools at all for the probe. It has nothing to look up, and
        # the built-ins would be a shell in the container either way.
        tools=NO_BUILTIN_TOOLS,
        allowed_tools=[],
        strict_mcp_config=True,
        setting_sources=[],
        model=provider.model or None,
        max_turns=1,
        env=build_sdk_env(decrypt_api_key(provider), DEFAULT_CONFIG_DIR),
        cwd=ensure_state_dir(DEFAULT_CONFIG_DIR),
        system_prompt="Answer in one word.",
    )

    text = ""
    failure: str | None = None
    try:
        async with asyncio.timeout(PROBE_TIMEOUT_SECONDS):
            async with ClaudeSDKClient(options=options) as client:
                await client.query(PROBE_PROMPT)
                async for message in client.receive_response():
                    if isinstance(message, AssistantMessage):
                        text += "".join(b.text for b in message.content if isinstance(b, TextBlock))
                    elif isinstance(message, ResultMessage) and message.is_error:
                        failure = (
                            str(message.result or "").strip() or "the backend reported an error"
                        )
    except TimeoutError as exc:
        raise LLMProviderError(
            f"Claude Code did not answer within {PROBE_TIMEOUT_SECONDS}s. "
            "Check that the container can reach api.anthropic.com."
        ) from exc
    except LLMProviderError:
        raise
    except Exception as exc:
        logger.warning("agent sdk probe failed for provider %s", provider.id, exc_info=True)
        detail = str(exc).strip() or exc.__class__.__name__
        raise LLMProviderError(f"Claude Code could not be started: {detail}") from exc

    version = bundled_cli_version()
    if failure:
        raise LLMProviderError(f"Claude Code {version} started, but the request failed: {failure}")
    if not text.strip():
        # Exit without output is the shape the single-shot backend's
        # silent-auth-failure took. Treat it as a failure, not a pass.
        raise LLMProviderError(
            f"Claude Code {version} started but returned nothing. That usually means "
            "the subscription token was rejected — regenerate it with "
            "'claude setup-token' and paste the value it prints last."
        )

    return (
        f"Claude Code {version} via the Agent SDK; authenticated; "
        f"tools enabled, so this provider can run investigations."
    )
