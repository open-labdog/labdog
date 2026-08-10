"""The credential-precedence trap, in its Agent SDK form.

``ANTHROPIC_API_KEY`` and ``ANTHROPIC_AUTH_TOKEN`` outrank
``CLAUDE_CODE_OAUTH_TOKEN``. The single-shot backend handles that by
building a complete environment and deleting them, which works because it
owns the whole environment it passes to ``subprocess_exec``.

The SDK composes the child environment as ``{**os.environ, **options.env}``,
so ``options.env`` is an *overlay* and a deletion from it deletes nothing —
the inherited value survives, and it is exactly the value that would take
precedence over the token LabDog configured. Verified against the real
binary: with a bogus key inherited and no override the session never
authenticated; with an empty-string override it authenticated normally.

These tests exist because the failure is silent in the direction that
matters — the session would run, and bill, as the wrong account.
"""

from __future__ import annotations

from app.ai.agent_sdk.environment import build_sdk_env, ensure_state_dir
from app.ai.providers.claude_cli import (
    CONFIG_DIR_ENV,
    DEFAULT_CONFIG_DIR,
    OAUTH_TOKEN_ENV,
    OVERRIDING_CREDENTIAL_ENV,
)

TOKEN = "sk-ant-oat01-not-a-real-token"


class TestWithAToken:
    def test_the_token_is_injected(self) -> None:
        assert build_sdk_env(TOKEN)[OAUTH_TOKEN_ENV] == TOKEN

    def test_the_config_dir_is_pinned(self) -> None:
        """Closes the other half of the trap: a login stored on disk
        outranks the token just as an env var does."""
        assert build_sdk_env(TOKEN)[CONFIG_DIR_ENV] == DEFAULT_CONFIG_DIR

    def test_a_custom_config_dir_is_honoured(self) -> None:
        assert build_sdk_env(TOKEN, "/tmp/elsewhere")[CONFIG_DIR_ENV] == "/tmp/elsewhere"

    def test_an_inherited_credential_is_blanked_not_dropped(self) -> None:
        """Dropping it from an overlay leaves the inherited value in place.

        The assertion is deliberately about the key being *present* and
        empty — a test that only checked the value would pass if the key
        vanished, which is the bug.
        """
        env = build_sdk_env(TOKEN, inherited={"ANTHROPIC_API_KEY": "sk-ant-leaked"})
        assert "ANTHROPIC_API_KEY" in env, "an absent key means the inherited one survives"
        assert env["ANTHROPIC_API_KEY"] == ""

    def test_every_overriding_variable_is_covered(self) -> None:
        """Pinned against the list the single-shot backend already knows
        about, so a variable added there is not silently missed here."""
        inherited = {name: "leaked" for name in OVERRIDING_CREDENTIAL_ENV}
        env = build_sdk_env(TOKEN, inherited=inherited)
        for name in OVERRIDING_CREDENTIAL_ENV:
            assert env.get(name) == "", f"{name} would still outrank the configured token"

    def test_a_variable_the_host_never_set_is_left_alone(self) -> None:
        """Nothing to neutralise, and a blank entry would only make a
        debug dump harder to read."""
        env = build_sdk_env(TOKEN, inherited={})
        assert "ANTHROPIC_API_KEY" not in env

    def test_it_does_not_smuggle_the_rest_of_the_environment(self) -> None:
        """An overlay, not a replacement: returning os.environ here would
        pin unrelated host state into the child for no reason."""
        env = build_sdk_env(TOKEN, inherited={"PATH": "/usr/bin", "SECRET": "x"})
        assert "PATH" not in env
        assert "SECRET" not in env


class TestStateDir:
    """Reported from a running instance: the Test button died with
    ``CLIConnectionError: Working directory does not exist``. The SDK
    refuses to spawn at all when ``cwd`` is missing, and only the container
    image creates this path — a package install or a dev checkout has no
    reason to."""

    def test_it_creates_a_missing_directory(self, tmp_path) -> None:
        target = tmp_path / "claude-cli"
        assert not target.exists()
        assert ensure_state_dir(str(target)) == str(target)
        assert target.is_dir()

    def test_an_existing_directory_is_returned_unchanged(self, tmp_path) -> None:
        target = tmp_path / "already-there"
        target.mkdir()
        assert ensure_state_dir(str(target)) == str(target)

    def test_it_returns_none_when_it_cannot_create_one(self, tmp_path) -> None:
        """None tells the caller to omit cwd and inherit the process's own
        working directory, which is safe because sessions load nothing from
        it (setting_sources=[]). Raising here would make an unwritable
        state directory fatal to a backend that would otherwise work."""
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("this is a file")
        assert ensure_state_dir(str(blocker / "child")) is None

    def test_it_does_not_raise_on_a_permission_error(self, tmp_path) -> None:
        locked = tmp_path / "locked"
        locked.mkdir(mode=0o500)
        try:
            assert ensure_state_dir(str(locked / "child")) is None
        finally:
            locked.chmod(0o700)


class TestWithoutAToken:
    """The operator authenticated Claude Code on the host and wants
    LabDog to use that login. Touching anything here breaks exactly the
    setup it is meant to serve."""

    def test_the_overlay_is_empty(self) -> None:
        assert build_sdk_env(None) == {}

    def test_an_empty_string_counts_as_no_token(self) -> None:
        assert build_sdk_env("") == {}

    def test_the_host_login_is_not_isolated_away(self) -> None:
        """Pinning CLAUDE_CONFIG_DIR would point the CLI at an empty
        directory and lose the very login being borrowed."""
        assert CONFIG_DIR_ENV not in build_sdk_env(None)

    def test_host_credentials_are_left_intact(self) -> None:
        env = build_sdk_env(None, inherited={"ANTHROPIC_API_KEY": "the-host-key"})
        assert "ANTHROPIC_API_KEY" not in env
