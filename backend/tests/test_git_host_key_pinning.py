"""SEC-27: git-over-SSH must actually verify the server it clones from.

Both git paths set ``StrictHostKeyChecking=accept-new`` *with*
``UserKnownHostsFile=/dev/null``. That reads like TOFU and is nothing of
the sort: every invocation starts from an empty known-hosts file, so
there is never a first use and therefore never a mismatch to detect.
Anyone able to intercept the connection to the pack repository serves
arbitrary playbooks, which LabDog then runs against the fleet as root.

The fix is a real known-hosts file with a real lifetime: pre-populated
from ``GitRepository.ssh_host_key_entry`` and verified strictly when one
is recorded, learned and handed back on first contact when one is not.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path

import pytest

from app.packs.git_auth import build_ssh_command, git_auth_context

ENTRY = "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleKeyMaterialNotReal"
KEY = "-----BEGIN OPENSSH PRIVATE KEY-----\nnotarealkey\n-----END OPENSSH PRIVATE KEY-----"


def _opts(cmd: str) -> list[str]:
    return shlex.split(cmd)


class TestTheKnownHostsFileIsNeverDevNull:
    """The regression this whole change exists to prevent."""

    def test_a_pinned_repo_does_not_get_dev_null(self):
        with git_auth_context(ssh_private_key=KEY, host_key_entry=ENTRY) as auth:
            cmd = auth.extra_env["GIT_SSH_COMMAND"]
        assert "UserKnownHostsFile=/dev/null" not in cmd

    def test_an_unpinned_repo_does_not_get_dev_null_either(self):
        """This is the case that used to be unconditional acceptance:
        accept-new against /dev/null can never fail."""
        with git_auth_context(ssh_private_key=KEY) as auth:
            cmd = auth.extra_env["GIT_SSH_COMMAND"]
        assert "UserKnownHostsFile=/dev/null" not in cmd


class TestAPinnedRepoIsVerifiedStrictly:
    def test_the_stored_key_is_the_only_one_accepted(self):
        with git_auth_context(ssh_private_key=KEY, host_key_entry=ENTRY) as auth:
            cmd = auth.extra_env["GIT_SSH_COMMAND"]
            assert "-o StrictHostKeyChecking=yes" in cmd
            assert Path(auth.known_hosts_path).read_text() == ENTRY + "\n"
            assert auth.pinned is True

    def test_the_controllers_known_hosts_cannot_vouch_for_the_server(self):
        with git_auth_context(ssh_private_key=KEY, host_key_entry=ENTRY) as auth:
            assert "GlobalKnownHostsFile=/dev/null" in auth.extra_env["GIT_SSH_COMMAND"]

    def test_there_is_nothing_to_learn_from_a_verified_connection(self):
        with git_auth_context(ssh_private_key=KEY, host_key_entry=ENTRY) as auth:
            # Simulate ssh rewriting the file — a pinned run must not
            # adopt whatever ends up there.
            Path(auth.known_hosts_path).write_text("evil.example ssh-rsa AAAAB3Nz\n")
            assert auth.learned_host_key() is None


class TestFirstContactLearnsTheKey:
    def test_an_empty_file_and_accept_new(self):
        with git_auth_context(ssh_private_key=KEY) as auth:
            assert "-o StrictHostKeyChecking=accept-new" in auth.extra_env["GIT_SSH_COMMAND"]
            assert Path(auth.known_hosts_path).read_text() == ""
            assert auth.pinned is False

    def test_what_ssh_records_is_handed_back_for_persisting(self):
        with git_auth_context(ssh_private_key=KEY) as auth:
            # ssh appends the accepted key to the file during the clone.
            Path(auth.known_hosts_path).write_text(ENTRY + "\n")
            assert auth.learned_host_key() == ENTRY

    def test_a_clone_that_never_reached_the_server_learns_nothing(self):
        """An empty file must not be persisted as "pinned to nothing",
        which would leave the repo permanently unverifiable."""
        with git_auth_context(ssh_private_key=KEY) as auth:
            assert auth.learned_host_key() is None


class TestTheFilesDoNotOutliveTheBlock:
    def test_both_the_key_and_the_known_hosts_are_removed(self):
        with git_auth_context(ssh_private_key=KEY) as auth:
            known_hosts = auth.known_hosts_path
            tmpdir = os.path.dirname(known_hosts)
            Path(known_hosts).write_text(ENTRY + "\n")
        assert not os.path.exists(known_hosts)
        assert not os.path.exists(tmpdir)

    def test_they_are_removed_even_when_the_clone_raises(self):
        with pytest.raises(RuntimeError):
            with git_auth_context(ssh_private_key=KEY) as auth:
                known_hosts = auth.known_hosts_path
                raise RuntimeError("clone failed")
        assert not os.path.exists(known_hosts)

    def test_the_known_hosts_file_is_not_world_readable(self):
        with git_auth_context(ssh_private_key=KEY, host_key_entry=ENTRY) as auth:
            assert oct(os.stat(auth.known_hosts_path).st_mode)[-3:] == "600"


class TestTheTokenPathIsUnchanged:
    """HTTPS auth has no host key; it must not grow one by accident."""

    def test_a_token_context_has_no_known_hosts(self):
        with git_auth_context(token="ghp_example") as auth:
            assert auth.known_hosts_path is None
            assert auth.learned_host_key() is None
            assert "GIT_SSH_COMMAND" not in auth.extra_env

    def test_the_token_is_still_kept_off_the_url(self):
        with git_auth_context(token="ghp_example") as auth:
            assert "http.extraHeader=Authorization: Bearer ghp_example" in auth.extra_args
            assert auth.redact_values == ["ghp_example"]


class TestTheSharedCommandBuilder:
    """``app.gitops.git_service`` builds its own env but must not drift
    on host-key policy — sharing the builder is what stops SEC-27 from
    reappearing in one path only."""

    def test_pinned_and_unpinned_differ_only_in_strictness(self):
        pinned = _opts(build_ssh_command("/k", "/kh", pinned=True))
        unpinned = _opts(build_ssh_command("/k", "/kh", pinned=False))
        assert "StrictHostKeyChecking=yes" in " ".join(pinned)
        assert "StrictHostKeyChecking=accept-new" in " ".join(unpinned)
        assert [o for o in pinned if "StrictHostKeyChecking" not in o] == [
            o for o in unpinned if "StrictHostKeyChecking" not in o
        ]

    def test_paths_with_spaces_survive_the_shell(self):
        opts = _opts(build_ssh_command("/tmp/a b/id", "/tmp/a b/known_hosts", pinned=True))
        assert "/tmp/a b/id" in opts
        assert "UserKnownHostsFile=/tmp/a b/known_hosts" in opts

    def test_password_auth_stays_off(self):
        cmd = build_ssh_command("/k", "/kh", pinned=True)
        assert "-o PasswordAuthentication=no" in cmd
        assert "-o IdentitiesOnly=yes" in cmd
