"""Tests for scripts/rotate_encryption_key.py.

Architecture note: the rotation logic lives in ``scripts.rotate_encryption_key``
as an importable ``async def rotate(session, old_key, new_key) -> dict[str, int]``.
These tests call that function directly inside the same savepoint-backed session
fixture used by the rest of the test suite, so no subprocess or real transaction
commit is needed.
"""

from __future__ import annotations

import base64

import pytest
from cryptography.exceptions import InvalidTag
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto.encryption import decrypt_ssh_key, encrypt_ssh_key
from app.crypto.key_management import generate_master_key
from app.models.git_repository import GitAuthType, GitRepository
from app.models.ssh_key import SSHKey
from app.proxmox.models import ProxmoxNode
from scripts.rotate_encryption_key import rotate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_key() -> bytes:
    return base64.b64decode(generate_master_key())


async def _insert_ssh_key(db: AsyncSession, key_a: bytes) -> tuple[int, str]:
    plaintext = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\ntest-ssh-key\n-----END OPENSSH PRIVATE KEY-----"
    )
    row = SSHKey(
        name="rotate-test-key",
        encrypted_private_key=encrypt_ssh_key(plaintext, key_a),
        public_key="ssh-ed25519 AAAA test",
    )
    db.add(row)
    await db.flush()
    return row.id, plaintext


async def _insert_proxmox_node(db: AsyncSession, key_a: bytes) -> tuple[int, str]:
    secret = "abc123-token-secret"
    row = ProxmoxNode(
        name="rotate-test-pve",
        api_url="https://pve.example.com:8006",
        token_id="root@pam!labdog",
        encrypted_token_secret=encrypt_ssh_key(secret, key_a),
    )
    db.add(row)
    await db.flush()
    return row.id, secret


async def _insert_git_repo_with_token(db: AsyncSession, key_a: bytes) -> tuple[int, str]:
    token = "ghp_supersecret"
    row = GitRepository(
        name="rotate-test-repo",
        url="https://github.com/example/repo.git",
        auth_type=GitAuthType.https_token,
        encrypted_https_token=encrypt_ssh_key(token, key_a),
    )
    db.add(row)
    await db.flush()
    return row.id, token


async def _insert_git_repo_no_token(db: AsyncSession) -> int:
    """A git repo with no HTTPS token — encrypted_https_token stays NULL."""
    row = GitRepository(
        name="rotate-test-repo-no-token",
        url="git@github.com:example/repo.git",
        auth_type=GitAuthType.none,
        encrypted_https_token=None,
    )
    db.add(row)
    await db.flush()
    return row.id


# ---------------------------------------------------------------------------
# Happy-path rotation test
# ---------------------------------------------------------------------------


async def test_rotate_re_encrypts_all_columns(db: AsyncSession) -> None:
    """After rotation every encrypted column decrypts correctly under key B."""
    key_a = _make_key()
    key_b = _make_key()

    ssh_id, ssh_plain = await _insert_ssh_key(db, key_a)
    pve_id, pve_plain = await _insert_proxmox_node(db, key_a)
    git_id, git_plain = await _insert_git_repo_with_token(db, key_a)
    no_token_id = await _insert_git_repo_no_token(db)

    counts = await rotate(db, key_a, key_b)

    # All three tables must appear in the report
    assert counts["ssh_keys"] == 1
    assert counts["proxmox_nodes"] == 1
    assert counts["git_repositories"] == 1  # only the row that has a token

    # rotate() issues Core UPDATE statements that bypass the ORM identity map.
    # Expire all cached objects so the next SELECT hits the DB.
    db.expire_all()

    # Fetch the rotated rows and verify plaintext is recoverable under key B
    ssh_row = (await db.execute(select(SSHKey).where(SSHKey.id == ssh_id))).scalar_one()
    assert decrypt_ssh_key(ssh_row.encrypted_private_key, key_b) == ssh_plain

    pve_row = (await db.execute(select(ProxmoxNode).where(ProxmoxNode.id == pve_id))).scalar_one()
    assert decrypt_ssh_key(pve_row.encrypted_token_secret, key_b) == pve_plain

    git_row = (
        await db.execute(select(GitRepository).where(GitRepository.id == git_id))
    ).scalar_one()
    assert decrypt_ssh_key(git_row.encrypted_https_token, key_b) == git_plain  # type: ignore[arg-type]

    # The NULL-token row must still have NULL after rotation
    no_token_row = (
        await db.execute(select(GitRepository).where(GitRepository.id == no_token_id))
    ).scalar_one()
    assert no_token_row.encrypted_https_token is None

    # The old key must no longer decrypt successfully
    with pytest.raises((InvalidTag, Exception)):
        decrypt_ssh_key(ssh_row.encrypted_private_key, key_a)


# ---------------------------------------------------------------------------
# Idempotency: old == new → short-circuit at the CLI layer
# ---------------------------------------------------------------------------


async def test_cli_noop_when_keys_equal(capsys: pytest.CaptureFixture[str]) -> None:
    """_main() prints a no-op message and returns without touching the DB."""
    from scripts.rotate_encryption_key import _main

    key_b64 = generate_master_key()
    # Both keys identical → should print "No-op" and return cleanly
    await _main(key_b64, key_b64)

    captured = capsys.readouterr()
    assert "No-op" in captured.out


async def test_rotate_same_key_data_still_readable(db: AsyncSession) -> None:
    """rotate() called with old == new leaves all data readable under that key.

    The CLI short-circuits before calling rotate() when old == new, so this
    test exercises the underlying rotate() function's safety when the caller
    skips the CLI guard.
    """
    key_a = _make_key()

    ssh_id, ssh_plain = await _insert_ssh_key(db, key_a)

    counts = await rotate(db, key_a, key_a)

    # All counts are valid integers
    for count in counts.values():
        assert isinstance(count, int)

    db.expire_all()

    # Data is still readable under key_a
    ssh_row = (await db.execute(select(SSHKey).where(SSHKey.id == ssh_id))).scalar_one()
    assert decrypt_ssh_key(ssh_row.encrypted_private_key, key_a) == ssh_plain


# ---------------------------------------------------------------------------
# Wrong-key test: rotate() raises and DB is unchanged
# ---------------------------------------------------------------------------


async def test_rotate_wrong_old_key_raises(db: AsyncSession) -> None:
    """rotate() with a wrong old key raises InvalidTag before any UPDATE runs.

    The decrypt attempt raises before encrypt/UPDATE for the first table, so
    the session state is clean after the exception.  The row must still be
    decryptable under the original key.
    """
    key_a = _make_key()
    key_wrong = _make_key()  # not what the data was encrypted with
    key_b = _make_key()

    ssh_id, ssh_plain = await _insert_ssh_key(db, key_a)

    with pytest.raises((InvalidTag, Exception)):
        await rotate(db, key_wrong, key_b)

    # Fetch fresh — confirms the row is still in the DB and readable under key_a.
    ssh_row = (await db.execute(select(SSHKey).where(SSHKey.id == ssh_id))).scalar_one()
    assert decrypt_ssh_key(ssh_row.encrypted_private_key, key_a) == ssh_plain


# ---------------------------------------------------------------------------
# Key validation (pure-unit, no DB)
# ---------------------------------------------------------------------------


def test_decode_key_rejects_wrong_length() -> None:
    """_decode_key must reject keys that are not 32 bytes."""
    from scripts.rotate_encryption_key import _decode_key

    short = base64.b64encode(b"too-short").decode()
    with pytest.raises(SystemExit):
        _decode_key(short, "test key")


def test_decode_key_rejects_invalid_base64() -> None:
    """_decode_key must reject non-base64 input."""
    from scripts.rotate_encryption_key import _decode_key

    with pytest.raises(SystemExit):
        _decode_key("not!!base64!!", "test key")
