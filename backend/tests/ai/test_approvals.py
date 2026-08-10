"""Pausing on a change, and picking up again.

The rules that matter are the ones that stop an approval being a
formality: what the operator approved is what runs, it runs once, a
denylisted command can never become approvable, and a request nobody
answers does not park the session forever.

The transcript-shape assertions look incidental and are not. Both wire
formats reject a tool call with no matching result, so a session parked
with a dangling call could not be replayed on resume — and would fail at
the provider, presenting as a backend outage rather than as a bug here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.ai import approvals
from app.ai.gate import decide
from app.ai.models import AIApprovalRequest, AIMessage, AISession, AIToolCall
from app.ai.providers.base import ToolCall
from app.ai.safety import classify_command
from app.ai.tools import TOOL_REGISTRY
from app.models.audit_log import AuditLog
from tests.ai.fake_provider import FakeProvider, ScriptedTurn
from tests.conftest import create_host, create_ssh_key

RESTART = "systemctl restart nginx"
READ = "journalctl -u nginx -n 20"


async def _host(db):
    key = await create_ssh_key(db)
    return await create_host(db, ssh_key_id=key.id)


async def _parked_session(db, make_session, host):
    """A session parked on a restart, as the runner would leave it."""
    session = await make_session("Fix nginx.", autonomy_level="approval", target_host_ids=[host.id])
    approval = await approvals.park(
        db,
        session,
        tool_name="run_ssh_command",
        arguments={"host_id": host.id, "command": RESTART, "purpose": "nginx is down"},
        verdict=classify_command(RESTART),
    )
    await db.commit()
    return session, approval


class TestParking:
    async def test_it_records_what_was_asked_for(self, db, make_session) -> None:
        host = await _host(db)
        _, approval = await _parked_session(db, make_session, host)

        assert approval.status == "pending"
        assert approval.command_preview == RESTART
        assert approval.target_host_id == host.id
        assert approval.classification == "mutating"
        assert approval.summary == "nginx is down"

    async def test_the_session_is_parked_not_finished(self, db, make_session) -> None:
        """No report and no finished_at: the investigation is not over,
        it is waiting on a person."""
        host = await _host(db)
        session, _ = await _parked_session(db, make_session, host)

        assert session.status == "waiting_approval"
        assert session.report_markdown is None
        assert session.finished_at is None

    async def test_it_expires(self, db, make_session) -> None:
        host = await _host(db)
        _, approval = await _parked_session(db, make_session, host)
        assert approval.expires_at is not None
        assert approval.expires_at > datetime.now(UTC)

    async def test_one_tool_call_row_not_two(self, db, make_session) -> None:
        """The runner writes the row before it knows the verdict. A second
        one here would show the operator the same proposed command twice."""
        host = await _host(db)
        session = await make_session(
            "Fix nginx.", autonomy_level="approval", target_host_ids=[host.id]
        )
        record = AIToolCall(
            session_id=session.id,
            tool_name="run_ssh_command",
            arguments={"host_id": host.id, "command": RESTART},
            classification="mutating",
            status="proposed",
        )
        db.add(record)
        await db.flush()

        await approvals.park(
            db,
            session,
            tool_name="run_ssh_command",
            arguments={"host_id": host.id, "command": RESTART},
            verdict=classify_command(RESTART),
            record=record,
        )
        await db.commit()

        rows = (
            (await db.execute(select(AIToolCall).where(AIToolCall.session_id == session.id)))
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].approval_id is not None
        assert rows[0].status == "proposed", "deferred is not the same as blocked"


class TestTheGateDecidesWhatCanPark:
    def test_a_denylisted_command_never_parks(self) -> None:
        """No amount of clicking approve should be able to run this, so it
        must never reach a human as a question."""
        d = decide(
            "run_ssh_command",
            {"host_id": 1, "command": "rm -rf /"},
            permitted=dict(TOOL_REGISTRY),
            autonomy_level="approval",
        )
        assert d.needs_approval is False

    def test_a_read_only_session_refuses_rather_than_asking(self) -> None:
        """read_only means no, not 'ask me'. Turning it into a prompt would
        quietly convert the safest level into the middle one."""
        d = decide(
            "run_ssh_command",
            {"host_id": 1, "command": RESTART},
            permitted=dict(TOOL_REGISTRY),
            autonomy_level="read_only",
        )
        assert d.needs_approval is False
        assert d.allowed is False


class TestTheLoopParksMidTurn:
    """Exercised through the real loop, because the thing being checked is
    what the loop leaves behind, not what ``park`` returns."""

    async def test_the_run_stops_and_the_session_parks(
        self, db, make_session, ai_provider, small_caps
    ) -> None:
        from app.ai.loop import AgentLoop

        host = await _host(db)
        session = await make_session(
            "Fix nginx.", autonomy_level="approval", target_host_ids=[host.id]
        )
        provider = FakeProvider(
            [
                ScriptedTurn(
                    tool_calls=[
                        ToolCall(
                            id="c1",
                            name="run_ssh_command",
                            arguments={"host_id": host.id, "command": RESTART},
                        )
                    ]
                ),
                ScriptedTurn(text="should never be reached"),
            ]
        )
        outcome = await AgentLoop(db, session, ai_provider, small_caps, provider=provider).run()

        assert outcome.status == "waiting_approval"
        assert session.status == "waiting_approval"
        assert len(provider.calls) == 1, "the loop must not take another turn while parked"

    async def test_every_call_in_the_turn_gets_a_result(
        self, db, make_session, ai_provider, small_caps
    ) -> None:
        """The load-bearing one. A dangling tool call cannot be replayed,
        and the failure would surface at the provider on resume."""
        from app.ai.loop import AgentLoop

        host = await _host(db)
        session = await make_session(
            "Fix nginx.", autonomy_level="approval", target_host_ids=[host.id]
        )
        calls = [
            ToolCall(
                id="c1", name="run_ssh_command", arguments={"host_id": host.id, "command": RESTART}
            ),
            ToolCall(
                id="c2", name="run_ssh_command", arguments={"host_id": host.id, "command": READ}
            ),
        ]
        provider = FakeProvider([ScriptedTurn(tool_calls=calls)])
        await AgentLoop(db, session, ai_provider, small_caps, provider=provider).run()

        results = (
            (
                await db.execute(
                    select(AIMessage).where(
                        AIMessage.session_id == session.id, AIMessage.role == "tool"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert {m.tool_call_id for m in results} == {"c1", "c2"}

    async def test_the_second_command_is_not_run(
        self, db, make_session, ai_provider, small_caps
    ) -> None:
        """A result for it exists, but nothing touched the host: parking
        stops the turn, it does not race the rest of it."""
        from app.ai.loop import AgentLoop

        host = await _host(db)
        session = await make_session(
            "Fix nginx.", autonomy_level="approval", target_host_ids=[host.id]
        )
        provider = FakeProvider(
            [
                ScriptedTurn(
                    tool_calls=[
                        ToolCall(
                            id="c1",
                            name="run_ssh_command",
                            arguments={"host_id": host.id, "command": RESTART},
                        ),
                        ToolCall(
                            id="c2",
                            name="run_ssh_command",
                            arguments={"host_id": host.id, "command": READ},
                        ),
                    ]
                )
            ]
        )
        await AgentLoop(db, session, ai_provider, small_caps, provider=provider).run()

        skipped = (
            (
                await db.execute(
                    select(AIMessage).where(
                        AIMessage.session_id == session.id, AIMessage.tool_call_id == "c2"
                    )
                )
            )
            .scalars()
            .one()
        )
        assert skipped.content == approvals.SKIPPED_RESULT
        assert session.command_count == 0


class TestDecidingIt:
    async def test_approving_dispatches_a_resume(self, superuser_client, db, make_session) -> None:
        host = await _host(db)
        _, approval = await _parked_session(db, make_session, host)

        with patch("app.tasks.celery_app.send_task") as send:
            resp = await superuser_client.post(
                f"/api/ai/approvals/{approval.id}", json={"approve": True}
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"
        assert send.call_args.args[0] == "app.tasks.ai_task.resume_session"

    async def test_rejecting_records_the_note(self, superuser_client, db, make_session) -> None:
        host = await _host(db)
        _, approval = await _parked_session(db, make_session, host)

        with patch("app.tasks.celery_app.send_task"):
            resp = await superuser_client.post(
                f"/api/ai/approvals/{approval.id}",
                json={"approve": False, "note": "I will do it by hand."},
            )

        assert resp.json()["status"] == "rejected"
        assert resp.json()["decision_note"] == "I will do it by hand."

    async def test_both_decisions_are_audited(self, superuser_client, db, make_session) -> None:
        """A human authorising a change to a host is exactly what the audit
        trail is for, and a rejection is the record that someone looked."""
        host = await _host(db)
        _, approval = await _parked_session(db, make_session, host)

        with patch("app.tasks.celery_app.send_task"):
            await superuser_client.post(f"/api/ai/approvals/{approval.id}", json={"approve": True})

        rows = (
            (await db.execute(select(AuditLog).where(AuditLog.action == "ai_approval_approved")))
            .scalars()
            .all()
        )
        assert rows and rows[-1].after_state["command"] == RESTART

    async def test_deciding_twice_is_refused(self, superuser_client, db, make_session) -> None:
        """The first decision may already have run a command. A second
        request returning 200 would leave the operator believing their
        latest click was the one that counted."""
        host = await _host(db)
        _, approval = await _parked_session(db, make_session, host)

        with patch("app.tasks.celery_app.send_task"):
            first = await superuser_client.post(
                f"/api/ai/approvals/{approval.id}", json={"approve": True}
            )
            second = await superuser_client.post(
                f"/api/ai/approvals/{approval.id}", json={"approve": False}
            )

        assert first.status_code == 200
        assert second.status_code == 409

    async def test_an_unknown_request_is_404(self, superuser_client) -> None:
        resp = await superuser_client.post("/api/ai/approvals/999999", json={"approve": True})
        assert resp.status_code == 404

    async def test_cancelling_the_session_lapses_the_request(
        self, superuser_client, db, make_session
    ) -> None:
        """Otherwise it sits in the queue forever inviting a click that
        silently does nothing."""
        host = await _host(db)
        session, approval = await _parked_session(db, make_session, host)

        await superuser_client.post(f"/api/ai/sessions/{session.id}/cancel")
        await db.refresh(approval)

        assert approval.status == "expired"
        assert approval.decided_by_user_id is None, "nobody weighed this and said no"

    async def test_it_cannot_be_decided_after_cancellation(
        self, superuser_client, db, make_session
    ) -> None:
        host = await _host(db)
        session, approval = await _parked_session(db, make_session, host)
        await superuser_client.post(f"/api/ai/sessions/{session.id}/cancel")

        resp = await superuser_client.post(
            f"/api/ai/approvals/{approval.id}", json={"approve": True}
        )
        assert resp.status_code == 409


class TestTheQueue:
    async def test_pending_requests_are_listed(self, superuser_client, db, make_session) -> None:
        """A parked scheduled run is invisible otherwise — nothing else
        tells an operator that something stopped overnight for them."""
        host = await _host(db)
        _, approval = await _parked_session(db, make_session, host)

        resp = await superuser_client.get("/api/ai/approvals")
        assert resp.status_code == 200
        assert approval.id in [a["id"] for a in resp.json()]

    async def test_a_decided_request_leaves_the_queue(
        self, superuser_client, db, make_session
    ) -> None:
        host = await _host(db)
        _, approval = await _parked_session(db, make_session, host)
        with patch("app.tasks.celery_app.send_task"):
            await superuser_client.post(f"/api/ai/approvals/{approval.id}", json={"approve": True})

        resp = await superuser_client.get("/api/ai/approvals")
        assert approval.id not in [a["id"] for a in resp.json()]

    async def test_the_session_detail_carries_them(
        self, superuser_client, db, make_session
    ) -> None:
        host = await _host(db)
        session, approval = await _parked_session(db, make_session, host)

        resp = await superuser_client.get(f"/api/ai/sessions/{session.id}")
        assert [a["id"] for a in resp.json()["approvals"]] == [approval.id]


class TestExecutingWhatWasApproved:
    async def test_it_runs_the_stored_arguments(self, db, make_session) -> None:
        """Not whatever the model asks for next. Approval is for these
        arguments, not for the intent behind them."""
        host = await _host(db)
        session, approval = await _parked_session(db, make_session, host)
        approval.status = "approved"

        seen: dict = {}

        async def fake_run(ctx, args):
            from app.ai.tools import ToolResult

            seen.update(args)
            seen["preapproved"] = ctx.preapproved
            return ToolResult("exit status: 0", ok=True, target_host_id=args["host_id"])

        with patch.object(TOOL_REGISTRY["run_ssh_command"], "run", fake_run):
            result = await approvals.execute_approved(db, session, approval)

        assert result.ok is True
        assert seen["command"] == RESTART
        assert seen["preapproved"] is True

    async def test_the_tool_call_row_becomes_executed(self, db, make_session) -> None:
        host = await _host(db)
        session, approval = await _parked_session(db, make_session, host)
        approval.status = "approved"

        async def fake_run(ctx, args):
            from app.ai.tools import ToolResult

            return ToolResult("exit status: 0", ok=True, target_host_id=args["host_id"])

        with patch.object(TOOL_REGISTRY["run_ssh_command"], "run", fake_run):
            await approvals.execute_approved(db, session, approval)

        record = (
            (await db.execute(select(AIToolCall).where(AIToolCall.approval_id == approval.id)))
            .scalars()
            .one()
        )
        assert record.status == "executed"
        assert session.command_count == 1

    async def test_a_failed_snapshot_stops_the_command(self, db, make_session) -> None:
        """The operator approved a change they could expect to be able to
        undo. Running it without a rollback point is a different, riskier
        change than the one they agreed to."""
        from app.ai.snapshots import SnapshotFailed

        host = await _host(db)
        session, approval = await _parked_session(db, make_session, host)
        approval.status = "approved"

        ran = False

        async def fake_run(ctx, args):  # pragma: no cover - must not be reached
            nonlocal ran
            ran = True
            raise AssertionError("the command ran without a snapshot")

        async def boom(*a, **kw):
            raise SnapshotFailed("Proxmox is unreachable")

        with (
            patch("app.ai.approvals.snapshot_before_change", boom),
            patch.object(TOOL_REGISTRY["run_ssh_command"], "run", fake_run),
        ):
            result = await approvals.execute_approved(db, session, approval)

        assert ran is False
        assert result.ok is False
        assert "Proxmox is unreachable" in result.content


class TestTheResumePrompt:
    def _approval(self, status: str, note: str | None = None) -> AIApprovalRequest:
        return AIApprovalRequest(
            session_id=1,
            tool_name="run_ssh_command",
            arguments={"host_id": 3, "command": RESTART},
            target_host_id=3,
            command_preview=RESTART,
            classification="mutating",
            reason="systemctl restart changes system state",
            status=status,
            decision_note=note,
        )

    def test_approved_says_labdog_ran_it(self) -> None:
        """Phrased as narration because that is what it is. Feeding it back
        as a tool result would misrepresent who acted, in the one place
        where who acted is the whole point."""
        from app.ai.tools import ToolResult

        text = approvals.resume_prompt(
            self._approval("approved"), ToolResult("exit status: 0", ok=True)
        )
        assert "LabDog ran it" in text
        assert "exit status: 0" in text

    def test_rejected_carries_the_reason(self) -> None:
        text = approvals.resume_prompt(self._approval("rejected", "I will do it by hand."), None)
        assert "rejected" in text
        assert "I will do it by hand." in text

    @pytest.mark.parametrize("status", ["rejected", "expired"])
    def test_a_refusal_forbids_a_workaround(self, status: str) -> None:
        """Without this the model treats the refusal as an obstacle to
        route around, which is exactly what an approval gate is for."""
        text = approvals.resume_prompt(self._approval(status), None)
        assert "another way to make the same change" in text

    def test_expired_does_not_claim_anyone_decided(self) -> None:
        text = approvals.resume_prompt(self._approval("expired"), None)
        assert "Nobody decided" in text


class TestPreviewing:
    def test_an_ssh_call_previews_as_its_command(self) -> None:
        assert approvals.command_preview("run_ssh_command", {"command": RESTART}) == RESTART

    def test_another_tool_previews_as_a_call(self) -> None:
        preview = approvals.command_preview("query_loki", {"query": '{job="x"}', "limit": 10})
        assert preview.startswith("query_loki(")
        assert "limit=10" in preview

    def test_the_models_stated_purpose_is_not_part_of_the_command(self) -> None:
        """It is advisory text, and putting it in the preview would blur
        what is actually going to run."""
        preview = approvals.command_preview("query_loki", {"query": "x", "purpose": "because"})
        assert "because" not in preview


class TestExpiry:
    async def test_a_lapsed_request_expires_and_resumes_its_session(self, db, make_session) -> None:
        """Expiring is bookkeeping; resuming is what turns a dead run into
        a finished one with a report someone can read."""
        from app.tasks.ai_approvals import _expire_stale_approvals

        host = await _host(db)
        session, approval = await _parked_session(db, make_session, host)
        approval.expires_at = datetime.now(UTC) - timedelta(hours=1)
        await db.commit()

        with (
            patch("app.db.task_session", _session_cm(db)),
            patch("app.tasks.ai_approvals.celery_app.send_task") as send,
        ):
            result = await _expire_stale_approvals()

        await db.refresh(approval)
        assert result["expired"] == 1
        assert approval.status == "expired"
        assert approval.decided_by_user_id is None
        assert send.call_args.kwargs["kwargs"] == {"session_id": session.id}

    async def test_a_live_request_is_left_alone(self, db, make_session) -> None:
        from app.tasks.ai_approvals import _expire_stale_approvals

        host = await _host(db)
        _, approval = await _parked_session(db, make_session, host)

        with (
            patch("app.db.task_session", _session_cm(db)),
            patch("app.tasks.ai_approvals.celery_app.send_task"),
        ):
            result = await _expire_stale_approvals()

        await db.refresh(approval)
        assert result["expired"] == 0
        assert approval.status == "pending"


def _session_cm(db):
    """Hand the task the test's own session instead of opening a new one."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _cm():
        yield db

    return _cm


class TestSessionsWithApprovalsStillDelete:
    async def test_deleting_a_session_takes_its_requests(
        self, superuser_client, db, make_session
    ) -> None:
        """ON DELETE CASCADE. A request whose session is gone would sit in
        the queue pointing at nothing."""
        host = await _host(db)
        session, approval = await _parked_session(db, make_session, host)
        session.status = "cancelled"
        await db.commit()

        resp = await superuser_client.delete(f"/api/ai/sessions/{session.id}")
        assert resp.status_code == 204

        remaining = await db.execute(
            select(AIApprovalRequest).where(AIApprovalRequest.id == approval.id)
        )
        assert remaining.scalar_one_or_none() is None


class TestParkedSessionsAreNotDeletable:
    async def test_delete_is_refused_while_parked(self, superuser_client, db, make_session) -> None:
        host = await _host(db)
        session, _ = await _parked_session(db, make_session, host)

        resp = await superuser_client.delete(f"/api/ai/sessions/{session.id}")
        assert resp.status_code == 409
        assert (await db.get(AISession, session.id)) is not None
