"""Build git invocation context (env + extra args) for authenticated pack sync.

A ``GitAuthContext`` bundles everything ``git_sync._run_git`` needs to know
about authentication for one invocation:

- Extra ``git`` CLI args (before the subcommand) — used to inject
  ``http.extraHeader`` for HTTPS PAT auth without the token ever landing
  in ``remote.origin.url``.
- Extra env vars — mainly ``GIT_SSH_COMMAND`` for SSH auth.
- A list of secret strings to redact from any captured stderr before
  persisting to the DB.

SSH auth writes the private key and known_hosts file to a
caller-provided temp directory with ``0600`` perms. The context must be
used within a ``with`` block so the files are always cleaned up, even
when the git invocation raises.
"""

from __future__ import annotations

import logging
import os
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from app.packs.known_hosts import build_known_hosts
from app.packs.models import PackAuthType

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
    auth_type: PackAuthType,
    ssh_private_key: str | None = None,
    ssh_known_hosts: str | None = None,
    token: str | None = None,
) -> Iterator[GitAuthContext]:
    """Yield a ``GitAuthContext`` configured for *auth_type*.

    For SSH, the private key and known_hosts are materialised in a
    restricted-permission temp directory; that directory is deleted when
    the ``with`` block exits.
    """
    if auth_type == PackAuthType.NONE:
        yield GitAuthContext()
        return

    if auth_type == PackAuthType.HTTPS_TOKEN:
        if not token:
            raise ValueError("HTTPS_TOKEN auth requires a token")
        # The token is never persisted to disk. It's passed inline as a
        # git -c override so git treats it as an HTTP header on the
        # request, but remote.origin.url stays clean.
        ctx = GitAuthContext(
            extra_args=[
                "-c",
                f"http.extraHeader=Authorization: Bearer {token}",
            ],
            redact_values=[token],
        )
        yield ctx
        return

    if auth_type == PackAuthType.SSH:
        if not ssh_private_key:
            raise ValueError("SSH auth requires a private key")
        if not ssh_known_hosts or not ssh_known_hosts.strip():
            raise ValueError(
                "SSH auth requires ssh_known_hosts — LabDog does not fall back "
                "to TOFU. Paste the remote's host keys when configuring the pack."
            )
        known_hosts_body = build_known_hosts(ssh_known_hosts)
        if not known_hosts_body:
            raise ValueError("ssh_known_hosts contained no valid entries")

        with _materialised_ssh_files(ssh_private_key, known_hosts_body) as (
            key_path,
            hosts_path,
        ):
            cmd = (
                f"ssh -i {_shell_quote(str(key_path))} "
                f"-o UserKnownHostsFile={_shell_quote(str(hosts_path))} "
                "-o StrictHostKeyChecking=yes "
                "-o IdentitiesOnly=yes "
                "-o PasswordAuthentication=no"
            )
            yield GitAuthContext(
                extra_env={"GIT_SSH_COMMAND": cmd},
                # SSH keys shouldn't appear in git stderr, but defend
                # anyway against odd edge cases (e.g. libcurl showing a
                # line in verbose output).
                redact_values=[ssh_private_key],
            )
        return

    raise ValueError(f"unknown auth_type: {auth_type!r}")


@contextmanager
def _materialised_ssh_files(
    private_key: str, known_hosts_body: str
) -> Iterator[tuple[Path, Path]]:
    """Write the key + known_hosts to a 0700 temp dir, return paths, clean up."""
    tmpdir = tempfile.mkdtemp(prefix="labdog-pack-ssh-")
    try:
        os.chmod(tmpdir, 0o700)
        key_path = Path(tmpdir) / "id"
        hosts_path = Path(tmpdir) / "known_hosts"
        key_path.write_text(
            private_key if private_key.endswith("\n") else private_key + "\n"
        )
        hosts_path.write_text(known_hosts_body)
        os.chmod(key_path, stat.S_IRUSR | stat.S_IWUSR)
        os.chmod(hosts_path, stat.S_IRUSR | stat.S_IWUSR)
        yield key_path, hosts_path
    finally:
        try:
            for p in (Path(tmpdir) / "id", Path(tmpdir) / "known_hosts"):
                if p.exists():
                    p.unlink()
            os.rmdir(tmpdir)
        except OSError:
            logger.warning("failed to clean SSH tmpdir %s", tmpdir, exc_info=True)


def _shell_quote(s: str) -> str:
    """Quote a path for inclusion in GIT_SSH_COMMAND. Paths we control
    are always under a safe tmpdir so this is belt-and-braces."""
    if any(c in s for c in " '\"\\$`\n"):
        return "'" + s.replace("'", "'\\''") + "'"
    return s
