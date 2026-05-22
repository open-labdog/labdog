"""Tests for the GitAuthContext builder.

We only verify the shape of the context — env vars, CLI args, and that
SSH materialises its tmpdir key with correct perms and cleans it up
after the with-block exits. Real git operations live in
``test_packs_git_integration.py``.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from app.packs.git_auth import git_auth_context


def test_no_credentials_yields_empty_context():
    with git_auth_context() as ctx:
        assert ctx.extra_args == []
        assert ctx.extra_env == {}
        assert ctx.redact_values == []


def test_token_sets_extraheader_and_redacts():
    with git_auth_context(token="ghp_secret_pat_value") as ctx:
        assert ctx.extra_args[0] == "-c"
        assert "Authorization: Bearer ghp_secret_pat_value" in ctx.extra_args[1]
        assert "ghp_secret_pat_value" in ctx.redact_values


def test_token_disables_follow_redirects():
    """SEC-11: PAT path must also set http.followRedirects=false."""
    with git_auth_context(token="ghp_secret") as ctx:
        args_str = " ".join(ctx.extra_args)
        assert "http.followRedirects=false" in args_str


def test_ssh_key_does_not_set_follow_redirects():
    """SSH-key path must NOT add http.followRedirects (defence-in-depth: don't break anything)."""
    key_bytes = "-----BEGIN OPENSSH PRIVATE KEY-----\nABCDEF\n-----END OPENSSH PRIVATE KEY-----"
    with git_auth_context(ssh_private_key=key_bytes) as ctx:
        args_str = " ".join(ctx.extra_args)
        assert "followRedirects" not in args_str


def test_no_credentials_does_not_set_follow_redirects():
    """Unauthenticated path must also not add http.followRedirects."""
    with git_auth_context() as ctx:
        args_str = " ".join(ctx.extra_args)
        assert "followRedirects" not in args_str


def test_token_and_key_together_rejected():
    with pytest.raises(ValueError, match="at most one"):
        with git_auth_context(ssh_private_key="k", token="t"):
            pass


def test_ssh_materialises_key_and_cleans_up():
    key_bytes = "-----BEGIN OPENSSH PRIVATE KEY-----\nABCDEF\n-----END OPENSSH PRIVATE KEY-----"

    seen: dict[str, Path] = {}
    with git_auth_context(ssh_private_key=key_bytes) as ctx:
        cmd = ctx.extra_env["GIT_SSH_COMMAND"]
        parts = cmd.split()
        i_idx = parts.index("-i")
        key_path = Path(parts[i_idx + 1].strip("'"))
        seen["key"] = key_path

        assert key_path.is_file()
        mode = stat.S_IMODE(os.stat(key_path).st_mode)
        assert mode == 0o600
        assert key_path.read_text().startswith("-----BEGIN")
        # TOFU for host keys — same posture as the gitops subsystem.
        assert "StrictHostKeyChecking=accept-new" in cmd
        assert "UserKnownHostsFile=/dev/null" in cmd
        assert "IdentitiesOnly=yes" in cmd
        assert key_bytes in ctx.redact_values

    assert not seen["key"].exists()
    assert not seen["key"].parent.exists()


def test_ssh_cleans_up_on_exception():
    key_bytes = "-----BEGIN OPENSSH PRIVATE KEY-----\nX\n-----END OPENSSH PRIVATE KEY-----"
    captured: dict[str, Path] = {}
    try:
        with git_auth_context(ssh_private_key=key_bytes) as ctx:
            cmd = ctx.extra_env["GIT_SSH_COMMAND"]
            key_path = Path(cmd.split()[2].strip("'"))
            captured["key"] = key_path
            assert key_path.is_file()
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert not captured["key"].exists()
    assert not captured["key"].parent.exists()
