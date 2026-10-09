"""Encryption-key rotation script for LabDog.

Re-encrypts every ``encrypted_*`` column in a single database transaction so
there is no window in which some rows use the old key and others use the new
one.  If the process is interrupted mid-run the transaction rolls back and
the database remains entirely under the old key.

Usage (preferred — reads env vars if flags are absent):
    LABDOG_OLD_KEY=<base64-32>  LABDOG_NEW_KEY=<base64-32> \\
        python -m scripts.rotate_encryption_key

Usage (explicit flags):
    python -m scripts.rotate_encryption_key \\
        --old-key <base64-32> --new-key <base64-32>
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto.encryption import decrypt_ssh_key, encrypt_ssh_key

# ---------------------------------------------------------------------------
# Column registry — every encrypted column that must be rotated.
# Each entry: (SQLAlchemy model class, column name, nullable)
#
# A column missing from here keeps its old-key ciphertext and stops
# decrypting the moment the key changes, while the script reports success
# (BUG-108). test_registry_covers_every_encrypted_column fails when a model
# gains an ``encrypted_*`` column this list does not name.
# ---------------------------------------------------------------------------


def _build_column_registry() -> list[tuple[Any, str, bool]]:
    """Return the list of (Model, column_name, nullable) tuples to rotate.

    Imports are deferred so this module can be imported in test environments
    that patch settings before the app module tree loads.
    """
    from app.ai.models import AIProvider
    from app.grafana.models import GrafanaInstance
    from app.models.git_repository import GitRepository
    from app.models.ssh_key import SSHKey
    from app.notifications.models import SMTPSettings
    from app.proxmox.models import ProxmoxNode

    return [
        (SSHKey, "encrypted_private_key", False),
        (ProxmoxNode, "encrypted_token_secret", False),
        (GitRepository, "encrypted_https_token", True),
        (GitRepository, "encrypted_webhook_secret", True),
        (GrafanaInstance, "encrypted_token", True),
        (AIProvider, "encrypted_api_key", True),
        (SMTPSettings, "encrypted_password", True),
    ]


# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------


def _decode_key(b64: str, label: str) -> bytes:
    """Decode a base64 string to exactly 32 bytes or raise SystemExit."""
    try:
        key = base64.b64decode(b64)
    except Exception as exc:
        print(f"ERROR: {label} is not valid base64: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    if len(key) != 32:
        print(
            f"ERROR: {label} must decode to exactly 32 bytes, got {len(key)}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return key


# ---------------------------------------------------------------------------
# Alembic version check
# ---------------------------------------------------------------------------


async def _assert_migrations_current(session: AsyncSession) -> None:
    """Raise SystemExit if there are unapplied alembic migrations.

    A pending migration means the schema the rotation script was written
    against may not match the live DB, making it unsafe to proceed.
    """
    # Resolve alembic executable relative to the running Python interpreter
    # so it picks up the venv rather than whatever happens to be on PATH.
    alembic_bin = Path(sys.executable).parent / "alembic"
    backend_dir = Path(__file__).resolve().parents[1]

    result = subprocess.run(
        [str(alembic_bin), "heads"],
        capture_output=True,
        text=True,
        cwd=str(backend_dir),
    )
    if result.returncode != 0:
        print(
            "ERROR: could not determine alembic heads:\n" + result.stderr,
            file=sys.stderr,
        )
        raise SystemExit(1)

    # "alembic heads" outputs one line per head revision, e.g. "abc123 (head)"
    heads = {line.split()[0] for line in result.stdout.splitlines() if line.strip()}

    rows = await session.execute(text("SELECT version_num FROM alembic_version"))
    applied = {row[0] for row in rows}

    if heads != applied:
        print(
            f"ERROR: database has unapplied migrations.\n"
            f"  Applied : {sorted(applied)}\n"
            f"  Expected: {sorted(heads)}\n"
            f"Run 'alembic upgrade head' before rotating the key.",
            file=sys.stderr,
        )
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# Core rotation logic (importable for tests)
# ---------------------------------------------------------------------------


async def rotate(
    session: AsyncSession,
    old_key: bytes,
    new_key: bytes,
) -> dict[str, int]:
    """Re-encrypt every encrypted column under *new_key*.

    Args:
        session: An active SQLAlchemy async session.  The caller is
            responsible for wrapping this in a transaction.
        old_key: 32-byte AES-256-GCM key currently protecting the data.
        new_key: 32-byte AES-256-GCM key to protect the data going forward.

    Returns:
        Mapping of ``table.column`` to number of values rotated. Keyed by
        column, not table: ``git_repositories`` holds two secrets, and a
        per-table key would let the second column's count overwrite the
        first's.

    Raises:
        cryptography.exceptions.InvalidTag: If *old_key* is wrong for any
            ciphertext — the caller's transaction should be rolled back.
    """
    counts: dict[str, int] = {}

    for Model, col_name, _nullable in _build_column_registry():
        column = getattr(Model, col_name)

        # Select the id and this one column rather than whole ORM rows: a
        # table with two encrypted columns is visited twice, and the first
        # pass's Core UPDATEs leave any ORM objects it loaded stale.
        result = await session.execute(select(Model.id, column).where(column.is_not(None)))

        rotated = 0
        for row_id, blob in result.all():
            plaintext = decrypt_ssh_key(blob, old_key)
            new_blob = encrypt_ssh_key(plaintext, new_key)
            await session.execute(
                update(Model).where(Model.id == row_id).values({col_name: new_blob})
            )
            rotated += 1

        counts[f"{Model.__tablename__}.{col_name}"] = rotated

    return counts


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


async def _main(old_b64: str, new_b64: str) -> None:
    old_key = _decode_key(old_b64, "old key (--old-key / LABDOG_OLD_KEY)")
    new_key = _decode_key(new_b64, "new key (--new-key / LABDOG_NEW_KEY)")

    if old_key == new_key:
        print("No-op: old key and new key are identical.")
        return

    # Import here so the module can be imported without triggering DB init
    from app.db import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        await _assert_migrations_current(session)
        # Single transaction — either all rows rotate or none do.
        async with session.begin():
            try:
                counts = await rotate(session, old_key, new_key)
            except Exception as exc:
                print(
                    f"ERROR: decryption failed — wrong old key or corrupt data.\n"
                    f"  {exc}\n"
                    f"Transaction rolled back; the database is unchanged.",
                    file=sys.stderr,
                )
                raise SystemExit(1) from exc

    for column, count in counts.items():
        print(f"  {column}: {count} value(s) rotated")
    print("Key rotation complete.")


def _parse_args() -> tuple[str, str]:
    parser = argparse.ArgumentParser(
        description="Re-encrypt every encrypted column under a new AES-256-GCM key.",
    )
    parser.add_argument(
        "--old-key",
        metavar="BASE64",
        help="Current 32-byte key in base64.  Defaults to LABDOG_OLD_KEY env var.",
    )
    parser.add_argument(
        "--new-key",
        metavar="BASE64",
        help="New 32-byte key in base64.  Defaults to LABDOG_NEW_KEY env var.",
    )
    args = parser.parse_args()

    old_b64 = args.old_key or os.environ.get("LABDOG_OLD_KEY", "")
    new_b64 = args.new_key or os.environ.get("LABDOG_NEW_KEY", "")

    if not old_b64:
        parser.error("old key is required: pass --old-key or set LABDOG_OLD_KEY")
    if not new_b64:
        parser.error("new key is required: pass --new-key or set LABDOG_NEW_KEY")

    return old_b64, new_b64


if __name__ == "__main__":
    old_b64, new_b64 = _parse_args()
    asyncio.run(_main(old_b64, new_b64))
