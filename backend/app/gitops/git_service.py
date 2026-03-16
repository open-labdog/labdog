"""Git operations service for GitOps integration.

Handles clone/pull with SSH key or HTTPS token authentication.
All credential handling uses temp files (/dev/shm/) with cleanup in finally blocks.
"""

import os
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import git  # gitpython

from app.crypto.encryption import decrypt_ssh_key
from app.crypto.key_management import get_master_key
from app.models.git_repository import GitAuthType, GitRepository


def clone_repo(
    repo: GitRepository,
    target_dir: Path | None = None,
    *,
    encrypted_ssh_key: bytes | None = None,
) -> tuple[git.Repo, Path]:
    """Clone a Git repository to a temp directory.

    For SSH auth: decrypts key -> writes to /dev/shm/ -> sets GIT_SSH_COMMAND -> clones -> cleans key
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
        target_dir = Path(tempfile.mkdtemp(prefix="barricade-git-"))

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
            f"encrypted_ssh_key required for SSH auth (repo '{repo.name}', ssh_key_id={repo.ssh_key_id})"
        )

    ssh_key_path = f"/dev/shm/barricade-git-{repo.id}.key"
    try:
        master_key = get_master_key()
        private_key = decrypt_ssh_key(encrypted_ssh_key, master_key)

        # Write to tmpfs — never touches disk
        with open(ssh_key_path, "w") as f:
            f.write(private_key)
        os.chmod(ssh_key_path, 0o600)

        ssh_cmd = (
            f"ssh -i {ssh_key_path} "
            "-o StrictHostKeyChecking=no "
            "-o UserKnownHostsFile=/dev/null"
        )
        env = {**os.environ, "GIT_SSH_COMMAND": ssh_cmd}

        cloned = git.Repo.clone_from(
            repo.url, str(target_dir), branch=repo.branch, env=env
        )
        return cloned, target_dir
    finally:
        # Always clean up SSH key from tmpfs
        if os.path.exists(ssh_key_path):
            os.unlink(ssh_key_path)


def _clone_https(repo: GitRepository, target_dir: Path) -> tuple[git.Repo, Path]:
    """Clone via HTTPS token auth. Token never persisted in .git/config."""
    if not repo.encrypted_https_token:
        raise ValueError(f"HTTPS token not available for repository '{repo.name}'")

    master_key = get_master_key()
    # Reuse same AES-256-GCM encrypt/decrypt for both SSH keys and tokens
    token = decrypt_ssh_key(repo.encrypted_https_token, master_key)

    # Embed token in URL: https://github.com/u/r.git -> https://oauth2:TOKEN@github.com/u/r.git
    parsed = urlparse(repo.url)
    host_with_port = parsed.hostname or ""
    if parsed.port:
        host_with_port += f":{parsed.port}"
    auth_url = urlunparse(parsed._replace(netloc=f"oauth2:{token}@{host_with_port}"))

    cloned = git.Repo.clone_from(auth_url, str(target_dir), branch=repo.branch)

    # Scrub token from .git/config — set remote to original URL
    cloned.remote("origin").set_url(repo.url)

    return cloned, target_dir


def clone_repo_local(
    url: str, target_dir: Path, branch: str = "main"
) -> tuple[git.Repo, Path]:
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
        raise FileNotFoundError(
            f"File '{file_path}' not found at commit {sha[:8]}"
        ) from e


def get_current_sha(repo_path: Path) -> str:
    """Return HEAD commit SHA."""
    repo = git.Repo(str(repo_path))
    return repo.head.commit.hexsha


def cleanup_repo(repo_dir: Path) -> None:
    """Remove cloned repository directory."""
    if repo_dir.exists():
        shutil.rmtree(str(repo_dir))
