"""Point-in-time reads behind the Overview tabs.

These answer "what needs a look right now" rather than "how did it trend",
so they live beside the chart service (``app.metrics.service``) instead of
in it. Each reuses a definition the Prometheus exporter already exports
(``app.metrics.aggregates``) so the two can't disagree about what counts
as stale or which certificates are live — but returns rows the UI can
name and link, where the exporter only needs counts.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ca_certs.models import CACertRule, CertState
from app.metrics.aggregates import get_host_module_counts
from app.metrics.schemas import (
    CACertExpiry,
    ModuleStatusCount,
    StuckOperation,
    StuckOperations,
)
from app.models.action_run import ActionHostRun, ActionRun
from app.models.host import Host, HostGroupMembership
from app.models.host_group import HostGroup
from app.models.sync_job import JobStatus, SyncJob
from app.tasks.sync_sweeper import STALE_THRESHOLD_MINUTES


def _aware(when: datetime) -> datetime:
    return when if when.tzinfo is not None else when.replace(tzinfo=UTC)


async def _queued_by_host(db: AsyncSession, host_ids: set[int]) -> dict[int, set[tuple[str, int]]]:
    """Operations deferred on each host, as ``(kind, id)`` so a host-targeted
    run that has both a run row and a host row in ``pending`` counts once."""
    queued: dict[int, set[tuple[str, int]]] = defaultdict(set)
    if not host_ids:
        return queued
    ids = list(host_ids)
    syncs = await db.execute(
        select(SyncJob.host_id, SyncJob.id).where(
            SyncJob.status == JobStatus.pending, SyncJob.host_id.in_(ids)
        )
    )
    for host_id, job_id in syncs.all():
        queued[host_id].add(("sync", job_id))
    host_runs = await db.execute(
        select(ActionHostRun.host_id, ActionHostRun.action_run_id).where(
            ActionHostRun.status == "pending", ActionHostRun.host_id.in_(ids)
        )
    )
    for host_id, run_id in host_runs.all():
        queued[host_id].add(("action", run_id))
    runs = await db.execute(
        select(ActionRun.host_id, ActionRun.id).where(
            ActionRun.status == "pending", ActionRun.host_id.in_(ids)
        )
    )
    for host_id, run_id in runs.all():
        queued[host_id].add(("action", run_id))
    return queued


async def get_stuck_operations(db: AsyncSession, now: datetime | None = None) -> StuckOperations:
    """Syncs and action runs still running past ``STALE_THRESHOLD_MINUTES``.

    The threshold is the sync sweeper's, the same one behind the exporter's
    ``labdog_sync_jobs_stale``. A long action may simply be slow — a
    kubeadm upgrade can take longer — so each item also says how much is
    queued behind it: that is what turns slow into a problem.
    """
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(minutes=STALE_THRESHOLD_MINUTES)

    sync_rows = (
        await db.execute(
            select(SyncJob, Host.hostname)
            .outerjoin(Host, Host.id == SyncJob.host_id)
            .where(
                SyncJob.status == JobStatus.running,
                SyncJob.started_at.isnot(None),
                SyncJob.started_at < cutoff,
            )
        )
    ).all()
    host_rows = (
        await db.execute(
            select(ActionHostRun, ActionRun.action_key, Host.hostname)
            .join(ActionRun, ActionRun.id == ActionHostRun.action_run_id)
            .outerjoin(Host, Host.id == ActionHostRun.host_id)
            .where(
                ActionHostRun.status == "running",
                ActionHostRun.started_at.isnot(None),
                ActionHostRun.started_at < cutoff,
            )
            .order_by(ActionHostRun.action_run_id, ActionHostRun.id)
        )
    ).all()

    held: set[int] = {job.host_id for job, _ in sync_rows if job.host_id is not None}
    held |= {hr.host_id for hr, _, _ in host_rows if hr.host_id is not None}
    queued = await _queued_by_host(db, held)

    def behind(host_ids: list[int], own: tuple[str, int]) -> int:
        ops: set[tuple[str, int]] = set()
        for h in host_ids:
            ops |= queued.get(h, set())
        ops.discard(own)
        return len(ops)

    items: list[StuckOperation] = []
    for job, hostname in sync_rows:
        modules = job.module_filter or ([job.module_type] if job.module_type != "bulk" else [])
        items.append(
            StuckOperation(
                kind="sync",
                id=job.id,
                label=", ".join(modules) if modules else "all modules",
                host_ids=[job.host_id] if job.host_id is not None else [],
                hostnames=[hostname or f"host #{job.host_id}"],
                started_at=_aware(job.started_at),
                queued_behind=behind([job.host_id], ("sync", job.id)),
            )
        )

    by_run: dict[int, StuckOperation] = {}
    for hr, action_key, hostname in host_rows:
        item = by_run.get(hr.action_run_id)
        if item is None:
            item = by_run[hr.action_run_id] = StuckOperation(
                kind="action",
                id=hr.action_run_id,
                label=action_key,
                host_ids=[],
                hostnames=[],
                started_at=_aware(hr.started_at),
                queued_behind=0,
            )
        if hr.host_id is not None:
            item.host_ids.append(hr.host_id)
        item.hostnames.append(hostname or hr.hostname)
        item.started_at = min(item.started_at, _aware(hr.started_at))
    for item in by_run.values():
        item.queued_behind = behind(item.host_ids, ("action", item.id))
        items.append(item)

    items.sort(key=lambda i: i.started_at)
    return StuckOperations(threshold_minutes=STALE_THRESHOLD_MINUTES, items=items)


async def get_module_status_counts(db: AsyncSession) -> list[ModuleStatusCount]:
    """Hosts per (module, sync status) — the exporter's
    ``labdog_host_module_status`` rows, as JSON."""
    return [
        ModuleStatusCount(module_type=m, sync_status=s, count=n)
        for m, s, n in await get_host_module_counts(db)
    ]


async def get_ca_cert_expiries(db: AsyncSession) -> list[CACertExpiry]:
    """Every live CA certificate with a known expiry, soonest first.

    A certificate declared on several groups (or a group and a host) is
    one entry: the same fingerprint is the same certificate, and its
    reach is the union of the hosts each rule covers.
    """
    rows = (
        await db.execute(
            select(CACertRule, HostGroup.name, Host.hostname)
            .outerjoin(HostGroup, HostGroup.id == CACertRule.group_id)
            .outerjoin(Host, Host.id == CACertRule.host_id)
            .where(CACertRule.state == CertState.present, CACertRule.not_after.isnot(None))
        )
    ).all()
    if not rows:
        return []

    group_ids = {rule.group_id for rule, _, _ in rows if rule.group_id is not None}
    members: dict[int, set[int]] = defaultdict(set)
    if group_ids:
        membership = await db.execute(
            select(HostGroupMembership.c.group_id, HostGroupMembership.c.host_id).where(
                HostGroupMembership.c.group_id.in_(group_ids)
            )
        )
        for group_id, host_id in membership.all():
            members[group_id].add(host_id)

    certs: dict[str, dict] = {}
    for rule, group_name, hostname in rows:
        entry = certs.setdefault(
            rule.fingerprint_sha256,
            {
                "name": rule.name,
                "not_after": _aware(rule.not_after),
                "hosts": set(),
                "scopes": [],
            },
        )
        if rule.group_id is not None:
            entry["hosts"] |= members.get(rule.group_id, set())
            entry["scopes"].append(f"group {group_name}")
        elif rule.host_id is not None:
            entry["hosts"].add(rule.host_id)
            entry["scopes"].append(f"host {hostname}")

    out = [
        CACertExpiry(
            name=c["name"],
            fingerprint_sha256=fp,
            not_after=c["not_after"],
            host_count=len(c["hosts"]),
            scopes=sorted(set(c["scopes"])),
        )
        for fp, c in certs.items()
    ]
    out.sort(key=lambda c: c.not_after)
    return out
