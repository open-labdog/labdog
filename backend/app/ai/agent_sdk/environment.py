"""The environment handed to the Claude Code subprocess.

:func:`app.ai.providers.claude_cli.build_cli_env` builds a *complete*
environment and removes the credential variables that would outrank the
configured token. That works when the caller owns the whole environment,
as ``subprocess_exec`` does.

The Agent SDK does not work that way. ``ClaudeAgentOptions.env`` is an
**overlay**: the SDK composes the child environment as
``{**os.environ, **options.env}``. A variable removed from the overlay is
therefore not removed at all — the inherited value survives, and it is
precisely the value that outranks the token LabDog configured.

Deleting is not available, so the variables are set to empty instead.
Verified against the real binary: with a bogus ``ANTHROPIC_API_KEY``
inherited from the parent and no override, the session never
authenticated; with the same key inherited and an empty-string override,
it authenticated normally and answered.

This is the same precedence trap the single-shot backend closed, wearing
a different hat, so it is kept somewhere it can be tested directly rather
than inline in the runner.
"""

from __future__ import annotations

import os

from app.ai.providers.claude_cli import (
    CONFIG_DIR_ENV,
    DEFAULT_CONFIG_DIR,
    OAUTH_TOKEN_ENV,
    OVERRIDING_CREDENTIAL_ENV,
)

#: Value used to neutralise an inherited credential variable. Empty rather
#: than a placeholder: the CLI tests these for presence, and any non-empty
#: string would be tried as a credential and fail authentication.
_NEUTRALISED = ""


def build_sdk_env(
    oauth_token: str | None,
    config_dir: str = DEFAULT_CONFIG_DIR,
    inherited: dict[str, str] | None = None,
) -> dict[str, str]:
    """The ``ClaudeAgentOptions.env`` overlay for one session.

    With a token: inject it, blank out the variables that would otherwise
    take precedence, and pin the config directory so a stored login cannot
    shadow it from disk.

    Without one: return an empty overlay. That case is an operator who
    authenticated Claude Code on the host and wants LabDog to use that
    login, so neither the credential variables nor the config directory may
    be disturbed — pinning the config directory here would break exactly
    the setup it is meant to serve.

    ``inherited`` is only read to decide which variables actually need
    neutralising; it defaults to the real environment.
    """
    if not oauth_token:
        return {}

    source = os.environ if inherited is None else inherited
    env: dict[str, str] = {
        OAUTH_TOKEN_ENV: oauth_token,
        CONFIG_DIR_ENV: config_dir,
    }
    for name in OVERRIDING_CREDENTIAL_ENV:
        # Only blank what is actually set. An overlay full of empty strings
        # would work, but it makes the child environment harder to read in
        # a debug dump, and hides which of them the host really had.
        if source.get(name):
            env[name] = _NEUTRALISED
    return env
