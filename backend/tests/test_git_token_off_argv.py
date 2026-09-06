"""SEC-29: the HTTPS PAT must not reach the command line.

``app.gitops.git_service._clone_https`` embedded the token in the clone
URL (``https://oauth2:TOKEN@host/...``). Two consequences:

* The token was on ``git``'s argv, and ``/proc/<pid>/cmdline`` is
  world-readable — any local account could read the PAT off a running
  clone by listing processes.
* It was written into ``.git/config`` until the ``set_url`` two lines
  later scrubbed it. A window, not an absence.

The pack path had already moved the token off the URL with
``git -c http.extraHeader=…``, but ``-c`` is argv too, so it solved the
config half and not the process half. Both paths now carry it in
``GIT_CONFIG_*`` environment variables: ``/proc/<pid>/environ`` is
readable only by the process owner and root.

These tests assert on what would be handed to the subprocess, since
that is where the exposure lived.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.packs.git_auth import git_auth_context, token_config_env

TOKEN = "ghp_exampletokenvalue0123456789"


class TestTheTokenIsNotOnArgv:
    def test_the_pack_path_puts_nothing_secret_in_extra_args(self):
        with git_auth_context(token=TOKEN) as auth:
            assert TOKEN not in " ".join(auth.extra_args)
            assert auth.extra_args == []

    def test_the_pack_path_carries_it_in_the_environment(self):
        with git_auth_context(token=TOKEN) as auth:
            assert auth.extra_env["GIT_CONFIG_VALUE_0"] == f"Authorization: Bearer {TOKEN}"
            assert auth.extra_env["GIT_CONFIG_KEY_0"] == "http.extraHeader"
            assert auth.extra_env["GIT_CONFIG_COUNT"] == "2"

    def test_the_gitops_path_does_not_put_it_in_the_clone_url(self):
        """The regression this exists to prevent: `git clone
        https://oauth2:TOKEN@host/...` is a world-readable argv."""
        from app.gitops.git_service import _clone_https
        from app.models.git_repository import GitAuthType, GitRepository

        repo = GitRepository(
            name="r",
            url="https://example.com/team/r.git",
            branch="main",
            auth_type=GitAuthType.https_token,
            encrypted_https_token=b"ciphertext",
        )
        seen: dict = {}

        def _fake_clone(url, path, **kw):
            seen["url"] = url
            seen["env"] = kw.get("env", {})
            return object()

        with (
            patch("app.gitops.git_service.decrypt_ssh_key", return_value=TOKEN),
            patch("app.gitops.git_service.get_master_key", return_value=b"\x00" * 32),
            patch("git.Repo.clone_from", side_effect=_fake_clone),
        ):
            _clone_https(repo, "/tmp/does-not-matter")

        assert seen["url"] == "https://example.com/team/r.git"
        assert TOKEN not in seen["url"]
        assert seen["env"]["GIT_CONFIG_VALUE_0"] == f"Authorization: Bearer {TOKEN}"

    def test_the_gitops_path_keeps_the_ambient_environment(self):
        """Replacing os.environ outright would drop PATH, HOME and the
        proxy settings a real clone may need."""
        from app.gitops.git_service import _clone_https
        from app.models.git_repository import GitAuthType, GitRepository

        repo = GitRepository(
            name="r",
            url="https://example.com/r.git",
            branch="main",
            auth_type=GitAuthType.https_token,
            encrypted_https_token=b"ciphertext",
        )
        seen: dict = {}

        with (
            patch("app.gitops.git_service.decrypt_ssh_key", return_value=TOKEN),
            patch("app.gitops.git_service.get_master_key", return_value=b"\x00" * 32),
            patch("os.environ", {"PATH": "/usr/bin", "HTTPS_PROXY": "http://proxy:3128"}),
            patch(
                "git.Repo.clone_from",
                side_effect=lambda url, path, **kw: seen.update(env=kw.get("env", {})),
            ),
        ):
            _clone_https(repo, "/tmp/does-not-matter")

        assert seen["env"]["PATH"] == "/usr/bin"
        assert seen["env"]["HTTPS_PROXY"] == "http://proxy:3128"


class TestTheHeaderIsNotResentOnARedirect:
    def test_follow_redirects_is_disabled(self):
        """Without this a remote can redirect the clone and be handed
        the PAT in the Authorization header."""
        env = token_config_env(TOKEN)
        idx = next(i for i in (0, 1) if env[f"GIT_CONFIG_KEY_{i}"] == "http.followRedirects")
        assert env[f"GIT_CONFIG_VALUE_{idx}"] == "false"

    def test_the_count_matches_the_number_of_pairs(self):
        """git reads exactly GIT_CONFIG_COUNT pairs — a wrong count
        silently drops a setting or makes git error out."""
        env = token_config_env(TOKEN)
        count = int(env["GIT_CONFIG_COUNT"])
        for i in range(count):
            assert f"GIT_CONFIG_KEY_{i}" in env
            assert f"GIT_CONFIG_VALUE_{i}" in env
        assert f"GIT_CONFIG_KEY_{count}" not in env


class TestTheOtherAuthModesAreUnaffected:
    def test_ssh_auth_gets_no_token_config(self):
        key = "-----BEGIN OPENSSH PRIVATE KEY-----\nx\n-----END OPENSSH PRIVATE KEY-----"
        with git_auth_context(ssh_private_key=key) as auth:
            assert "GIT_CONFIG_COUNT" not in auth.extra_env

    def test_public_access_gets_nothing_at_all(self):
        with git_auth_context() as auth:
            assert auth.extra_env == {}
            assert auth.extra_args == []

    def test_the_token_is_still_declared_for_redaction(self):
        with git_auth_context(token=TOKEN) as auth:
            assert auth.redact_values == [TOKEN]

    def test_supplying_both_credentials_is_still_rejected(self):
        with pytest.raises(ValueError, match="at most one"):
            with git_auth_context(ssh_private_key="k", token=TOKEN):
                pass
