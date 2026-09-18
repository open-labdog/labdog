"""Git operations service for GitOps integration.

Handles clone/pull with SSH key or HTTPS token authentication.
All credential handling uses temp files (/dev/shm/) with cleanup in finally blocks.
"""

import os
import shutil
import tempfile
from pathlib import Path

import git  # gitpython

from app.crypto.encryption import decrypt_ssh_key
from app.crypto.key_management import get_master_key
from app.models.git_repository import GitAuthType, GitRepository
from app.packs.git_auth import build_ssh_command, token_config_env


def clone_repo(
    repo: GitRepository,
    target_dir: Path | None = None,
    *,
    encrypted_ssh_key: bytes | None = None,
) -> tuple[git.Repo, Path]:
    """Clone a Git repository to a temp directory.

    For SSH auth: decrypts key -> writes to /dev/shm/ -> sets GIT_SSH_COMMAND
    -> clones -> cleans key
    For HTTPS auth: decrypts token -> constructs URL -> clones -> never persists token

    Args:
        repo: GitRepository model instance with url, branch, auth_type.
        target_dir: Optional clone destination. Created as tmpdir if None.
        encrypted_ssh_key: AES-256-GCM encrypted SSH private key bytes.
            Required when auth_type is ssh_key. Caller queries SSHKey model
            by repo.ssh_key_id to obtain this.

    Returns:
        Tuple of (git.Repo object, path to cloned directory).
    """
    if target_dir is None:
        target_dir = Path(tempfile.mkdtemp(prefix="labdog-git-"))

    if repo.auth_type == GitAuthType.ssh_key:
        return _clone_ssh(repo, target_dir, encrypted_ssh_key)
    elif repo.auth_type == GitAuthType.https_token:
        return _clone_https(repo, target_dir)
    else:
        # No auth — local or public repo
        cloned = git.Repo.clone_from(repo.url, str(target_dir), branch=repo.branch)
        return cloned, target_dir


def _clone_ssh(
    repo: GitRepository,
    target_dir: Path,
    encrypted_ssh_key: bytes | None,
) -> tuple[git.Repo, Path]:
    """Clone via SSH key auth.

    Follows the same pattern as tasks/sync.py:
    decrypt -> write to /dev/shm/ (tmpfs) -> use -> cleanup in finally.
    """
    if not encrypted_ssh_key:
        raise ValueError(
            f"encrypted_ssh_key required for SSH auth"
            f" (repo '{repo.name}', ssh_key_id={repo.ssh_key_id})"
        )

    fd, ssh_key_path = tempfile.mkstemp(dir="/dev/shm", prefix="labdog-", suffix=".key")
    os.close(fd)
    known_hosts_path = f"{ssh_key_path}.known_hosts"
    try:
        master_key = get_master_key()
        private_key = decrypt_ssh_key(encrypted_ssh_key, master_key)

        # Write to tmpfs — never touches disk
        with open(ssh_key_path, "w") as f:
            f.write(private_key)
        os.chmod(ssh_key_path, 0o600)

        # SEC-27: verify the server against the key recorded on the
        # repository row. This used to be accept-new *with*
        # UserKnownHostsFile=/dev/null, which never verifies anything —
        # every clone started from an empty file, so there was never a
        # first use and never a mismatch. On first contact we still
        # accept and record, so the next clone is checked.
        pinned = bool(repo.ssh_host_key_entry and repo.ssh_host_key_entry.strip())
        with open(known_hosts_path, "w") as f:
            if pinned:
                f.write(repo.ssh_host_key_entry.strip() + "\n")  # type: ignore[union-attr]
        os.chmod(known_hosts_path, 0o600)

        env = {
            **os.environ,
            "GIT_SSH_COMMAND": build_ssh_command(ssh_key_path, known_hosts_path, pinned=pinned),
        }

        cloned = git.Repo.clone_from(repo.url, str(target_dir), branch=repo.branch, env=env)

        # Trust on first use: record what the server presented so every
        # later clone is verified against it. The caller owns the
        # session this row belongs to and commits it.
        if not pinned:
            learned = Path(known_hosts_path).read_text().strip()
            if learned:
                repo.ssh_host_key_entry = learned

        return cloned, target_dir
    finally:
        # Always clean up SSH key from tmpfs
        for path in (ssh_key_path, known_hosts_path):
            if os.path.exists(path):
                os.unlink(path)


def _clone_https(repo: GitRepository, target_dir: Path) -> tuple[git.Repo, Path]:
    """Clone via HTTPS token auth. The token never reaches argv or disk.

    SEC-29: this used to embed the token in the URL
    (``https://oauth2:TOKEN@host/...``) and pass that to ``git clone``.
    The token was therefore on the command line — ``/proc/<pid>/cmdline``
    is world-readable, so any local account could read the PAT off a
    running clone — and it was written into ``.git/config`` until the
    ``set_url`` two lines later scrubbed it, which is a window, not an
    absence.

    It now travels as an ``Authorization`` header configured through
    ``GIT_CONFIG_*`` env vars, the same mechanism the pack sync path
    uses. ``/proc/<pid>/environ`` is readable only by the process owner
    and root, the URL stays clean, and there is nothing to scrub
    afterwards.
    """
    if not repo.encrypted_https_token:
        raise ValueError(f"HTTPS token not available for repository '{repo.name}'")

    master_key = get_master_key()
    # Reuse same AES-256-GCM encrypt/decrypt for both SSH keys and tokens
    token = decrypt_ssh_key(repo.encrypted_https_token, master_key)

    env = {**os.environ, **token_config_env(token)}
    cloned = git.Repo.clone_from(repo.url, str(target_dir), branch=repo.branch, env=env)
    return cloned, target_dir


def clone_repo_local(url: str, target_dir: Path, branch: str = "main") -> tuple[git.Repo, Path]:
    """Clone a local/public repo without auth. For testing."""
    cloned = git.Repo.clone_from(url, str(target_dir), branch=branch)
    return cloned, target_dir


def read_file_at_sha(repo_path: Path, file_path: str, sha: str) -> str:
    """Read a file's content at a specific commit SHA.

    Uses ``git show SHA:path`` — does NOT require checkout.

    Raises:
        FileNotFoundError: If file doesn't exist at that SHA.
    """
    repo = git.Repo(str(repo_path))
    try:
        blob = repo.commit(sha).tree / file_path
        return blob.data_stream.read().decode("utf-8")
    except (KeyError, git.exc.GitCommandError) as e:
        raise FileNotFoundError(f"File '{file_path}' not found at commit {sha[:8]}") from e


def get_current_sha(repo_path: Path) -> str:
    """Return HEAD commit SHA."""
    repo = git.Repo(str(repo_path))
    return repo.head.commit.hexsha


def cleanup_repo(repo_dir: Path) -> None:
    """Remove cloned repository directory."""
    if repo_dir.exists():
        shutil.rmtree(str(repo_dir))
