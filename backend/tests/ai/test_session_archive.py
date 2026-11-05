"""Clearing out old sessions: archiving, paging, and deleting in bulk.

Archiving is the everyday tool because it keeps what a delete loses —
the rollback, the alert's link, and the record the full-auto safeguards
count. Deleting is still there, with refusals for exactly those cases.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.ai.models import AISession, AIToolCall
from app.models.app_setting import AppSetting
from app.models.audit_log import AuditLog
from app.settings_service import invalidate_cache

NOW = datetime.now(UTC)


async def _set(db, key: str, value: str) -> None:
    row = (await db.execute(select(AppSetting).where(AppSetting.key == key))).scalar_one_or_none()
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))
    await db.flush()
    invalidate_cache(key)


async def _session(
    db,
    provider,
    *,
    status="succeeded",
    mode="chat",
    autonomy="read_only",
    ended=timedelta(days=2),
    archived=False,
) -> AISession:
    session = AISession(
        provider_id=provider.id,
        mode=mode,
        mission=f"{mode} mission",
        autonomy_level=autonomy,
        status=status,
        target_host_ids=[],
    )
    db.add(session)
    await db.flush()
    session.created_at = NOW - ended - timedelta(minutes=5)
    if status in ("succeeded", "failed", "cancelled"):
        session.finished_at = NOW - ended
    if archived:
        session.archived_at = NOW - timedelta(hours=1)
    await db.flush()
    return session


async def _changed_a_host(db, session, *, snapshot=None) -> None:
    db.add(
        AIToolCall(
            session_id=session.id,
            tool_name="run_ssh_command",
            classification="mutating",
            status="executed",
            target_host_id=None,
            snapshot_name=snapshot,
        )
    )
    await db.flush()


async def _ids(client, **params) -> list[int]:
    resp = await client.get("/api/ai/sessions", params=params)
    assert resp.status_code == 200, resp.text
    return [s["id"] for s in resp.json()]


class TestTheList:
    async def test_archived_sessions_are_left_out(self, superuser_client, db, ai_provider):
        kept = await _session(db, ai_provider)
        gone = await _session(db, ai_provider, archived=True)
        await db.commit()

        assert kept.id in await _ids(superuser_client)
        assert gone.id not in await _ids(superuser_client)
        assert await _ids(superuser_client, view="archived") == [gone.id]
        assert {kept.id, gone.id} <= set(await _ids(superuser_client, view="all"))

    async def test_pages(self, superuser_client, db, ai_provider):
        made = [
            (await _session(db, ai_provider, ended=timedelta(days=d))).id for d in range(5, 0, -1)
        ]
        await db.commit()
        newest_first = made[::-1]

        first = await _ids(superuser_client, limit=2)
        assert first == newest_first[:2]
        assert await _ids(superuser_client, limit=2, before_id=first[-1]) == newest_first[2:4]

    async def test_says_when_it_was_archived(self, superuser_client, db, ai_provider):
        session = await _session(db, ai_provider, archived=True)
        await db.commit()
        body = (await superuser_client.get(f"/api/ai/sessions/{session.id}")).json()
        assert body["archived_at"] is not None


class TestArchiving:
    async def test_by_id(self, superuser_client, db, ai_provider):
        done = await _session(db, ai_provider)
        live = await _session(db, ai_provider, status="running")
        await db.commit()

        resp = await superuser_client.post(
            "/api/ai/sessions/archive", json={"session_ids": [done.id, live.id, 999999]}
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["done"] == [done.id]
        skipped = {s["session_id"]: s["reason"] for s in body["skipped"]}
        assert "running" in skipped[live.id]
        assert skipped[999999] == "Not found."
        await db.refresh(done)
        await db.refresh(live)
        assert done.archived_at is not None and live.archived_at is None

    async def test_everything_finished_before_a_date(self, superuser_client, db, ai_provider):
        old = await _session(db, ai_provider, ended=timedelta(days=10))
        recent = await _session(db, ai_provider, ended=timedelta(days=1))
        live = await _session(db, ai_provider, status="running", ended=timedelta(days=10))
        await db.commit()

        resp = await superuser_client.post(
            "/api/ai/sessions/archive",
            json={"finished_before": (NOW - timedelta(days=5)).isoformat()},
        )

        assert old.id in resp.json()["done"]
        assert recent.id not in resp.json()["done"] and live.id not in resp.json()["done"]

    async def test_of_one_mode(self, superuser_client, db, ai_provider):
        alert = await _session(db, ai_provider, mode="alert_investigation")
        chat = await _session(db, ai_provider)
        await db.commit()

        resp = await superuser_client.post(
            "/api/ai/sessions/archive",
            json={"finished_before": NOW.isoformat(), "mode": "alert_investigation"},
        )

        assert alert.id in resp.json()["done"] and chat.id not in resp.json()["done"]

    async def test_needs_something_to_archive(self, superuser_client):
        resp = await superuser_client.post("/api/ai/sessions/archive", json={})
        assert resp.status_code == 422

    async def test_audited(self, superuser_client, db, ai_provider):
        session = await _session(db, ai_provider)
        await db.commit()
        await superuser_client.post("/api/ai/sessions/archive", json={"session_ids": [session.id]})
        row = (
            await db.execute(select(AuditLog).where(AuditLog.action == "ai_sessions_archived"))
        ).scalar_one()
        assert row.after_state["session_ids"] == [session.id]

    async def test_and_back(self, superuser_client, db, ai_provider):
        session = await _session(db, ai_provider, archived=True)
        await db.commit()

        resp = await superuser_client.post(
            "/api/ai/sessions/unarchive", json={"session_ids": [session.id]}
        )

        assert resp.json()["done"] == [session.id]
        assert session.id in await _ids(superuser_client)

    async def test_keeps_what_the_safeguards_count(self, superuser_client, db, ai_provider):
        """The reason archiving is the everyday tool: the daily cap still
        sees an archived fix."""
        from app.ai.alert_autonomy import _sessions_that_changed
        from tests.conftest import create_host

        host = await create_host(db)
        session = await _session(
            db,
            ai_provider,
            mode="alert_investigation",
            autonomy="full_auto",
            ended=timedelta(hours=1),
        )
        db.add(
            AIToolCall(
                session_id=session.id,
                tool_name="run_ssh_command",
                classification="mutating",
                status="executed",
                target_host_id=host.id,
                started_at=NOW - timedelta(hours=1),
            )
        )
        await db.commit()
        before = await _sessions_that_changed(db, host.id, NOW - timedelta(hours=24))

        await superuser_client.post("/api/ai/sessions/archive", json={"session_ids": [session.id]})

        assert before == 1
        assert await _sessions_that_changed(db, host.id, NOW - timedelta(hours=24)) == 1


class TestDeletingInBulk:
    async def _delete(self, client, *ids):
        resp = await client.post("/api/ai/sessions/delete", json={"session_ids": list(ids)})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        return body["done"], {s["session_id"]: s["reason"] for s in body["skipped"]}

    async def test_deletes_and_skips(self, superuser_client, db, ai_provider):
        plain = await _session(db, ai_provider, archived=True)
        live = await _session(db, ai_provider, status="waiting_approval")
        await db.commit()

        done, skipped = await self._delete(superuser_client, plain.id, live.id)

        assert done == [plain.id]
        assert "waiting_approval" in skipped[live.id]
        assert await db.get(AISession, plain.id) is None

    async def test_one_audit_row_each(self, superuser_client, db, ai_provider):
        a, b = await _session(db, ai_provider), await _session(db, ai_provider)
        await db.commit()
        await self._delete(superuser_client, a.id, b.id)
        rows = (
            await db.execute(
                select(AuditLog.entity_id).where(AuditLog.action == "ai_session_deleted")
            )
        ).scalars()
        assert {a.id, b.id} <= set(rows)

    async def test_skips_a_fix_the_safeguards_still_count(self, superuser_client, db, ai_provider):
        fix = await _session(
            db,
            ai_provider,
            mode="alert_investigation",
            autonomy="full_auto",
            ended=timedelta(hours=2),
        )
        await _changed_a_host(db, fix)
        await db.commit()

        done, skipped = await self._delete(superuser_client, fix.id)

        assert done == []
        assert "safeguards" in skipped[fix.id] and "Archive it instead" in skipped[fix.id]

    async def test_deletes_it_once_they_no_longer_do(self, superuser_client, db, ai_provider):
        fix = await _session(
            db,
            ai_provider,
            mode="alert_investigation",
            autonomy="full_auto",
            ended=timedelta(hours=25),
        )
        await _changed_a_host(db, fix)
        await db.commit()

        done, _ = await self._delete(superuser_client, fix.id)
        assert done == [fix.id]

    async def test_a_longer_cooldown_holds_it_longer(self, superuser_client, db, ai_provider):
        await _set(db, "ai.alert_remediation_cooldown_minutes", str(3 * 24 * 60))
        fix = await _session(
            db,
            ai_provider,
            mode="alert_investigation",
            autonomy="full_auto",
            ended=timedelta(days=2),
        )
        await _changed_a_host(db, fix)
        await db.commit()

        done, skipped = await self._delete(superuser_client, fix.id)
        assert done == [] and fix.id in skipped

    async def test_a_fix_that_changed_nothing_is_not_held(self, superuser_client, db, ai_provider):
        session = await _session(
            db,
            ai_provider,
            mode="alert_investigation",
            autonomy="full_auto",
            ended=timedelta(hours=1),
        )
        await db.commit()
        done, _ = await self._delete(superuser_client, session.id)
        assert done == [session.id]

    async def test_skips_a_session_whose_snapshot_is_kept(self, superuser_client, db, ai_provider):
        session = await _session(db, ai_provider, autonomy="approval")
        await _changed_a_host(db, session, snapshot="labdog-ai-7-1")
        await db.commit()

        done, skipped = await self._delete(superuser_client, session.id)

        assert done == []
        assert "labdog-ai-7-1" in skipped[session.id]


class TestTheSingleDelete:
    async def test_refuses_a_fix_the_safeguards_still_count(
        self, superuser_client, db, ai_provider
    ):
        fix = await _session(
            db,
            ai_provider,
            mode="alert_investigation",
            autonomy="full_auto",
            ended=timedelta(hours=2),
        )
        await _changed_a_host(db, fix)
        await db.commit()

        resp = await superuser_client.delete(f"/api/ai/sessions/{fix.id}")

        assert resp.status_code == 409
        assert "safeguards" in resp.json()["detail"]
        assert await db.get(AISession, fix.id) is not None

    async def test_still_leaves_a_snapshot_behind_when_asked(
        self, superuser_client, db, ai_provider
    ):
        session = await _session(db, ai_provider, autonomy="approval")
        await _changed_a_host(db, session, snapshot="labdog-ai-7-2")
        await db.commit()

        resp = await superuser_client.delete(f"/api/ai/sessions/{session.id}")

        assert resp.status_code == 204
        row = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.action == "ai_session_deleted", AuditLog.entity_id == session.id
                )
            )
        ).scalar_one()
        assert row.before_state["snapshots_left_behind"] == ["labdog-ai-7-2"]


class TestArchivingOnASchedule:
    @pytest.fixture(autouse=True)
    def _task_db(self, db):
        @asynccontextmanager
        async def _cm():
            yield db

        with patch("app.db.task_session", new=_cm):
            yield

    async def test_off_by_default(self, db, ai_provider):
        from app.tasks.ai_sessions import _archive_old_sessions

        session = await _session(db, ai_provider, ended=timedelta(days=400))
        assert (await _archive_old_sessions())["archived"] == 0
        await db.refresh(session)
        assert session.archived_at is None

    async def test_archives_finished_sessions_past_the_window(self, db, ai_provider):
        from app.tasks.ai_sessions import _archive_old_sessions

        await _set(db, "ai.session_archive_after_days", "30")
        old = await _session(db, ai_provider, ended=timedelta(days=31))
        recent = await _session(db, ai_provider, ended=timedelta(days=29))
        live = await _session(db, ai_provider, status="queued", ended=timedelta(days=31))

        result = await _archive_old_sessions()

        assert result == {"archived": 1, "days": 30}
        for session in (old, recent, live):
            await db.refresh(session)
        assert old.archived_at is not None
        assert recent.archived_at is None and live.archived_at is None
