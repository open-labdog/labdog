"""A backend that cannot run tools must not be asked to investigate.

Observed live, and the reason this file exists. A chat session ran on the
single-shot CLI backend. The loop correctly withheld the tools — but the
system prompt still said "start by finding out what is in scope with
list_hosts" and "base every claim on something a tool actually returned".
The model followed the instruction it was given, the CLI rendered the
absent tool result as the literal word ``undefined``, and the model
supplied the rest itself:

    {"hostname": "labdog-prod-01", "address": "10.0.10.11",
     "roles": ["docker-host", "media-server", ...],
     "notes": "Primary always-on box. Runs Jellyfin, *arr stack, ..."}

No such host exists. The real ones live on 10.10.10.x — one character
away — and the fabrication name-dropped a service the operator really
runs, which is what made it convincing. The session was badged
**succeeded** in green.

Withholding tools is not the same as refusing the work. These tests pin
the refusal.
"""

from __future__ import annotations

import pytest

from app.ai.models import AIProvider
from app.ai.providers.factory import supports_tools
from app.ai.service import AIDisabledError, assert_can_investigate


def _provider(provider_type: str) -> AIProvider:
    return AIProvider(name=f"test-{provider_type}", provider_type=provider_type, model="m")


class TestCapabilityLookup:
    def test_cli_cannot_run_tools(self) -> None:
        assert supports_tools(_provider("claude_cli")) is False

    @pytest.mark.parametrize("provider_type", ["openai_compat", "anthropic"])
    def test_the_http_backends_can(self, provider_type: str) -> None:
        assert supports_tools(_provider(provider_type)) is True

    def test_an_unknown_type_is_assumed_incapable(self) -> None:
        """Fails toward refusing a session rather than running one whose
        output cannot be trusted."""
        assert supports_tools(_provider("something-new")) is False

    def test_it_reads_the_provider_classes(self) -> None:
        """Guards against the map drifting from the classes it mirrors: a
        hardcoded answer here would keep passing after a backend gained or
        lost tool support."""
        from app.ai.providers.claude_cli import ClaudeCLIProvider
        from app.ai.providers.factory import _PROVIDER_CLASSES

        assert _PROVIDER_CLASSES["claude_cli"] is ClaudeCLIProvider
        assert supports_tools(_provider("claude_cli")) is ClaudeCLIProvider.supports_tools


class TestInvestigationIsRefused:
    def test_a_toolless_provider_is_rejected(self) -> None:
        with pytest.raises(AIDisabledError) as err:
            assert_can_investigate(_provider("claude_cli"))
        message = str(err.value)
        assert "cannot run tools" in message
        assert "Anthropic" in message, "should name a backend that would work"

    @pytest.mark.parametrize("provider_type", ["openai_compat", "anthropic"])
    def test_a_capable_provider_passes(self, provider_type: str) -> None:
        assert_can_investigate(_provider(provider_type))


class TestLoopGuard:
    """The API blocks this at session creation, but scheduled runs do not
    go through the API — so the loop has to refuse it too."""

    def test_investigative_modes_are_named(self) -> None:
        from app.ai.loop import INVESTIGATIVE_MODES

        assert "chat" in INVESTIGATIVE_MODES
        assert "scheduled" in INVESTIGATIVE_MODES
        assert "alert_investigation" in INVESTIGATIVE_MODES

    def test_verify_is_not_investigative(self) -> None:
        """A verify step is handed its evidence and returns a verdict on it.
        It looks nothing up, so a single-shot backend serves it — that is
        the case the CLI backend exists for, and refusing it would leave
        that backend with no purpose at all."""
        from app.ai.loop import INVESTIGATIVE_MODES

        assert "verify" not in INVESTIGATIVE_MODES

    def test_the_loop_checks_before_running(self) -> None:
        import inspect

        from app.ai.loop import AgentLoop

        source = inspect.getsource(AgentLoop.run)
        assert "INVESTIGATIVE_MODES" in source
        assert "supports_tools" in source
        # The guard has to come before the model is ever called.
        assert source.index("INVESTIGATIVE_MODES") < source.index("while True")

    def test_the_session_is_failed_not_succeeded(self) -> None:
        """Recording it as succeeded is what let fabricated output pass for
        a finished investigation."""
        import inspect

        from app.ai.loop import AgentLoop

        source = inspect.getsource(AgentLoop.run)
        guard = source[source.index("INVESTIGATIVE_MODES") :]
        assert 'status = "failed"' in guard[:600]


class TestApiRefusesAtCreation:
    def test_create_session_calls_the_guard(self) -> None:
        from pathlib import Path

        source = Path("app/api/ai.py").read_text()
        assert "assert_can_investigate(provider)" in source
