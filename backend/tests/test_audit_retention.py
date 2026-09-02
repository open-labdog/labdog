"""Retention-window guards for the audit-log and SSH-transcript pruners.

``logging.audit_retention_days`` documents ``0`` as "keep forever", but both
pruners fed the value straight into ``timedelta(days=...)``. At 0 the cutoff
becomes *now*, so ``DELETE ... WHERE created_at < cutoff`` matches the entire
table — the setting that means "never delete" performed the largest possible
delete.

This was latent rather than live: ``_get_retention_days`` read through
``get_setting_sync_typed``, whose sync engine could never connect (no psycopg2
in the dependency set), so it always returned the default of 90. Fixing that
reader is what turns this from latent into live, which is why the guard lands
first.

These tests exercise the guard, so they must not need a database: the point is
that the function returns *before* opening a session.
"""

import pytest

from app.tasks import audit_retention


@pytest.mark.parametrize("disabled_value", [0, -1])
class TestRetentionDisabled:
    @pytest.mark.asyncio
    async def test_audit_log_prune_is_a_noop(self, monkeypatch, disabled_value):
        async def _fake_days() -> int:
            return disabled_value

        monkeypatch.setattr(audit_retention, "_get_retention_days", _fake_days)

        result = await audit_retention._prune_audit_logs()

        assert result["deleted"] == 0
        assert result["retention_days"] == disabled_value
        assert result["skipped"] == "retention disabled"

    @pytest.mark.asyncio
    async def test_transcript_prune_is_a_noop(self, monkeypatch, disabled_value):
        async def _fake_days() -> int:
            return disabled_value

        monkeypatch.setattr(audit_retention, "_get_retention_days", _fake_days)

        result = await audit_retention._prune_ssh_transcripts()

        assert result["deleted"] == 0
        assert result["retention_days"] == disabled_value
        assert result["skipped"] == "retention disabled"


class TestRetentionEnabledStillRuns:
    """A positive window must not take the skip path — otherwise the guard
    would silently disable retention altogether."""

    @pytest.mark.asyncio
    async def test_positive_window_does_not_skip(self, monkeypatch):
        async def _fake_days() -> int:
            return 90

        monkeypatch.setattr(audit_retention, "_get_retention_days", _fake_days)

        opened = {"session": False}

        def _boom(*_args, **_kwargs):
            opened["session"] = True
            raise RuntimeError("stop here — reaching the DB is the assertion")

        monkeypatch.setattr("app.db.task_session", _boom)

        with pytest.raises(RuntimeError):
            await audit_retention._prune_audit_logs()
        assert opened["session"] is True
