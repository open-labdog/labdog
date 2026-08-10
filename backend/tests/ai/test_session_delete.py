"""Deleting a session.

The session list had no way to remove anything, so a run that failed for
a reason since fixed sat there permanently. Deleting is housekeeping, and
the rules that matter are the ones that stop it being destructive: a live
session belongs to a task that is still writing to it, spend must survive
the transcript, and a deleted investigation should not become invisible.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.ai.models import AIMessage, AISession, AIToolCall, AIUsageDay
from app.models.audit_log import AuditLog


async def _session(db, ai_provider, *, status: str = "succeeded") -> AISession:
    session = AISession(
        provider_id=ai_provider.id,
        mode="chat",
        mission="Check nginx.",
        autonomy_level="read_only",
        status=status,
        target_host_ids=[1, 2],
    )
    db.add(session)
    await db.flush()
    db.add(AIMessage(session_id=session.id, seq=0, role="user", content="Check nginx."))
    db.add(
        AIToolCall(
            session_id=session.id,
            tool_name="run_ssh_command",
            classification="read_only",
            status="executed",
        )
    )
    await db.flush()
    return session


class TestDeletingAFinishedSession:
    async def test_it_is_removed(self, superuser_client, db, ai_provider) -> None:
        session = await _session(db, ai_provider)
        resp = await superuser_client.delete(f"/api/ai/sessions/{session.id}")

        assert resp.status_code == 204
        remaining = await db.execute(select(AISession).where(AISession.id == session.id))
        assert remaining.scalar_one_or_none() is None

    async def test_the_transcript_goes_with_it(self, superuser_client, db, ai_provider) -> None:
        """ON DELETE CASCADE on both child tables. A session whose messages
        outlived it would leave rows nothing can reach."""
        session = await _session(db, ai_provider)
        await superuser_client.delete(f"/api/ai/sessions/{session.id}")

        messages = await db.execute(select(AIMessage).where(AIMessage.session_id == session.id))
        calls = await db.execute(select(AIToolCall).where(AIToolCall.session_id == session.id))
        assert messages.scalars().all() == []
        assert calls.scalars().all() == []

    async def test_it_is_recorded_in_the_audit_log(self, superuser_client, db, ai_provider) -> None:
        """The transcript is about to be unrecoverable, so what it was has
        to survive somewhere."""
        session = await _session(db, ai_provider)
        await superuser_client.delete(f"/api/ai/sessions/{session.id}")

        rows = await db.execute(select(AuditLog).where(AuditLog.action == "ai_session_deleted"))
        entry = rows.scalars().all()[-1]
        assert entry.entity_id == session.id
        assert "Check nginx." in (entry.before_state or {}).get("mission", "")

    async def test_recorded_spend_survives(self, superuser_client, db, ai_provider) -> None:
        """The ledger is keyed by date and provider, not by session,
        precisely so housekeeping cannot rewrite what was spent."""
        from app.ai import service

        await service.record_usage(
            db, provider_id=ai_provider.id, prompt_tokens=100, completion_tokens=10, cost=1.25
        )
        session = await _session(db, ai_provider)
        await db.commit()

        await superuser_client.delete(f"/api/ai/sessions/{session.id}")

        ledger = await db.execute(
            select(AIUsageDay).where(AIUsageDay.provider_id == ai_provider.id)
        )
        rows = ledger.scalars().all()
        assert rows, "deleting a session must not erase the spend it caused"
        assert sum(r.cost for r in rows) == pytest.approx(1.25)


class TestDeletingIsRefusedWhileLive:
    """The row is owned by a running Celery task. Deleting it out from
    under one turns a working run into a confusing crash."""

    @pytest.mark.parametrize("status", ["queued", "running", "waiting_approval"])
    async def test_a_live_session_is_refused(
        self, superuser_client, db, ai_provider, status: str
    ) -> None:
        session = await _session(db, ai_provider, status=status)
        resp = await superuser_client.delete(f"/api/ai/sessions/{session.id}")

        assert resp.status_code == 409
        assert "Cancel it" in resp.json()["detail"]

        survivor = await db.execute(select(AISession).where(AISession.id == session.id))
        assert survivor.scalar_one_or_none() is not None

    @pytest.mark.parametrize("status", ["succeeded", "failed", "cancelled"])
    async def test_a_finished_session_is_allowed(
        self, superuser_client, db, ai_provider, status: str
    ) -> None:
        session = await _session(db, ai_provider, status=status)
        resp = await superuser_client.delete(f"/api/ai/sessions/{session.id}")
        assert resp.status_code == 204

    async def test_a_cancelled_run_can_then_be_deleted(
        self, superuser_client, db, ai_provider
    ) -> None:
        """The documented way out of the refusal above, end to end."""
        session = await _session(db, ai_provider, status="running")
        assert (await superuser_client.delete(f"/api/ai/sessions/{session.id}")).status_code == 409

        await superuser_client.post(f"/api/ai/sessions/{session.id}/cancel")
        assert (await superuser_client.delete(f"/api/ai/sessions/{session.id}")).status_code == 204


class TestMissing:
    async def test_an_unknown_session_is_404(self, superuser_client) -> None:
        resp = await superuser_client.delete("/api/ai/sessions/999999")
        assert resp.status_code == 404
