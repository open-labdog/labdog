"""Build git invocation context (env + extra args) for authenticated pack sync.

A ``GitAuthContext`` bundles everything ``git_sync._run_git`` needs to know
about authentication for one invocation:

- Extra ``git`` CLI args (before the subcommand) — used to inject
  ``http.extraHeader`` for HTTPS PAT auth without the token ever landing
  in ``remote.origin.url``.
- Extra env vars — mainly ``GIT_SSH_COMMAND`` for SSH auth.
- A list of secret strings to redact from any captured stderr before
  persisting to the DB.

SSH auth writes the private key to a temp directory with ``0600`` perms
and uses TOFU (``StrictHostKeyChecking=accept-new``) for host-key
verification — same posture as the rest of LabDog's git integration.
The context must be used within a ``with`` block so the files are
always cleaned up, even when the git invocation raises.
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


@contextmanager
def git_auth_context(
    *,
    ssh_private_key: str | None = None,
    token: str | None = None,
) -> Iterator[GitAuthContext]:
    """Yield a ``GitAuthContext`` configured for the supplied credential.

    Exactly one of ``ssh_private_key`` or ``token`` should be non-empty
    — or both None for unauthenticated public-repo access. For SSH the
    private key is materialised in a 0700 tmpdir with 0600 perms and
    deleted when the ``with`` block exits, even on error.
    """
    if ssh_private_key and token:
        raise ValueError(
            "git_auth_context accepts at most one of ssh_private_key / token"
        )

    if token:
        # Token is passed inline as a git -c override so git treats it
        # as an HTTP header on the request. remote.origin.url stays
        # clean.
        yield GitAuthContext(
            extra_args=[
                "-c",
                f"http.extraHeader=Authorization: Bearer {token}",
            ],
            redact_values=[token],
        )
        return

    if ssh_private_key:
        with _materialised_ssh_key(ssh_private_key) as key_path:
            cmd = (
                f"ssh -i {_shell_quote(str(key_path))} "
                "-o StrictHostKeyChecking=accept-new "
                "-o UserKnownHostsFile=/dev/null "
                "-o IdentitiesOnly=yes "
                "-o PasswordAuthentication=no"
            )
            yield GitAuthContext(
                extra_env={"GIT_SSH_COMMAND": cmd},
                # SSH keys shouldn't appear in git stderr, but defend
                # anyway against odd edge cases.
                redact_values=[ssh_private_key],
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
        key_path.write_text(
            private_key if private_key.endswith("\n") else private_key + "\n"
        )
        os.chmod(key_path, stat.S_IRUSR | stat.S_IWUSR)
        yield key_path
    finally:
        try:
            if (Path(tmpdir) / "id").exists():
                (Path(tmpdir) / "id").unlink()
            os.rmdir(tmpdir)
        except OSError:
            logger.warning("failed to clean SSH tmpdir %s", tmpdir, exc_info=True)


def _shell_quote(s: str) -> str:
    """Quote a path for inclusion in GIT_SSH_COMMAND. Paths we control
    are always under a safe tmpdir so this is belt-and-braces."""
    if any(c in s for c in " '\"\\$`\n"):
        return "'" + s.replace("'", "'\\''") + "'"
    return s
