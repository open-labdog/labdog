"""Build git invocation context (env + extra args) for authenticated pack sync.

A ``GitAuthContext`` bundles everything ``git_sync._run_git`` needs to know
about authentication for one invocation:

- Extra ``git`` CLI args (before the subcommand) — used to inject
  ``http.extraHeader`` for HTTPS PAT auth without the token ever landing
  in ``remote.origin.url``.
- Extra env vars — mainly ``GIT_SSH_COMMAND`` for SSH auth.
- A list of secret strings to redact from any captured stderr before
  persisting to the DB.

SSH auth writes the private key to a temp directory with ``0600`` perms.

Host-key verification is real TOFU (SEC-27). It used to be
``StrictHostKeyChecking=accept-new`` *with*
``UserKnownHostsFile=/dev/null``, which reads as TOFU but is
unconditional acceptance: every invocation started from an empty
known-hosts file, so there was never a first use and therefore never a
mismatch to detect. Anyone able to intercept the connection to the pack
repository could serve arbitrary playbooks, which LabDog then runs
against the fleet as root.

Now the known-hosts file is a real file in the same temp directory:

* With a stored ``host_key_entry`` it is pre-populated and
  ``StrictHostKeyChecking=yes`` refuses anything that does not match.
* Without one, ``accept-new`` records what the server presented, and
  :meth:`GitAuthContext.learned_host_key` hands it back so the caller
  can persist it on the repository row and pin every later sync.

The context must be used within a ``with`` block so the files are
always cleaned up, even when the git invocation raises — and
``learned_host_key()`` must be called inside it, since the file is gone
on exit.
"""

from __future__ import annotations

import logging
import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class GitAuthContext:
    """Bundle of env/args/secrets for one git invocation."""

    extra_args: list[str] = field(default_factory=list)
    extra_env: dict[str, str] = field(default_factory=dict)
    redact_values: list[str] = field(default_factory=list)
    #: Known-hosts file backing this invocation, when it is an SSH one.
    known_hosts_path: str | None = None
    #: True when that file was pre-populated from a stored key, i.e. the
    #: connection was verified rather than trusted on first use.
    pinned: bool = False

    def learned_host_key(self) -> str | None:
        """The host key ``accept-new`` just recorded, or ``None``.

        Returns ``None`` when the connection was already pinned (there
        was nothing to learn), when this is not an SSH invocation, or
        when nothing was written — a clone that never reached the
        server leaves the file empty.

        Must be called inside the ``with`` block: the file lives in the
        temp directory the context manager removes on exit.
        """
        if self.pinned or not self.known_hosts_path:
            return None
        try:
            content = Path(self.known_hosts_path).read_text().strip()
        except OSError:
            return None
        return content or None


@contextmanager
def git_auth_context(
    *,
    ssh_private_key: str | None = None,
    token: str | None = None,
    host_key_entry: str | None = None,
) -> Iterator[GitAuthContext]:
    """Yield a ``GitAuthContext`` configured for the supplied credential.

    Exactly one of ``ssh_private_key`` or ``token`` should be non-empty
    — or both None for unauthenticated public-repo access. For SSH the
    private key is materialised in a 0700 tmpdir with 0600 perms and
    deleted when the ``with`` block exits, even on error.

    ``host_key_entry`` is the ``known_hosts`` line recorded for this
    repository on a previous sync. When present the connection is
    verified against it; when absent the key presented on this
    connection is trusted and readable afterwards through
    :meth:`GitAuthContext.learned_host_key`.
    """
    if ssh_private_key and token:
        raise ValueError("git_auth_context accepts at most one of ssh_private_key / token")

    if token:
        # Token is passed inline as a git -c override so git treats it
        # as an HTTP header on the request. remote.origin.url stays
        # clean.
        # followRedirects is disabled so that git never re-sends the
        # Authorization header to a redirect target — prevents PAT
        # exfiltration if the remote redirects to an attacker host.
        yield GitAuthContext(
            extra_args=[
                "-c",
                f"http.extraHeader=Authorization: Bearer {token}",
                "-c",
                "http.followRedirects=false",
            ],
            redact_values=[token],
        )
        return

    if ssh_private_key:
        with _materialised_ssh_key(ssh_private_key) as key_path:
            known_hosts_path = key_path.parent / "known_hosts"
            pinned = bool(host_key_entry and host_key_entry.strip())
            known_hosts_path.write_text(
                f"{host_key_entry.strip()}\n" if pinned else ""  # type: ignore[union-attr]
            )
            os.chmod(known_hosts_path, stat.S_IRUSR | stat.S_IWUSR)
            yield GitAuthContext(
                extra_env={
                    "GIT_SSH_COMMAND": build_ssh_command(
                        str(key_path), str(known_hosts_path), pinned=pinned
                    )
                },
                # SSH keys shouldn't appear in git stderr, but defend
                # anyway against odd edge cases.
                redact_values=[ssh_private_key],
                known_hosts_path=str(known_hosts_path),
                pinned=pinned,
            )
        return

    yield GitAuthContext()


@contextmanager
def _materialised_ssh_key(private_key: str) -> Iterator[Path]:
    """Write the key to a 0700 temp dir with 0600 perms, yield path, clean up."""
    tmpdir = tempfile.mkdtemp(prefix="labdog-pack-ssh-")
    try:
        os.chmod(tmpdir, 0o700)
        key_path = Path(tmpdir) / "id"
        key_path.write_text(private_key if private_key.endswith("\n") else private_key + "\n")
        os.chmod(key_path, stat.S_IRUSR | stat.S_IWUSR)
        yield key_path
    finally:
        try:
            for name in ("id", "known_hosts"):
                target = Path(tmpdir) / name
                if target.exists():
                    target.unlink()
            os.rmdir(tmpdir)
        except OSError:
            logger.warning("failed to clean SSH tmpdir %s", tmpdir, exc_info=True)


def build_ssh_command(key_path: str, known_hosts_path: str, *, pinned: bool) -> str:
    """The ``GIT_SSH_COMMAND`` for one authenticated git invocation.

    Shared with ``app.gitops.git_service`` so the two git paths cannot
    drift apart on host-key policy — which is exactly how SEC-27 came
    about.

    ``GlobalKnownHostsFile=/dev/null`` keeps the controller's
    system-wide known-hosts out of the decision: LabDog verifies against
    the key it recorded for this repository, not against whatever else
    the host happens to trust.
    """
    strictness = "yes" if pinned else "accept-new"
    return (
        f"ssh -i {_shell_quote(key_path)} "
        f"-o StrictHostKeyChecking={strictness} "
        f"-o UserKnownHostsFile={_shell_quote(known_hosts_path)} "
        "-o GlobalKnownHostsFile=/dev/null "
        "-o IdentitiesOnly=yes "
        "-o PasswordAuthentication=no"
    )


def _shell_quote(s: str) -> str:
    """Quote a path for inclusion in GIT_SSH_COMMAND. Paths we control
    are always under a safe tmpdir so this is belt-and-braces."""
    if any(c in s for c in " '\"\\$`\n"):
        return "'" + s.replace("'", "'\\''") + "'"
    return s
