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

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.ai.providers.base import LLMProviderError
from app.ai.providers.claude_cli import (
    CONFIG_DIR_ENV,
    DEFAULT_CONFIG_DIR,
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


class TestStoredCredentialTrap:
    """A credentials file outranks the token too, not just the env vars.

    Verified against claude 2.1.220: with a bogus `CLAUDE_CODE_OAUTH_TOKEN`
    and a HOME containing no login, the CLI used the token and returned 401.
    With the *same* bogus token and a HOME containing a real
    `.claude/.credentials.json`, it succeeded — the stored login won and the
    configured token was ignored, with no warning and no visible difference
    in the output. Identical failure signature to the env-var trap: LabDog
    would silently authenticate as somebody else's account.

    `CLAUDE_CONFIG_DIR` is what closes it; pointing it at an empty directory
    restored the 401.
    """

    def test_config_dir_is_isolated_when_a_token_is_configured(self) -> None:
        env = build_cli_env("tok", base={})
        assert env[CONFIG_DIR_ENV] == DEFAULT_CONFIG_DIR

    def test_isolation_overrides_an_inherited_config_dir(self) -> None:
        """An inherited value pointing at a home with a login would reopen
        the trap, so LabDog's value has to win."""
        env = build_cli_env("tok", base={CONFIG_DIR_ENV: "/home/someone/.claude"})
        assert env[CONFIG_DIR_ENV] == DEFAULT_CONFIG_DIR

    def test_config_dir_is_untouched_when_no_token_is_configured(self) -> None:
        """Without a token the intended setup *is* the host's own login, so
        isolating the config directory would break the only thing that case
        can use to authenticate."""
        base = {CONFIG_DIR_ENV: "/home/labdog/.claude"}
        assert build_cli_env(None, base=base) == base
        assert CONFIG_DIR_ENV not in build_cli_env(None, base={})

    def test_the_isolated_directory_lives_under_labdog_state(self) -> None:
        """It has to be somewhere both deployment shapes can write:
        `ReadWritePaths` in the systemd unit, and the documented volume
        mount in a container. Anywhere else and one of the two breaks."""
        assert DEFAULT_CONFIG_DIR.startswith("/var/lib/labdog/")


class TestConfigDirCreation:
    def test_directory_is_created_when_a_token_is_configured(self, tmp_path) -> None:
        target = tmp_path / "claude-cli"
        ClaudeCLIProvider(oauth_token="tok", config_dir=str(target))._ensure_config_dir()
        assert target.is_dir()

    def test_nothing_is_created_without_a_token(self, tmp_path) -> None:
        """That case never uses the directory — creating it would litter the
        filesystem of every operator using a host login."""
        target = tmp_path / "claude-cli"
        ClaudeCLIProvider(config_dir=str(target))._ensure_config_dir()
        assert not target.exists()

    def test_a_failure_to_create_does_not_raise(self, tmp_path) -> None:
        """A read-only filesystem must not stop a session from starting —
        the CLI runs fine without persisted state."""
        blocked = tmp_path / "file-in-the-way"
        blocked.write_text("not a directory")
        ClaudeCLIProvider(
            oauth_token="tok", config_dir=str(blocked / "nested")
        )._ensure_config_dir()


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


class TestFailureDetail:
    """What the operator sees when the CLI exits non-zero.

    Verified against claude 2.1.220: in ``-p`` mode the CLI prints errors —
    including ``Failed to authenticate. API Error: 401`` for a bad token —
    to *stdout* and exits 1 with stderr empty. An error built from stderr
    alone reads "claude exited 1:" with nothing after the colon, which is
    useless at exactly the moment the operator mistypes a token.
    """

    @staticmethod
    def _fake_exec(returncode: int, stdout: bytes, stderr: bytes):
        proc = AsyncMock()
        proc.returncode = returncode
        proc.communicate = AsyncMock(return_value=(stdout, stderr))
        return AsyncMock(return_value=proc)

    async def _stream(self, monkeypatch, *, stdout: bytes, stderr: bytes) -> str:
        monkeypatch.setattr(
            asyncio,
            "create_subprocess_exec",
            self._fake_exec(1, stdout, stderr),
        )
        provider = ClaudeCLIProvider()
        from app.ai.providers.base import NormalizedMessage

        with pytest.raises(LLMProviderError) as err:
            async for _ in provider.stream_turn(
                [NormalizedMessage(role="user", content="hi")],
                [],
                max_tokens=100,
                temperature=0.0,
            ):
                pass
        return str(err.value)

    async def test_stdout_is_used_when_stderr_is_empty(self, monkeypatch) -> None:
        message = await self._stream(
            monkeypatch,
            stdout=b"Failed to authenticate. API Error: 401 OAuth access token is invalid.\n",
            stderr=b"",
        )
        assert "401" in message
        assert "authenticate" in message

    async def test_stderr_still_wins_when_present(self, monkeypatch) -> None:
        message = await self._stream(
            monkeypatch,
            stdout=b"partial output before the crash",
            stderr=b"segfault details",
        )
        assert "segfault details" in message
        assert "partial output" not in message


class TestConnectionActuallyAuthenticates:
    """`Test` has to be able to fail for the reason it usually fails.

    It used to run `claude --version` and nothing else, which authenticates
    against nothing. A provider holding a completely invalid token reported
    "Found /usr/local/bin/claude (2.1.220); single-shot mode" in green, and
    the first sign of trouble was a session dying with a 401 — with the
    green test standing behind the broken credential.
    """

    def test_it_sends_a_real_prompt(self) -> None:
        import inspect

        from app.ai.providers.claude_cli import ClaudeCLIProvider

        source = inspect.getsource(ClaudeCLIProvider.test_connection)
        assert "_argv(" in source, (
            "test_connection must make an authenticated call, not just --version"
        )

    def test_the_failure_message_distinguishes_auth_from_a_missing_binary(self) -> None:
        import inspect

        from app.ai.providers.claude_cli import ClaudeCLIProvider

        source = inspect.getsource(ClaudeCLIProvider.test_connection)
        assert "authentication failed" in source


class TestSubscriptionTokenFormat:
    """Guards the field against the wrong secret being pasted into it.

    Seen in practice: a 92-character non-Anthropic secret stored happily,
    then `401 Invalid bearer token` at session time, with the provider test
    still green.
    """

    def test_the_check_exists_and_names_the_command(self) -> None:
        from pathlib import Path

        source = Path("app/api/ai.py").read_text()
        assert "SUBSCRIPTION_TOKEN_PREFIX" in source
        assert "claude setup-token" in source, (
            "the error should say how to get a real token, not just reject one"
        )

    def test_it_is_applied_on_both_create_and_update(self) -> None:
        from pathlib import Path

        source = Path("app/api/ai.py").read_text()
        assert source.count("_check_subscription_token(") >= 3, (
            "expected the helper plus a call on create and on update"
        )

    def test_a_blank_token_is_still_allowed(self) -> None:
        """Blank means 'use the host's own login' and must keep working."""
        from app.api.ai import _check_subscription_token

        _check_subscription_token("claude_cli", None)
        _check_subscription_token("claude_cli", "")

    def test_a_real_looking_token_passes(self) -> None:
        from app.api.ai import _check_subscription_token

        _check_subscription_token("claude_cli", "sk-ant-oat01-abc123")

    def test_another_providers_key_is_not_checked(self) -> None:
        """Only the CLI field carries a subscription token; an Anthropic API
        key must not be measured against this prefix."""
        from app.api.ai import _check_subscription_token

        _check_subscription_token("anthropic", "sk-ant-api03-whatever")

    def test_a_wrong_secret_is_rejected(self) -> None:
        from fastapi import HTTPException

        from app.api.ai import _check_subscription_token

        with pytest.raises(HTTPException) as err:
            _check_subscription_token("claude_cli", "Nj4pvdSQwzI2jZsomethingelse")
        assert err.value.status_code == 400
        assert "setup-token" in str(err.value.detail)


class TestMissingBinary:
    """The message an operator sees when the CLI is not installed.

    The cause differs by deployment shape — a package install expects the
    operator to supply the binary, while the official image bundles it, so
    a container hitting this is on a custom or outdated image. A bare "not
    found" sends a container user looking in the wrong place entirely.
    """

    def test_message_covers_both_deployment_shapes(self) -> None:
        from app.ai.providers.claude_cli import NOT_FOUND_MESSAGE

        lowered = NOT_FOUND_MESSAGE.lower()
        assert "container" in lowered, "container users get no guidance"
        assert "anthropic" in lowered, "no pointer to the provider that needs no binary"
        assert "docs/ui/assistant.md" in NOT_FOUND_MESSAGE

    def test_message_does_not_claim_the_image_lacks_the_binary(self) -> None:
        """It did lack it until the CLI was added to the Dockerfile. Leaving
        that claim in place would send operators to build a derived image
        they no longer need."""
        from app.ai.providers.claude_cli import NOT_FOUND_MESSAGE

        assert "does not include" not in NOT_FOUND_MESSAGE.lower()
        assert "extend the image" not in NOT_FOUND_MESSAGE.lower()


class TestAnthropicKeyIsRequired:
    """An Anthropic provider without a key is guaranteed to fail.

    The API always authenticates, so a keyless provider saves fine and then
    401s from inside a session — far from the form where the cause is
    visible. These pin the validation that rejects it at save time.

    Exercised against the API in the DB-backed suite; here we assert the
    endpoint carries the checks at all, so deleting one is a test failure
    rather than a silent regression.
    """

    def test_create_rejects_a_missing_key(self) -> None:
        from pathlib import Path

        source = Path("app/api/ai.py").read_text()
        assert 'payload.provider_type == "anthropic" and not payload.api_key' in source
        assert "platform.claude.com" in source, (
            "the error should say where to get a key, not just that one is missing"
        )

    def test_update_rejects_clearing_the_key(self) -> None:
        from pathlib import Path

        source = Path("app/api/ai.py").read_text()
        assert 'api_key == "" and provider.provider_type == "anthropic"' in source
