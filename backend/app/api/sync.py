from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_db
from app.models.host import Host, HostGroupMembership
from app.models.host_group import HostGroup
from app.models.firewall_rule import FirewallRule
from app.models.sync_job import SyncJob
from app.models.user_group_permission import GroupRole
from app.models.user import User
from app.auth.users import current_active_user
from app.auth.rbac import get_user_accessible_group_ids, require_group_role
from app.rules.model import FirewallRuleSpec
from app.rules.merge import merge_group_rules
from app.sync.diff import compute_diff, fetch_current_state_stub

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


async def _get_desired_rules(host_id: int, db: AsyncSession) -> list[FirewallRuleSpec]:
    """Get merged desired rules for a host from DB."""
    memberships = await db.execute(
        select(HostGroupMembership.c.group_id).where(HostGroupMembership.c.host_id == host_id)
    )
    group_ids = [r[0] for r in memberships.all()]
    if not group_ids:
        return []

    groups_data = []
    for gid in group_ids:
        group_result = await db.execute(select(HostGroup).where(HostGroup.id == gid))
        group = group_result.scalar_one()
        rules_result = await db.execute(select(FirewallRule).where(FirewallRule.group_id == gid))
        rules = [
            FirewallRuleSpec(
                action=r.action.value if hasattr(r.action, "value") else r.action,
                protocol=r.protocol.value if hasattr(r.protocol, "value") else r.protocol,
                direction=r.direction.value if hasattr(r.direction, "value") else r.direction,
                source_cidr=r.source_cidr,
                destination_cidr=r.destination_cidr,
                port_start=r.port_start,
                port_end=r.port_end,
                comment=r.comment,
                is_system=r.is_system,
                priority=r.priority,
                group_id=r.group_id,
                rule_id=r.id,
            )
            for r in rules_result.scalars().all()
        ]
        groups_data.append({"id": gid, "priority": group.priority, "rules": rules})

    return merge_group_rules(groups_data)


@router.post("/hosts/{host_id}/plan", response_model=HostDiff)
async def plan_host(
    host_id: int,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Preview changes for a single host (does NOT apply)."""
    host_result = await db.execute(select(Host).where(Host.id == host_id))
    host = host_result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    # Check access
    accessible = await get_user_accessible_group_ids(user, db)
    if accessible is not None:
        memberships = await db.execute(
            select(HostGroupMembership.c.group_id).where(
                HostGroupMembership.c.host_id == host_id,
                HostGroupMembership.c.group_id.in_(accessible),
            )
        )
        if not memberships.all():
            raise HTTPException(status_code=403, detail="Not authorized")

    desired = await _get_desired_rules(host_id, db)
    current = await fetch_current_state_stub(host_id)
    diff = compute_diff(current, desired)

    return HostDiff(
        host_id=host_id,
        hostname=host.hostname,
        has_changes=diff.has_changes,
        rules_to_add=[_spec_to_diff_item(r) for r in diff.rules_to_add],
        rules_to_remove=[_spec_to_diff_item(r) for r in diff.rules_to_remove],
        rules_unchanged=[_spec_to_diff_item(r) for r in diff.rules_unchanged],
    )


@router.post("/groups/{group_id}/plan", response_model=list[HostDiff])
async def plan_group(
    group_id: int,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Preview changes for all hosts in a group (does NOT apply)."""
    accessible = await get_user_accessible_group_ids(user, db)
    if accessible is not None and group_id not in accessible:
        raise HTTPException(status_code=403, detail="Not authorized")

    memberships = await db.execute(
        select(HostGroupMembership.c.host_id).where(HostGroupMembership.c.group_id == group_id)
    )
    host_ids = [r[0] for r in memberships.all()]

    results = []
    for hid in host_ids:
        host_result = await db.execute(select(Host).where(Host.id == hid))
        host = host_result.scalar_one()
        desired = await _get_desired_rules(hid, db)
        current = await fetch_current_state_stub(hid)
        diff = compute_diff(current, desired)
        results.append(
            HostDiff(
                host_id=hid,
                hostname=host.hostname,
                has_changes=diff.has_changes,
                rules_to_add=[_spec_to_diff_item(r) for r in diff.rules_to_add],
                rules_to_remove=[_spec_to_diff_item(r) for r in diff.rules_to_remove],
                rules_unchanged=[_spec_to_diff_item(r) for r in diff.rules_unchanged],
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
    created_at: datetime
    model_config = {"from_attributes": True}


@router.post("/hosts/{host_id}/sync", response_model=SyncJobResponse, status_code=201)
async def trigger_host_sync(
    host_id: int,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Trigger sync for a single host."""
    # Check host exists
    host_result = await db.execute(select(Host).where(Host.id == host_id))
    host = host_result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    # Check no running sync for this host
    running = await db.execute(
        select(SyncJob).where(
            SyncJob.host_id == host_id,
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

    # Create sync job
    job = SyncJob(
        host_id=host_id,
        status="pending",
        triggered_by_user_id=user.id,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # Dispatch Celery task
    from app.tasks.sync import run_sync_playbook

    run_sync_playbook.delay(job_id=job.id, host_id=host_id)

    return job


@router.post("/groups/{group_id}/sync", status_code=201)
async def trigger_group_sync(
    group_id: int,
    _: None = Depends(require_group_role(GroupRole.editor)),
    user: User = Depends(current_active_user),
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

    jobs = []
    from app.tasks.sync import run_sync_playbook

    for hid in host_ids:
        # Skip hosts with running syncs
        running = await db.execute(
            select(SyncJob).where(
                SyncJob.host_id == hid, SyncJob.status.in_(["pending", "running"])
            )
        )
        if running.scalar_one_or_none():
            continue
        job = SyncJob(
            host_id=hid, group_id=group_id, status="pending", triggered_by_user_id=user.id
        )
        db.add(job)
        await db.flush()
        run_sync_playbook.delay(job_id=job.id, host_id=hid)
        jobs.append(job)

    await db.commit()
    return {"triggered": len(jobs), "skipped": len(host_ids) - len(jobs)}


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
    result = await db.execute(q)
    return result.scalars().all()
