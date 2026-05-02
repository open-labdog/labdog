from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.logger import log_action
from app.auth.users import current_active_user, current_superuser
from app.db import get_db
from app.models.firewall_rule import FirewallRule
from app.models.host import Host, HostGroupMembership
from app.models.sync_job import SyncJob
from app.models.user import User
from app.rules.desired_state import get_desired_state
from app.rules.model import ChainPolicies, FirewallRuleSpec
from app.sync.diff import SSHFetchError, compute_diff, fetch_current_firewall_state

router = APIRouter(prefix="/sync", tags=["sync"])


class RuleDiffItem(BaseModel):
    action: str
    protocol: str
    direction: str
    source_cidr: str | None = None
    destination_cidr: str | None = None
    port_start: int | None = None
    port_end: int | None = None
    comment: str | None = None
    is_system: bool = False


class HostDiff(BaseModel):
    host_id: int
    hostname: str
    has_changes: bool
    rules_to_add: list[RuleDiffItem]
    rules_to_remove: list[RuleDiffItem]
    rules_unchanged: list[RuleDiffItem]
    policy_changes: dict[str, list[str]] = {}
    error: str | None = None


def _spec_to_diff_item(spec: FirewallRuleSpec) -> RuleDiffItem:
    return RuleDiffItem(
        action=spec.action,
        protocol=spec.protocol,
        direction=spec.direction,
        source_cidr=spec.source_cidr,
        destination_cidr=spec.destination_cidr,
        port_start=spec.port_start,
        port_end=spec.port_end,
        comment=spec.comment,
        is_system=spec.is_system,
    )


async def _get_desired_state(
    host_id: int,
    db: AsyncSession,
    host_source_ip: str | None = None,
) -> tuple[list[FirewallRuleSpec], ChainPolicies]:
    """Get merged desired rules and policies for a host from DB."""
    return await get_desired_state(host_id, db, host_source_ip=host_source_ip)


@router.post("/hosts/{host_id}/plan", response_model=HostDiff)
async def plan_host(
    host_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Preview changes for a single host (does NOT apply)."""
    host_result = await db.execute(select(Host).where(Host.id == host_id))
    host = host_result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    desired, desired_policies = await _get_desired_state(
        host_id, db, host_source_ip=host.labdog_source_ip
    )
    try:
        state = await fetch_current_firewall_state(host_id, db)
    except SSHFetchError as exc:
        raise HTTPException(
            status_code=502, detail=f"Cannot reach host {exc.hostname}: {exc.detail}"
        ) from exc
    diff = compute_diff(
        state.rules, desired, current_policies=state.policies, desired_policies=desired_policies
    )
    policy_changes = {k: list(v) for k, v in diff.policy_changes.items()}

    return HostDiff(
        host_id=host_id,
        hostname=host.hostname,
        has_changes=diff.has_changes,
        rules_to_add=[_spec_to_diff_item(r) for r in diff.rules_to_add],
        rules_to_remove=[_spec_to_diff_item(r) for r in diff.rules_to_remove],
        rules_unchanged=[_spec_to_diff_item(r) for r in diff.rules_unchanged],
        policy_changes=policy_changes,
    )


@router.post("/groups/{group_id}/plan", response_model=list[HostDiff])
async def plan_group(
    group_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Preview changes for all hosts in a group (does NOT apply)."""
    memberships = await db.execute(
        select(HostGroupMembership.c.host_id).where(HostGroupMembership.c.group_id == group_id)
    )
    host_ids = [r[0] for r in memberships.all()]

    results = []
    for hid in host_ids:
        host_result = await db.execute(select(Host).where(Host.id == hid))
        host = host_result.scalar_one()
        desired, desired_policies = await _get_desired_state(
            hid, db, host_source_ip=host.labdog_source_ip
        )
        try:
            state = await fetch_current_firewall_state(hid, db)
        except SSHFetchError as exc:
            results.append(
                HostDiff(
                    host_id=hid,
                    hostname=host.hostname,
                    has_changes=False,
                    rules_to_add=[],
                    rules_to_remove=[],
                    rules_unchanged=[],
                    error=f"Cannot reach host: {exc.detail}",
                )
            )
            continue
        diff = compute_diff(
            state.rules, desired, current_policies=state.policies, desired_policies=desired_policies
        )
        policy_changes = {k: list(v) for k, v in diff.policy_changes.items()}
        results.append(
            HostDiff(
                host_id=hid,
                hostname=host.hostname,
                has_changes=diff.has_changes,
                rules_to_add=[_spec_to_diff_item(r) for r in diff.rules_to_add],
                rules_to_remove=[_spec_to_diff_item(r) for r in diff.rules_to_remove],
                rules_unchanged=[_spec_to_diff_item(r) for r in diff.rules_unchanged],
                policy_changes=policy_changes,
            )
        )

    return results


# ---------------------------------------------------------------------------
# Sync execution endpoints
# ---------------------------------------------------------------------------


class SyncJobResponse(BaseModel):
    id: int
    host_id: int
    group_id: int | None
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    ansible_output: str | None
    error_message: str | None
    triggered_by_user_id: int | None
    module_type: str = "firewall"
    created_at: datetime
    model_config = {"from_attributes": True}


@router.post("/hosts/{host_id}/sync", response_model=SyncJobResponse, status_code=201)
async def trigger_host_sync(
    host_id: int,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    """Trigger sync for a single host."""
    # Check host exists
    host_result = await db.execute(select(Host).where(Host.id == host_id))
    host = host_result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    # Check no running firewall sync for this host
    running = await db.execute(
        select(SyncJob).where(
            SyncJob.host_id == host_id,
            SyncJob.module_type == "firewall",
            SyncJob.status.in_(["pending", "running"]),
        )
    )
    if running.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Sync already in progress for this host")

    # Check host has rules (via groups)
    memberships = await db.execute(
        select(HostGroupMembership.c.group_id).where(HostGroupMembership.c.host_id == host_id)
    )
    group_ids = [r[0] for r in memberships.all()]
    if not group_ids:
        raise HTTPException(status_code=400, detail="Host has no groups assigned")

    rules_count = await db.execute(
        select(func.count(FirewallRule.id)).where(FirewallRule.group_id.in_(group_ids))
    )
    if rules_count.scalar() == 0:
        raise HTTPException(
            status_code=400,
            detail="Cannot sync — no rules defined. This would remove all firewall rules.",
        )

    # Capture user.id eagerly — see BUG-41/SEC-05 for the rationale: a
    # rollback during the IntegrityError path expires the ORM-bound
    # ``user`` and lazy-loading ``user.id`` would attempt sync IO.
    user_id = user.id

    # Create sync job (partial unique index prevents duplicates)
    job = SyncJob(
        host_id=host_id,
        status="pending",
        triggered_by_user_id=user_id,
    )
    db.add(job)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Sync already in progress for this host")

    # SEC-05: emit a trigger-time audit row so "who clicked sync, when"
    # is recorded immediately — not only on Celery finalisation. This
    # endpoint is the per-tab firewall-sync trigger; record
    # ``trigger_kind="per_host"`` and a synthetic single-element
    # ``module_filter`` derived from the legacy module_type.
    await log_action(
        db,
        action="sync_triggered",
        entity_type="host",
        entity_id=host_id,
        user_id=user_id,
        before_state=None,
        after_state={
            "sync_job_id": job.id,
            "module_filter": ["firewall"],
            "trigger_kind": "per_host",
        },
    )
    await db.commit()
    await db.refresh(job)

    # Dispatch Celery task
    from app.tasks.sync import run_sync_playbook

    run_sync_playbook.delay(job_id=job.id, host_id=host_id)

    return job


@router.post("/groups/{group_id}/sync", status_code=201)
async def trigger_group_sync(
    group_id: int,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    """Trigger sync for all hosts in a group."""
    # Check group has rules
    rules_count = await db.execute(
        select(func.count(FirewallRule.id)).where(FirewallRule.group_id == group_id)
    )
    if rules_count.scalar() == 0:
        raise HTTPException(status_code=400, detail="Cannot sync — no rules defined.")

    memberships = await db.execute(
        select(HostGroupMembership.c.host_id).where(HostGroupMembership.c.group_id == group_id)
    )
    host_ids = [r[0] for r in memberships.all()]
    if not host_ids:
        raise HTTPException(status_code=400, detail="No hosts in this group")

    from app.tasks.sync import run_sync_playbook

    # Capture user.id eagerly so post-rollback access is safe.
    user_id = user.id

    # BUG-37: dispatch must happen AFTER commit, not before. flush() makes
    # job.id available to Python but does not make the row visible to the
    # Celery worker's connection. Build the dispatch list in the loop, commit
    # all rows together, then dispatch.
    pending: list[tuple[int, int]] = []
    for hid in host_ids:
        running = await db.execute(
            select(SyncJob).where(
                SyncJob.host_id == hid,
                SyncJob.module_type == "firewall",
                SyncJob.status.in_(["pending", "running"]),
            )
        )
        if running.scalar_one_or_none():
            continue
        job = SyncJob(
            host_id=hid, group_id=group_id, status="pending", triggered_by_user_id=user_id
        )
        db.add(job)
        await db.flush()
        pending.append((job.id, hid))

    # SEC-05: emit a trigger-time audit row scoped to the group entity.
    # ``after_state.hosts`` lists the host IDs that actually got jobs
    # dispatched (skipping those with an in-flight sync). Module filter
    # is the legacy single-module ``["firewall"]`` for this endpoint.
    if pending:
        await log_action(
            db,
            action="sync_triggered",
            entity_type="host_group",
            entity_id=group_id,
            user_id=user_id,
            before_state=None,
            after_state={
                "sync_job_ids": [jid for jid, _ in pending],
                "hosts": [hid for _, hid in pending],
                "module_filter": ["firewall"],
                "trigger_kind": "per_group",
            },
        )
    await db.commit()

    for job_id, hid in pending:
        run_sync_playbook.delay(job_id=job_id, host_id=hid)
    return {"triggered": len(pending), "skipped": len(host_ids) - len(pending)}


class BulkSyncRequest(BaseModel):
    module_filter: list[str] | None = None


class BulkSyncResponse(BaseModel):
    job_id: int
    status: str
    module_filter: list[str] | None


# Canonical module names accepted by the orchestrator's module_filter.
# Kept in sync with ``app.ansible_runtime.composer.CANONICAL_ORDER``.
_BULK_ALLOWED_MODULES: frozenset[str] = frozenset(
    {
        "firewall",
        "services",
        "packages",
        "hosts-file",
        "cron",
        "linux-users",
        "resolver",
    }
)


@router.post("/hosts/{host_id}/bulk", response_model=BulkSyncResponse)
async def trigger_bulk_sync(
    host_id: int,
    body: BulkSyncRequest,
    response: Response,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    """Trigger a coalesced multi-module sync for one host.

    ``body.module_filter`` is either ``None`` (sync every supported
    module) or a non-empty list of canonical module names. The task is
    dispatched as a single ``run_host_sync`` Celery job tagged with
    ``module_type="bulk"``.

    Idempotency: when a bulk SyncJob is already pending or running for
    the host, the existing job's ID is returned with HTTP 200 — the
    partial unique index ``uq_sync_job_active`` guarantees no duplicate
    ever lands in the DB. Fresh inserts return HTTP 201.
    """
    # Validate module_filter shape early so a bad request never hits the
    # DB or queue.
    module_filter = body.module_filter
    if module_filter is not None:
        if len(module_filter) == 0:
            raise HTTPException(
                status_code=400,
                detail="module_filter must be null or non-empty",
            )
        unknown = [m for m in module_filter if m not in _BULK_ALLOWED_MODULES]
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown module(s) in module_filter: {unknown}",
            )

    # Verify host exists.
    host_result = await db.execute(select(Host).where(Host.id == host_id))
    host = host_result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    # Capture user.id eagerly. After a possible session rollback below
    # the ORM's lazy-load of ``user.id`` would attempt sync IO outside
    # the greenlet context and raise ``MissingGreenlet``.
    user_id = user.id

    job = SyncJob(
        host_id=host_id,
        status="pending",
        module_type="bulk",
        triggered_by_user_id=user_id,
    )
    db.add(job)
    # Pre-flush so ``job.id`` is available for the audit row that we
    # commit alongside the SyncJob insert. If the unique index trips,
    # the audit row is rolled back together with the job and we emit a
    # fresh audit on the idempotent-200 path below.
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        existing_result = await db.execute(
            select(SyncJob).where(
                SyncJob.host_id == host_id,
                SyncJob.module_type == "bulk",
                SyncJob.status.in_(["pending", "running"]),
            )
        )
        existing = existing_result.scalar_one_or_none()
        if existing is None:
            raise HTTPException(
                status_code=409,
                detail="Bulk sync conflict; please retry",
            ) from None
        existing_id = existing.id
        existing_status = (
            existing.status.value if hasattr(existing.status, "value") else str(existing.status)
        )
        # SEC-05 idempotent-200 audit row.
        await log_action(
            db,
            action="sync_triggered",
            entity_type="host",
            entity_id=host_id,
            user_id=user_id,
            before_state=None,
            after_state={
                "sync_job_id": existing_id,
                "module_filter": module_filter,
                "trigger_kind": "bulk",
            },
        )
        await db.commit()
        response.status_code = 200
        # BUG-41: existing SyncJob doesn't persist the original filter
        # list; surface ``None`` to avoid lying about what the queued
        # job will do.
        return BulkSyncResponse(
            job_id=existing_id,
            status=existing_status,
            module_filter=None,
        )

    # Fresh insert path: emit the trigger-time audit row in the same
    # transaction as the SyncJob commit, so they're durably linked.
    await log_action(
        db,
        action="sync_triggered",
        entity_type="host",
        entity_id=host_id,
        user_id=user_id,
        before_state=None,
        after_state={
            "sync_job_id": job.id,
            "module_filter": module_filter,
            "trigger_kind": "bulk",
        },
    )
    await db.commit()
    await db.refresh(job)

    # Fresh insert — dispatch the orchestrator.
    from app.tasks.host_sync_orchestrator import run_host_sync

    run_host_sync.delay(job_id=job.id, host_id=host_id, module_filter=module_filter)

    response.status_code = 201
    return BulkSyncResponse(
        job_id=job.id,
        status=job.status.value if hasattr(job.status, "value") else str(job.status),
        module_filter=module_filter,
    )


@router.get("/jobs/{job_id}", response_model=SyncJobResponse)
async def get_job(
    job_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(SyncJob).where(SyncJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/jobs", response_model=list[SyncJobResponse])
async def list_jobs(
    host_id: int | None = None,
    group_id: int | None = None,
    status: str | None = None,
    module_type: str | None = None,
    limit: int = 20,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(SyncJob).order_by(SyncJob.id.desc()).limit(limit)
    if host_id:
        q = q.where(SyncJob.host_id == host_id)
    if group_id:
        q = q.where(SyncJob.group_id == group_id)
    if status:
        q = q.where(SyncJob.status == status)
    if module_type:
        q = q.where(SyncJob.module_type == module_type)
    result = await db.execute(q)
    return result.scalars().all()
