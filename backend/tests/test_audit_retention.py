"""Retention-window guards for the audit-log and SSH-transcript pruners.

``logging.audit_retention_days`` documents ``0`` as "keep forever", but both
pruners fed the value straight into ``timedelta(days=...)``. At 0 the cutoff
becomes *now*, so ``DELETE ... WHERE created_at < cutoff`` matches the entire
table — the setting that means "never delete" performing the largest possible
delete.

It used to be unreachable: ``_get_retention_days`` read through
``get_setting_sync_typed``, whose sync engine could never connect (no psycopg2
in the dependency set), so it always returned the default of 90. That reader
has since been removed (BUG-61) and the job now reads the setting
authoritatively inside its own session — which is what turns this guard from
theoretical into load-bearing.

The pruners open a session before reading the window, so ``task_session`` is
faked here rather than a database being started: the assertion is that the
guard returns before any DELETE is issued, not that Postgres behaves.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.tasks import audit_retention


@pytest.fixture
def fake_session(monkeypatch):
    """Replace ``task_session`` with one that records what ran on it."""
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()

    @asynccontextmanager
    async def _fake_task_session():
        yield session

    monkeypatch.setattr("app.db.task_session", _fake_task_session)
    return session


def _days(value: int):
    async def _fake(_db) -> int:
        return value

    return _fake


@pytest.mark.parametrize("disabled_value", [0, -1])
class TestRetentionDisabled:
    async def test_audit_log_prune_is_a_noop(self, monkeypatch, fake_session, disabled_value):
        monkeypatch.setattr(audit_retention, "_get_retention_days", _days(disabled_value))

        result = await audit_retention._prune_audit_logs()

        assert result["deleted"] == 0
        assert result["retention_days"] == disabled_value
        assert result["skipped"] == "retention disabled"
        # The point of the guard: nothing was deleted, not merely nothing
        # reported. A DELETE would have gone through session.execute.
        assert fake_session.execute.await_count == 0

    async def test_transcript_prune_is_a_noop(self, monkeypatch, fake_session, disabled_value):
        monkeypatch.setattr(audit_retention, "_get_retention_days", _days(disabled_value))

        result = await audit_retention._prune_ssh_transcripts()

        assert result["deleted"] == 0
        assert result["retention_days"] == disabled_value
        assert result["skipped"] == "retention disabled"
        assert fake_session.execute.await_count == 0


class TestRetentionEnabledStillRuns:
    """A positive window must not take the skip path — otherwise the guard
    would silently disable retention altogether."""

    async def test_positive_window_issues_the_delete(self, monkeypatch, fake_session):
        monkeypatch.setattr(audit_retention, "_get_retention_days", _days(90))
        fake_session.execute.return_value = MagicMock(scalar=MagicMock(return_value=7))

        result = await audit_retention._prune_audit_logs()

        assert "skipped" not in result
        assert result["retention_days"] == 90
        # A count and a delete.
        assert fake_session.execute.await_count == 2
        assert fake_session.commit.await_count == 1


class TestTheWindowIsReadFromTheDatabase:
    """BUG-61: the reader must consult the session it was given.

    The old implementation opened its own connection with a URL that needed
    a driver the project does not depend on, swallowed the resulting
    ModuleNotFoundError, and returned the hardcoded default — so an operator
    who set this to 30 still got 90, silently and forever.
    """

    async def test_it_awaits_the_async_getter_with_the_session(self, monkeypatch):
        seen = {}

        async def _fake_get_setting_typed(key, db):
            seen["key"] = key
            seen["db"] = db
            return 30

        monkeypatch.setattr("app.settings_service.get_setting_typed", _fake_get_setting_typed)
        sentinel = object()

        assert await audit_retention._get_retention_days(sentinel) == 30
        assert seen["key"] == "logging.audit_retention_days"
        assert seen["db"] is sentinel, "the reader must use the caller's session"
