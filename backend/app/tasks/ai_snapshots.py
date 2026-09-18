"""Expiring the rollback points the agent left behind.

Every change the agent makes to a VM-mapped host takes a Proxmox snapshot
first, and nothing removed them. Verified on a live instance: two
accumulated on one VM inside ten minutes of testing, and every subsequent
change would have added another until the datastore filled.

**Immediate cleanup on success would be the wrong fix**, even though it is
what the action-run path does. That path deletes its snapshot because a
verify step has just declared the change good. An AI session has no verify
step, and the snapshot exists precisely so a person can undo the agent's
work *after* reading what it did — deleting it the moment the session
succeeds throws away the thing it was taken for. So this is a retention
window, not a cleanup step, and `ai.snapshot_retention_days` is how long
"afterwards" lasts.

The sweep only ever touches snapshots recorded on an ``AIToolCall`` row.
It does not list what is on the hypervisor and match by name: the action
runs use the same machinery, an operator may have taken their own
snapshots, and a sweep that deleted by pattern would eventually delete
someone else's rollback point. Working from our own rows means the worst
case is a snapshot we forget about, not one we destroy.

That worst case is real and bounded: deleting a session cascades its tool
calls away, so any snapshot it took becomes an orphan this sweep can no
longer see. The delete endpoint records the names in its audit entry for
exactly that reason, and the ``labdog-ai-`` prefix makes orphans
identifiable in the Proxmox UI.
"""

from __future__ import annotations

import asyncio
import logging

from app.tasks import celery_app

logger = logging.getLogger(__name__)

#: Once a day. The window is measured in days, so a finer sweep would only
#: add Proxmox API calls that find nothing.
SWEEP_INTERVAL_SECONDS = 86_400


@celery_app.task(
    name="app.tasks.ai_snapshots.prune_ai_snapshots",
    queue="default",
)
def prune_ai_snapshots() -> dict:
    """Delete AI-taken snapshots older than the retention window."""
    return asyncio.run(_prune_ai_snapshots())


async def _prune_ai_snapshots() -> dict:
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    from sqlalchemy import select  # noqa: PLC0415

    from app.ai.models import AIToolCall  # noqa: PLC0415
    from app.ai.snapshots import SnapshotFailed, resolve_target  # noqa: PLC0415
    from app.db import task_session  # noqa: PLC0415
    from app.settings_service import get_setting_typed  # noqa: PLC0415
    from app.workflows.steps.cleanup import delete_snapshot  # noqa: PLC0415

    deleted = 0
    failed = 0

    async with task_session() as db:
        days = int(await get_setting_typed("ai.snapshot_retention_days", db))
        if days <= 0:
            # 0 means keep forever. Deliberately a no-op rather than a
            # sweep with an infinite window, so the "off" setting cannot
            # be defeated by a rounding error somewhere in the date maths.
            return {"deleted": 0, "failed": 0, "retention_days": 0}

        cutoff = datetime.now(UTC) - timedelta(days=days)
        stale = (
            (
                await db.execute(
                    select(AIToolCall).where(
                        AIToolCall.snapshot_name.is_not(None),
                        AIToolCall.snapshot_pruned_at.is_(None),
                        AIToolCall.started_at < cutoff,
                    )
                )
            )
            .scalars()
            .all()
        )

        for call in stale:
            if call.target_host_id is None:
                # Nothing to resolve a hypervisor from. Mark it so the
                # sweep does not retry forever; the name survives on the
                # row for anyone cleaning up by hand.
                call.snapshot_pruned_at = datetime.now(UTC)
                continue
            try:
                target = await resolve_target(db, call.target_host_id)
            except SnapshotFailed as exc:
                logger.warning(
                    "ai_snapshots: cannot resolve host %s for snapshot %r: %s",
                    call.target_host_id,
                    call.snapshot_name,
                    exc,
                )
                failed += 1
                continue
            if target is None:
                # The host lost its VM mapping since the snapshot was
                # taken. There is no longer anything to delete it through.
                call.snapshot_pruned_at = datetime.now(UTC)
                continue

            try:
                await delete_snapshot(
                    target.client,
                    target.pve_node,
                    target.vmid,
                    call.snapshot_name,
                    vm_type=target.vm_type,
                )
            except Exception as exc:
                # Left unmarked so the next sweep retries. A snapshot that
                # is already gone raises here too, and retrying that is
                # cheap — far cheaper than the alternative failure, which
                # is silently forgetting a rollback point still occupying
                # the datastore.
                logger.warning(
                    "ai_snapshots: could not delete %r on vmid %s: %s",
                    call.snapshot_name,
                    target.vmid,
                    exc,
                )
                failed += 1
                continue

            # The name is kept. It is the record of what protected this
            # change, and "there was a rollback point, it has expired" is
            # a different thing for a reader to know than "there never
            # was one".
            call.snapshot_pruned_at = datetime.now(UTC)
            deleted += 1

        await db.commit()

    if deleted or failed:
        logger.info(
            "ai_snapshots: deleted %d snapshot(s), %d failed, retention %d days",
            deleted,
            failed,
            days,
        )
    return {"deleted": deleted, "failed": failed, "retention_days": days}


# ---------------------------------------------------------------------------
# RedBeat registration
# ---------------------------------------------------------------------------


def _register_beat_schedules() -> None:
    from app.tasks.beat_registry import ensure_entry

    ensure_entry(
        name="prune-ai-snapshots",
        task="app.tasks.ai_snapshots.prune_ai_snapshots",
        run_every_seconds=SWEEP_INTERVAL_SECONDS,
        app=celery_app,
    )


# Registration happens from ``beat_init`` (app.tasks.beat_registry), not at
# import. Calling it here rewrote the entry's ``due_at`` in every process
# that imported this module — API included — so on a deployment that
# restarts more than once a day, a daily job never fired at all (BUG-70).
