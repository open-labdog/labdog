"""The Test button has to have a path for every backend.

Reported from a running instance: an operator switched a provider to the
agentic backend, pressed Test, and got

    Provider 'claude-cli' is driven by the Claude Agent SDK, which owns
    the agent loop itself. Sessions for it run through AgentSDKRunner,
    not AgentLoop; this call is a routing bug.

printed in red in the providers table. Two separate mistakes: the test
endpoint called ``build_provider()`` for a backend that deliberately has
no ``LLMProvider``, and the guard it hit was worded for whoever wrote it
rather than whoever would read it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ai.models import AIProvider
from app.ai.providers.base import LLMProviderError
from app.ai.providers.factory import build_provider


def _provider(provider_type: str) -> AIProvider:
    return AIProvider(name=f"test-{provider_type}", provider_type=provider_type, model="m")


class TestTheEndpointRoutesAroundBuildProvider:
    """The bug was structural: one call site that could not serve one of
    the backends it was reachable for."""

    def test_the_test_endpoint_checks_before_building(self) -> None:
        source = Path("app/api/ai.py").read_text()
        marker = "runs_on_agent_sdk(provider)"
        assert marker in source, "the test endpoint must branch on the SDK-backed types"
        # The branch has to come before build_provider, or it changes nothing.
        assert source.index(marker) < source.index("backend = build_provider(provider)")

    def test_every_backend_has_a_probe(self) -> None:
        """Enumerated from the stored provider types rather than a literal
        list, so a new backend cannot be added without one."""
        from app.ai.models import PROVIDER_TYPES
        from app.ai.providers.factory import _PROVIDER_CLASSES, SDK_BACKED_TYPES

        for provider_type in PROVIDER_TYPES:
            has_probe = provider_type in _PROVIDER_CLASSES or provider_type in SDK_BACKED_TYPES
            assert has_probe, f"{provider_type} has no way to answer the Test button"


class TestTheGuardIsReadable:
    """It reached an operator's screen once. Assume it can again."""

    def test_it_does_not_name_internal_classes(self) -> None:
        with pytest.raises(LLMProviderError) as err:
            build_provider(_provider("claude_agent"))
        message = str(err.value)
        for jargon in ("AgentSDKRunner", "AgentLoop", "routing bug"):
            assert jargon not in message, f"{jargon!r} means nothing to an operator"

    def test_it_says_whose_fault_it_is(self) -> None:
        """Otherwise the operator hunts through their own settings for a
        problem that is not there."""
        with pytest.raises(LLMProviderError) as err:
            build_provider(_provider("claude_agent"))
        assert "internal" in str(err.value).lower()

    def test_it_still_names_the_provider(self) -> None:
        with pytest.raises(LLMProviderError) as err:
            build_provider(_provider("claude_agent"))
        assert "test-claude_agent" in str(err.value)


class TestTheProbeIsHonest:
    """The single-shot backend shipped a Test that only checked the binary
    was present. It reported green against a stored value that was not a
    token, and the failure surfaced as a 401 mid-session. The lesson is
    that a probe must exercise the credential."""

    def test_it_sends_a_real_prompt(self) -> None:
        source = Path("app/ai/agent_sdk/probe.py").read_text()
        assert "PROBE_PROMPT" in source
        assert "client.query" in source

    def test_it_uses_the_stored_credential(self) -> None:
        source = Path("app/ai/agent_sdk/probe.py").read_text()
        assert "decrypt_api_key" in source
        assert "build_sdk_env" in source, "must go through the same env overlay a session uses"

    def test_it_withholds_built_in_tools(self) -> None:
        """A probe needs no tools, and the built-ins would be a shell in
        the container."""
        source = Path("app/ai/agent_sdk/probe.py").read_text()
        assert "NO_BUILTIN_TOOLS" in source

    def test_empty_output_is_a_failure(self) -> None:
        """Exit-zero-with-no-output is the shape a silent auth failure
        took on the single-shot backend."""
        source = Path("app/ai/agent_sdk/probe.py").read_text()
        assert "returned nothing" in source
