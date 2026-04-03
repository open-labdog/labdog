from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone
from app.db import get_db
from app.models.host import Host, HostGroupMembership
from app.models.host_group import HostGroup
from app.models.firewall_rule import FirewallRule
from app.models.user import User
from app.auth.users import current_active_user, current_superuser
from app.drift.detector import check_drift
from app.rules.model import ChainPolicies, FirewallRuleSpec
from app.rules.merge import merge_group_rules, merge_group_policies
from app.rules.converter import firewall_rules_to_specs
from app.rules.desired_state import get_desired_state

router = APIRouter(prefix="/drift", tags=["drift"])


class DriftResponse(BaseModel):
    host_id: int
    status: str
    has_changes: bool
    add_count: int
    remove_count: int
    policy_changes: dict[str, list[str]] = {}
    error_message: str | None = None
    checked_at: str


class DriftSettingsUpdate(BaseModel):
    drift_check_enabled: bool


def _drift_result_to_response(host_id: int, result) -> DriftResponse:
    policy_changes = {}
    if result.diff and result.diff.policy_changes:
        policy_changes = {k: list(v) for k, v in result.diff.policy_changes.items()}
    return DriftResponse(
        host_id=host_id,
        status=result.status,
        has_changes=result.diff.has_changes if result.diff else False,
        add_count=len(result.diff.rules_to_add) if result.diff else 0,
        remove_count=len(result.diff.rules_to_remove) if result.diff else 0,
        policy_changes=policy_changes,
        error_message=result.error_message,
        checked_at=result.checked_at.isoformat(),
    )


async def _get_desired_state_for_host(
    host_id: int, db: AsyncSession, host_source_ip: str | None = None,
) -> tuple[list[FirewallRuleSpec], ChainPolicies]:
    """Return (merged_rules, merged_policies) for a host."""
    return await get_desired_state(host_id, db, host_source_ip=host_source_ip)


@router.post("/hosts/{host_id}/check", response_model=DriftResponse)
async def check_host_drift(
    host_id: int,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    host_result = await db.execute(select(Host).where(Host.id == host_id))
    host = host_result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")
    backend = host.firewall_backend.value if hasattr(host.firewall_backend, "value") else host.firewall_backend
    if backend == "unknown":
        from app.models.host import SyncStatus
        host.sync_status = SyncStatus.unknown
        host.last_drift_check_at = datetime.now(timezone.utc)
        await db.commit()
        return DriftResponse(
            host_id=host_id,
            status="unknown",
            has_changes=False,
            add_count=0,
            remove_count=0,
            error_message="Firewall backend not detected",
            checked_at=datetime.now(timezone.utc).isoformat(),
        )
    desired, policies = await _get_desired_state_for_host(host_id, db, host_source_ip=host.barricade_source_ip)
    result = await check_drift(host_id, desired, db, desired_policies=policies)
    host.sync_status = result.status
    host.last_drift_check_at = datetime.now(timezone.utc)
    await db.commit()
    return _drift_result_to_response(host_id, result)


@router.post("/groups/{group_id}/check", response_model=list[DriftResponse])
async def check_group_drift(
    group_id: int,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    memberships = await db.execute(
        select(HostGroupMembership.c.host_id).where(HostGroupMembership.c.group_id == group_id)
    )
    host_ids = [r[0] for r in memberships.all()]
    results = []
    from app.models.host import SyncStatus

    for hid in host_ids:
        host_result = await db.execute(select(Host).where(Host.id == hid))
        host = host_result.scalar_one()
        backend = host.firewall_backend.value if hasattr(host.firewall_backend, "value") else host.firewall_backend
        if backend == "unknown":
            host.sync_status = SyncStatus.unknown
            host.last_drift_check_at = datetime.now(timezone.utc)
            await db.commit()
            results.append(
                DriftResponse(
                    host_id=hid,
                    status="unknown",
                    has_changes=False,
                    add_count=0,
                    remove_count=0,
                    error_message="Firewall backend not detected",
                    checked_at=datetime.now(timezone.utc).isoformat(),
                )
            )
            continue
        desired, policies = await _get_desired_state_for_host(hid, db, host_source_ip=host.barricade_source_ip)
        result = await check_drift(hid, desired, db, desired_policies=policies)
        host.sync_status = result.status
        host.last_drift_check_at = datetime.now(timezone.utc)
        await db.commit()
        results.append(_drift_result_to_response(hid, result))
    return results


@router.put("/hosts/{host_id}/settings")
async def update_drift_settings(
    host_id: int,
    body: DriftSettingsUpdate,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    host_result = await db.execute(select(Host).where(Host.id == host_id))
    host = host_result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")
    host.drift_check_enabled = body.drift_check_enabled
    await db.commit()
    return {"drift_check_enabled": host.drift_check_enabled}
