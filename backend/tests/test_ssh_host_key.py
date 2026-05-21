"""Tests for SEC-16: SSH host-key TOFU verification.

Covers:
- First connect with empty ssh_host_key_entry succeeds, persists key on Host row.
- Second connect verifies against persisted key; succeeds when matching.
- Second connect with mismatched key raises HostKeyMismatchError.
- POST /api/hosts/{id}/trust-host-key clears the column (superuser only) and
  next connect re-TOFUs.

Uses the same patching pattern as test_host_facts.py: mock asyncssh.connect so
no real SSH is attempted.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import asyncssh
import pytest
from sqlalchemy import select

from app.models.host import Host
from app.ssh_utils import HostKeyMismatchError, ssh_connect_host
from tests.conftest import create_host, create_ssh_key

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_server_key(key_type: str = "ssh-ed25519", pubkey_b64: str = "AAAA") -> MagicMock:
    """Build a mock asyncssh public key that export_public_key() returns a plausible line."""
    key = MagicMock(spec=asyncssh.SSHKey)
    key.export_public_key.return_value = f"{key_type} {pubkey_b64}\n".encode()
    return key


def _make_conn(server_key: MagicMock) -> MagicMock:
    """Build a mock asyncssh connection that returns *server_key* from get_server_host_key."""
    conn = MagicMock()
    conn.get_server_host_key.return_value = server_key
    conn.close = MagicMock()

    async def wait_closed():
        pass

    conn.wait_closed = wait_closed
    return conn


def _patch_asyncssh_connect(conn: MagicMock | None = None, raise_exc: Exception | None = None):
    """Patch asyncssh.connect at the source module level.

    If *raise_exc* is set, the mock raises that exception instead of yielding
    a connection.
    """
    if raise_exc is not None:
        mock = AsyncMock(side_effect=raise_exc)
    else:
        mock = AsyncMock(return_value=conn)
    return patch("asyncssh.connect", new=mock)


# ---------------------------------------------------------------------------
# ssh_connect_host unit tests (no HTTP layer)
# ---------------------------------------------------------------------------


class TestSshConnectHostTOFU:
    """Direct unit tests for the ssh_connect_host helper."""

    async def test_first_connect_persists_key(self, db):
        """Empty ssh_host_key_entry: accept any key, persist it, commit."""
        ssh_key = await create_ssh_key(db)
        host = await create_host(db, ip="10.0.0.1", ssh_key_id=ssh_key.id)
        assert host.ssh_host_key_entry is None

        server_key = _make_fake_server_key(pubkey_b64="FIRSTKEY")
        conn = _make_conn(server_key)

        with _patch_asyncssh_connect(conn):
            async with ssh_connect_host(host, db, client_keys=[]):
                pass

        refreshed = (await db.execute(select(Host).where(Host.id == host.id))).scalar_one()
        assert refreshed.ssh_host_key_entry is not None
        assert "FIRSTKEY" in refreshed.ssh_host_key_entry
        assert "10.0.0.1" in refreshed.ssh_host_key_entry

    async def test_second_connect_matching_key_succeeds(self, db):
        """Stored key matches server key: connection succeeds, no exception."""
        ssh_key = await create_ssh_key(db)
        host = await create_host(db, ip="10.0.0.2", ssh_key_id=ssh_key.id)

        server_key = _make_fake_server_key(pubkey_b64="STOREDKEY")
        conn = _make_conn(server_key)

        # Perform TOFU on first connect.
        with _patch_asyncssh_connect(conn):
            async with ssh_connect_host(host, db, client_keys=[]):
                pass

        stored_entry = host.ssh_host_key_entry
        assert stored_entry is not None

        # Second connect: asyncssh reads the tempfile (known_hosts) and
        # accepts the same key.  We patch asyncssh.connect to succeed.
        with _patch_asyncssh_connect(conn):
            async with ssh_connect_host(host, db, client_keys=[]):
                pass

        refreshed = (await db.execute(select(Host).where(Host.id == host.id))).scalar_one()
        # Entry unchanged.
        assert refreshed.ssh_host_key_entry == stored_entry

    async def test_second_connect_mismatched_key_raises(self, db):
        """Stored key does not match server key: HostKeyMismatchError is raised."""
        ssh_key = await create_ssh_key(db)
        host = await create_host(db, ip="10.0.0.3", ssh_key_id=ssh_key.id)

        # Seed a known key into the host row.
        host.ssh_host_key_entry = "10.0.0.3 ssh-ed25519 AAAA_STORED_KEY"
        await db.flush()

        # asyncssh raises HostKeyNotVerifiable when the presented key
        # does not match the known_hosts file.
        mismatch_exc = asyncssh.HostKeyNotVerifiable("Host key not verifiable")

        with _patch_asyncssh_connect(raise_exc=mismatch_exc):
            with pytest.raises(HostKeyMismatchError) as exc_info:
                async with ssh_connect_host(host, db, client_keys=[]):
                    pass

        assert "10.0.0.3" in str(exc_info.value)
        assert "trust-host-key" in str(exc_info.value)

    async def test_tofu_skipped_when_server_returns_no_key(self, db):
        """If get_server_host_key() returns None, ssh_host_key_entry stays NULL."""
        ssh_key = await create_ssh_key(db)
        host = await create_host(db, ip="10.0.0.4", ssh_key_id=ssh_key.id)
        assert host.ssh_host_key_entry is None

        conn = _make_conn(server_key=None)

        with _patch_asyncssh_connect(conn):
            async with ssh_connect_host(host, db, client_keys=[]):
                pass

        refreshed = (await db.execute(select(Host).where(Host.id == host.id))).scalar_one()
        # Still NULL because server sent no key.
        assert refreshed.ssh_host_key_entry is None


# ---------------------------------------------------------------------------
# trust-host-key API endpoint tests
# ---------------------------------------------------------------------------


class TestTrustHostKeyEndpoint:
    """Integration tests for POST /api/hosts/{id}/trust-host-key."""

    async def test_clears_stored_key_and_emits_audit_log(self, superuser_client, db):
        """Superuser POST clears ssh_host_key_entry and writes an audit log row."""
        from app.models.audit_log import AuditLog

        ssh_key = await create_ssh_key(db)
        host = await create_host(db, ip="10.0.1.1", ssh_key_id=ssh_key.id)
        host.ssh_host_key_entry = "10.0.1.1 ssh-ed25519 AAAA_OLD_KEY"
        await db.flush()

        resp = await superuser_client.post(f"/api/hosts/{host.id}/trust-host-key")
        assert resp.status_code == 204

        refreshed = (await db.execute(select(Host).where(Host.id == host.id))).scalar_one()
        assert refreshed.ssh_host_key_entry is None

        audit_result = await db.execute(
            select(AuditLog).where(
                AuditLog.action == "trust_host_key",
                AuditLog.entity_type == "host",
                AuditLog.entity_id == host.id,
            )
        )
        audit_row = audit_result.scalar_one_or_none()
        assert audit_row is not None

    async def test_returns_404_for_missing_host(self, superuser_client, db):
        resp = await superuser_client.post("/api/hosts/99999/trust-host-key")
        assert resp.status_code == 404

    async def test_regular_user_cannot_trust_host_key(self, regular_user_client, db):
        """Non-superuser gets 403."""
        ssh_key = await create_ssh_key(db)
        host = await create_host(db, ip="10.0.1.2", ssh_key_id=ssh_key.id)

        resp = await regular_user_client.post(f"/api/hosts/{host.id}/trust-host-key")
        assert resp.status_code == 403

    async def test_re_tofu_after_trust_clears_key(self, db):
        """After trust-host-key clears the entry, next connect stores the new key."""
        ssh_key = await create_ssh_key(db)
        host = await create_host(db, ip="10.0.1.3", ssh_key_id=ssh_key.id)

        # Simulate having a stale key.
        host.ssh_host_key_entry = "10.0.1.3 ssh-ed25519 AAAA_OLD"
        await db.flush()

        # Clear it (simulating the endpoint action).
        host.ssh_host_key_entry = None
        await db.flush()

        # Now connect: TOFU should fire and store the new key.
        new_server_key = _make_fake_server_key(pubkey_b64="AAAA_NEW")
        conn = _make_conn(new_server_key)

        with _patch_asyncssh_connect(conn):
            async with ssh_connect_host(host, db, client_keys=[]):
                pass

        refreshed = (await db.execute(select(Host).where(Host.id == host.id))).scalar_one()
        assert refreshed.ssh_host_key_entry is not None
        assert "AAAA_NEW" in refreshed.ssh_host_key_entry
        assert "AAAA_OLD" not in refreshed.ssh_host_key_entry
