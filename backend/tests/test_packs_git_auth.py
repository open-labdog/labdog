"""Tests for the GitAuthContext builder.

We only verify the shape of the context — env vars, CLI args, and that
SSH materialises its tmpdir files with correct perms and cleans them up
after the with-block exits. End-to-end git integration lives in
``test_packs_git_integration.py``.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from app.packs.git_auth import git_auth_context
from app.packs.models import PackAuthType


def test_none_auth_yields_empty_context():
    with git_auth_context(auth_type=PackAuthType.NONE) as ctx:
        assert ctx.extra_args == []
        assert ctx.extra_env == {}
        assert ctx.redact_values == []


def test_https_token_sets_extraheader_and_redacts_token():
    with git_auth_context(
        auth_type=PackAuthType.HTTPS_TOKEN, token="ghp_secret_pat_value"
    ) as ctx:
        assert ctx.extra_args[0] == "-c"
        assert "Authorization: Bearer ghp_secret_pat_value" in ctx.extra_args[1]
        assert "ghp_secret_pat_value" in ctx.redact_values


def test_https_token_missing_token_raises():
    with pytest.raises(ValueError, match="requires a token"):
        with git_auth_context(auth_type=PackAuthType.HTTPS_TOKEN):
            pass


def test_ssh_missing_key_raises():
    with pytest.raises(ValueError, match="requires a private key"):
        with git_auth_context(auth_type=PackAuthType.SSH):
            pass


def test_ssh_missing_known_hosts_raises():
    with pytest.raises(ValueError, match="ssh_known_hosts"):
        with git_auth_context(
            auth_type=PackAuthType.SSH,
            ssh_private_key="-----BEGIN OPENSSH PRIVATE KEY-----\nkey\n-----END OPENSSH PRIVATE KEY-----",
        ):
            pass


def test_ssh_materialises_files_and_cleans_up():
    key_bytes = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\nABCDEF\n-----END OPENSSH PRIVATE KEY-----"
    )
    hosts_entry = "github.com ssh-rsa AAAAB3NzaC1yc2EABC"

    seen_paths: dict[str, Path] = {}
    with git_auth_context(
        auth_type=PackAuthType.SSH,
        ssh_private_key=key_bytes,
        ssh_known_hosts=hosts_entry,
    ) as ctx:
        cmd = ctx.extra_env["GIT_SSH_COMMAND"]
        # GIT_SSH_COMMAND references concrete paths under a tmpdir.
        parts = cmd.split()
        # -i <keypath> is present
        i_idx = parts.index("-i")
        key_path = Path(parts[i_idx + 1].strip("'"))
        # UserKnownHostsFile=<path>
        uhf = next(p for p in parts if p.startswith("-o") is False and "UserKnownHostsFile" in p)
        hosts_path = Path(uhf.split("=", 1)[1].strip("'"))

        seen_paths["key"] = key_path
        seen_paths["hosts"] = hosts_path

        assert key_path.is_file()
        assert hosts_path.is_file()
        # 0600 perms on the key file.
        mode = stat.S_IMODE(os.stat(key_path).st_mode)
        assert mode == 0o600
        assert key_path.read_text().startswith("-----BEGIN")
        assert hosts_entry in hosts_path.read_text()
        assert "StrictHostKeyChecking=yes" in cmd
        assert "IdentitiesOnly=yes" in cmd
        # Secrets list carries the key so stderr redaction will catch it.
        assert key_bytes in ctx.redact_values

    # After the with-block, files and their tmpdir are gone.
    assert not seen_paths["key"].exists()
    assert not seen_paths["hosts"].exists()
    assert not seen_paths["key"].parent.exists()


def test_ssh_cleans_up_on_exception():
    key_bytes = "-----BEGIN OPENSSH PRIVATE KEY-----\nX\n-----END OPENSSH PRIVATE KEY-----"
    captured: dict[str, Path] = {}
    try:
        with git_auth_context(
            auth_type=PackAuthType.SSH,
            ssh_private_key=key_bytes,
            ssh_known_hosts="h ssh-rsa K",
        ) as ctx:
            cmd = ctx.extra_env["GIT_SSH_COMMAND"]
            key_path = Path(cmd.split()[2].strip("'"))
            captured["key"] = key_path
            assert key_path.is_file()
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert not captured["key"].exists()
    assert not captured["key"].parent.exists()
