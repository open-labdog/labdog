"""Subscription auth for the Claude CLI backend.

The CLI resolves credentials in a fixed order, and `CLAUDE_CODE_OAUTH_TOKEN`
sits *below* `ANTHROPIC_API_KEY` in it. So a host that has an API key in its
environment — perfectly reasonable, since the Anthropic HTTP provider wants
one — would silently bill the API account instead of the subscription the
operator configured. No error, no warning, no difference in the output: the
only symptom is the invoice.

These tests pin the two properties that prevent that: the token reaches the
subprocess, and nothing that outranks it survives alongside it.
"""

from __future__ import annotations

from app.ai.providers.claude_cli import (
    OAUTH_TOKEN_ENV,
    OVERRIDING_CREDENTIAL_ENV,
    ClaudeCLIProvider,
    build_cli_env,
)


class TestTokenInjection:
    def test_token_is_passed_to_the_subprocess(self) -> None:
        env = build_cli_env("sk-ant-oat01-example", base={"PATH": "/usr/bin"})
        assert env[OAUTH_TOKEN_ENV] == "sk-ant-oat01-example"

    def test_the_rest_of_the_environment_survives(self) -> None:
        """Dropping unrelated variables would break PATH, HOME, and the
        proxy settings the CLI needs to reach the network at all."""
        base = {"PATH": "/usr/bin", "HOME": "/var/lib/labdog", "HTTPS_PROXY": "http://p"}
        env = build_cli_env("tok", base=base)
        for key, value in base.items():
            assert env[key] == value

    def test_no_token_leaves_the_environment_untouched(self) -> None:
        """A host where the CLI is already logged in must keep working."""
        base = {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "sk-ant-api-key"}
        assert build_cli_env(None, base=base) == base
        assert build_cli_env("", base=base) == base


class TestPrecedenceTrap:
    """`ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` both outrank the OAuth
    token in the CLI's resolution order."""

    def test_api_key_is_removed_when_a_token_is_configured(self) -> None:
        env = build_cli_env("tok", base={"ANTHROPIC_API_KEY": "sk-ant-api-key"})
        assert "ANTHROPIC_API_KEY" not in env
        assert env[OAUTH_TOKEN_ENV] == "tok"

    def test_auth_token_is_removed_when_a_token_is_configured(self) -> None:
        env = build_cli_env("tok", base={"ANTHROPIC_AUTH_TOKEN": "bearer-thing"})
        assert "ANTHROPIC_AUTH_TOKEN" not in env

    def test_every_overriding_credential_is_covered(self) -> None:
        """Guards the constant itself: adding a name to the tuple without
        removing it here would leave a silent billing override in place."""
        base = dict.fromkeys(OVERRIDING_CREDENTIAL_ENV, "value")
        env = build_cli_env("tok", base=base)
        assert not [name for name in OVERRIDING_CREDENTIAL_ENV if name in env]

    def test_an_api_key_alone_still_wins_when_no_token_is_set(self) -> None:
        """Removing the API key unconditionally would break every operator
        who deliberately authenticates the CLI that way."""
        env = build_cli_env(None, base={"ANTHROPIC_API_KEY": "sk-ant-api-key"})
        assert env["ANTHROPIC_API_KEY"] == "sk-ant-api-key"


class TestProviderWiring:
    def test_provider_exposes_the_sanitised_env(self, monkeypatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-key")
        provider = ClaudeCLIProvider(model="claude-opus-5", oauth_token="tok")
        env = provider.env
        assert env[OAUTH_TOKEN_ENV] == "tok"
        assert "ANTHROPIC_API_KEY" not in env

    def test_provider_without_a_token_inherits_the_process_env(self, monkeypatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-key")
        provider = ClaudeCLIProvider(model="claude-opus-5")
        assert provider.env["ANTHROPIC_API_KEY"] == "sk-ant-api-key"
        assert OAUTH_TOKEN_ENV not in provider.env

    def test_the_token_is_never_placed_on_the_command_line(self) -> None:
        """argv is world-readable via /proc; the token belongs in the
        environment, which is not."""
        provider = ClaudeCLIProvider(model="claude-opus-5", oauth_token="sekrit")
        assert "sekrit" not in " ".join(provider._argv("hello"))
