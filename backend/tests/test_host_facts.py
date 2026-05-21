"""Tests for hostname placeholder helpers and the auto-heal path in
``app.tasks.facts.collect_host_facts``.

The collect_host_facts task only rewrites ``Host.hostname`` when the
stored value matches the canonical ``host-<ip>`` placeholder produced
by the discovery / scan-approve fallback. Operator-chosen names are
left alone. This is exercised via mocked SSH so we can test the
auto-update branch without spinning up a real remote host.
"""

from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from app.discovery.verify import is_placeholder_hostname, placeholder_hostname
from app.models.host import Host
from tests.conftest import create_host, create_ssh_key

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Pure-function helpers (placeholder_hostname / is_placeholder_hostname)
# ---------------------------------------------------------------------------


class TestPlaceholderHelpers:
    def test_placeholder_format_is_stable(self):
        assert placeholder_hostname("10.10.3.190") == "host-10.10.3.190"
        assert placeholder_hostname("192.168.1.1") == "host-192.168.1.1"

    def test_is_placeholder_matches_canonical_form(self):
        assert is_placeholder_hostname("host-10.10.3.190", "10.10.3.190")

    def test_is_placeholder_rejects_real_names(self):
        assert not is_placeholder_hostname("tester3", "10.10.3.190")
        assert not is_placeholder_hostname("host-tester3", "10.10.3.190")
        assert not is_placeholder_hostname("10.10.3.190", "10.10.3.190")  # bare IP

    def test_is_placeholder_none_returns_false(self):
        assert not is_placeholder_hostname(None, "10.10.3.190")


# ---------------------------------------------------------------------------
# collect_host_facts hostname auto-heal
# ---------------------------------------------------------------------------


def _make_conn(stdout_by_cmd: dict[str, str]) -> MagicMock:
    """Build a mock asyncssh-style connection.

    Keyed on substring match against the command (first match wins).
    Unknown commands return empty stdout with exit_status=0 so the
    code path treats them as "tool not present" rather than crashing.
    """

    async def run(cmd, check=False):
        for key, stdout in stdout_by_cmd.items():
            if key in cmd:
                return MagicMock(exit_status=0, stdout=stdout, stderr="")
        return MagicMock(exit_status=0, stdout="", stderr="")

    conn = MagicMock()
    conn.run = run
    return conn


def _ssh_connect_patch(conn):
    @asynccontextmanager
    async def fake_ssh_connect_host(*args, **kwargs):
        yield conn

    # facts.py now imports ssh_connect_host inside the function body.
    # Patch the source module so both the import and the live object
    # are replaced for the duration of the test.
    return patch("app.ssh_utils.ssh_connect_host", new=fake_ssh_connect_host)


def _task_session_patch(db):
    @asynccontextmanager
    async def fake_task_session():
        yield db

    return patch("app.db.task_session", new=fake_task_session)


async def _run_collect_host_facts(host_id: int, db, conn):
    """Invoke the inner async body of collect_host_facts with all the
    external boundaries (DB session, SSH, crypto) stubbed out.

    We call the async helper directly rather than going through the
    Celery ``.apply()`` wrapper, which would try to spin a new event
    loop inside the pytest-asyncio loop and fail.
    """
    from app.tasks.facts import _collect_host_facts_async

    with (
        _task_session_patch(db),
        _ssh_connect_patch(conn),
        patch("asyncssh.import_private_key", return_value=MagicMock()),
        patch(
            "app.crypto.encryption.decrypt_ssh_key", return_value=b"fake-pem"
        ),
        patch(
            "app.crypto.key_management.get_master_key", return_value=b"fake-master"
        ),
    ):
        await _collect_host_facts_async(host_id)


class TestCollectHostFactsHostnameAutoHeal:
    """Verify the hostname auto-heal branch in collect_host_facts."""

    async def test_replaces_placeholder_with_fetched_hostname(self, db):
        ssh_key = await create_ssh_key(db)
        host = await create_host(
            db,
            hostname=placeholder_hostname("10.10.3.190"),
            ip="10.10.3.190",
            ssh_key_id=ssh_key.id,
        )
        conn = _make_conn({"hostname": "tester3\n"})

        await _run_collect_host_facts(host.id, db, conn)

        refreshed = (
            await db.execute(select(Host).where(Host.id == host.id))
        ).scalar_one()
        assert refreshed.hostname == "tester3"

    async def test_never_overwrites_operator_chosen_name(self, db):
        ssh_key = await create_ssh_key(db)
        host = await create_host(
            db,
            hostname="my-chosen-name",
            ip="10.10.3.191",
            ssh_key_id=ssh_key.id,
        )
        # Remote happens to report a different name -- must be ignored.
        conn = _make_conn({"hostname": "remote-says-this\n"})

        await _run_collect_host_facts(host.id, db, conn)

        refreshed = (
            await db.execute(select(Host).where(Host.id == host.id))
        ).scalar_one()
        assert refreshed.hostname == "my-chosen-name"

    async def test_skips_rename_on_name_collision(self, db):
        ssh_key = await create_ssh_key(db)
        # Another host already owns the name the remote would report.
        await create_host(
            db, hostname="tester3", ip="10.10.3.99", ssh_key_id=ssh_key.id
        )
        placeholder_host = await create_host(
            db,
            hostname=placeholder_hostname("10.10.3.190"),
            ip="10.10.3.190",
            ssh_key_id=ssh_key.id,
        )
        conn = _make_conn({"hostname": "tester3\n"})

        await _run_collect_host_facts(placeholder_host.id, db, conn)

        refreshed = (
            await db.execute(select(Host).where(Host.id == placeholder_host.id))
        ).scalar_one()
        # Placeholder preserved -- collision skipped silently rather
        # than mangling the fetched name with a numeric suffix.
        assert refreshed.hostname == placeholder_hostname("10.10.3.190")

    async def test_no_rename_when_remote_returns_empty(self, db):
        ssh_key = await create_ssh_key(db)
        host = await create_host(
            db,
            hostname=placeholder_hostname("10.10.3.190"),
            ip="10.10.3.190",
            ssh_key_id=ssh_key.id,
        )
        conn = _make_conn({"hostname": "   \n"})  # whitespace only

        await _run_collect_host_facts(host.id, db, conn)

        refreshed = (
            await db.execute(select(Host).where(Host.id == host.id))
        ).scalar_one()
        assert refreshed.hostname == placeholder_hostname("10.10.3.190")
