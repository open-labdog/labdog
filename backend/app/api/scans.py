"""API endpoints for ScanConfig CRUD and pending-host management."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy import insert as sa_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.logger import log_action
from app.auth.users import current_active_user
from app.db import get_db
from app.models.host import Host, HostGroupMembership
from app.models.scan_config import PendingHost, ScanConfig
from app.models.ssh_key import SSHKey
from app.models.user import User
from app.schemas.scans import (
    ApproveBody,
    ApproveResponse,
    DismissBody,
    DismissResponse,
    PendingHostFleetResponse,
    PendingHostResponse,
    PendingSummaryResponse,
    ScanConfigCreate,
    ScanConfigResponse,
    ScanConfigUpdate,
)

router = APIRouter(prefix="/scans", tags=["scans"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_config_or_404(config_id: int, db: AsyncSession) -> ScanConfig:
    result = await db.execute(select(ScanConfig).where(ScanConfig.id == config_id))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Scan config not found")
    return config


# ---------------------------------------------------------------------------
# GET /api/scans/pending-summary
# Must be declared before /{id} routes so FastAPI doesn't interpret
# "pending-summary" as an integer path parameter.
# ---------------------------------------------------------------------------


@router.get("/pending-summary", response_model=PendingSummaryResponse)
async def pending_summary(
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the total count of all pending hosts across all scan configs."""
    result = await db.execute(select(func.count()).select_from(PendingHost))
    total = result.scalar_one()
    return PendingSummaryResponse(total=total)


@router.get("/pending", response_model=list[PendingHostFleetResponse])
async def list_all_pending_hosts(
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Return all pending hosts across every scan config, with the config name joined in.

    Used by the fleet-wide approval surface on the Hosts page (T9).
    Results are ordered by discovery time descending so the newest finds appear first.
    """
    result = await db.execute(
        select(
            PendingHost.id,
            PendingHost.scan_config_id,
            ScanConfig.name.label("scan_config_name"),
            PendingHost.ip_address,
            PendingHost.hostname,
            PendingHost.ssh_verified,
            PendingHost.ssh_error,
            PendingHost.discovered_at,
        )
        .join(ScanConfig, ScanConfig.id == PendingHost.scan_config_id)
        .order_by(PendingHost.discovered_at.desc())
    )
    rows = result.mappings().all()
    return [PendingHostFleetResponse(**row) for row in rows]


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.get("", response_model=list[ScanConfigResponse])
async def list_scan_configs(
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """List all scan configs ordered by creation date descending."""
    result = await db.execute(select(ScanConfig).order_by(ScanConfig.created_at.desc()))
    return result.scalars().all()


@router.post("", response_model=ScanConfigResponse, status_code=201)
async def create_scan_config(
    body: ScanConfigCreate,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new scan config."""
    existing = await db.execute(select(ScanConfig).where(ScanConfig.name == body.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Scan config name already exists")

    config = ScanConfig(
        name=body.name,
        cidrs=body.cidrs,
        ssh_key_id=body.ssh_key_id,
        ssh_port=body.ssh_port,
        default_group_ids=body.default_group_ids,
        interval_minutes=body.interval_minutes,
        cron_expression=body.cron_expression,
        enabled=body.enabled,
        auto_add=body.auto_add,
    )
    db.add(config)
    await db.commit()
    await db.refresh(config)
    return config


@router.get("/{config_id}", response_model=ScanConfigResponse)
async def get_scan_config(
    config_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Return a single scan config with its current pending host count."""
    config = await _get_config_or_404(config_id, db)

    count_result = await db.execute(
        select(func.count()).select_from(PendingHost).where(PendingHost.scan_config_id == config_id)
    )
    pending_count = count_result.scalar_one()

    response = ScanConfigResponse.model_validate(config)
    response.pending_count = pending_count
    return response


@router.put("/{config_id}", response_model=ScanConfigResponse)
async def update_scan_config(
    config_id: int,
    body: ScanConfigUpdate,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing scan config."""
    config = await _get_config_or_404(config_id, db)

    update_data = body.model_dump(exclude_unset=True)

    # When one schedule field is provided but not the other, ensure the resulting
    # state on the row still satisfies the XOR constraint.
    if "interval_minutes" in update_data and "cron_expression" not in update_data:
        # Caller is setting interval — clear cron.
        config.cron_expression = None
    elif "cron_expression" in update_data and "interval_minutes" not in update_data:
        # Caller is setting cron — clear interval.
        config.interval_minutes = None

    for field, value in update_data.items():
        setattr(config, field, value)

    await db.commit()
    await db.refresh(config)
    return config


@router.delete("/{config_id}", status_code=204)
async def delete_scan_config(
    config_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a scan config. Pending hosts are removed via CASCADE."""
    config = await _get_config_or_404(config_id, db)
    await db.delete(config)
    await db.commit()


# ---------------------------------------------------------------------------
# Run now
# ---------------------------------------------------------------------------


@router.post("/{config_id}/run", status_code=202)
async def run_scan_config_now(
    config_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Enqueue an immediate run of the scan config, bypassing the schedule."""
    # Verify it exists first.
    await _get_config_or_404(config_id, db)

    # Import at call time to keep the fast-path import chain clean.
    from app.tasks import celery_app  # noqa: PLC0415

    celery_app.send_task("scans.run_config", args=[config_id])
    return {"status": "queued", "config_id": config_id}


# ---------------------------------------------------------------------------
# Pending host endpoints
# ---------------------------------------------------------------------------


@router.get("/{config_id}/pending", response_model=list[PendingHostResponse])
async def list_pending_hosts(
    config_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """List all pending hosts for a given scan config."""
    await _get_config_or_404(config_id, db)
    result = await db.execute(
        select(PendingHost)
        .where(PendingHost.scan_config_id == config_id)
        .order_by(PendingHost.discovered_at.desc())
    )
    return result.scalars().all()


@router.post("/{config_id}/pending/approve", response_model=ApproveResponse)
async def approve_pending_hosts(
    config_id: int,
    body: ApproveBody,
    current_user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Promote pending hosts into the host inventory under the scan config's default groups.

    IDs that belong to a different scan config are silently excluded (scope guard).
    IPs already present in the host table are skipped without error but still removed
    from the pending queue — the user clicked Approve and the host exists, so it's done.
    """
    config = await _get_config_or_404(config_id, db)

    # Load the SSH key so we can read ssh_user from it (BUG-28: removed from ScanConfig).
    ssh_key_result = await db.execute(select(SSHKey).where(SSHKey.id == config.ssh_key_id))
    ssh_key = ssh_key_result.scalar_one()

    # Batch-load only rows that belong to *this* scan config — cross-config injection guard.
    pending_result = await db.execute(
        select(PendingHost).where(
            PendingHost.id.in_(body.ids),
            PendingHost.scan_config_id == config_id,
        )
    )
    pending_rows = pending_result.scalars().all()

    # Build a set of already-known IPs for dedup.
    existing_result = await db.execute(select(Host.ip_address))
    existing_ips: set[str] = {row[0] for row in existing_result.all()}

    approved = 0
    skipped_ips: list[str] = []
    approved_host_ids: list[int] = []

    for pending in pending_rows:
        ip = pending.ip_address
        if ip in existing_ips:
            # Host was added between discovery and approval — skip gracefully.
            skipped_ips.append(ip)
            continue

        hostname = pending.hostname or f"host-{ip}"

        host = Host(
            hostname=hostname,
            ip_address=ip,
            ssh_port=config.ssh_port,
            ssh_user=ssh_key.ssh_user,
            ssh_key_id=config.ssh_key_id,
        )
        db.add(host)
        await db.flush()  # materialise host.id before inserting memberships

        if config.default_group_ids:
            await db.execute(
                sa_insert(HostGroupMembership),
                [{"host_id": host.id, "group_id": gid} for gid in config.default_group_ids],
            )

        # Prevent within-batch duplicates if the same IP appears twice in the request.
        existing_ips.add(ip)
        approved += 1
        approved_host_ids.append(host.id)

        await log_action(
            db,
            action="discovery.approve",
            entity_type="scan_config",
            entity_id=config_id,
            user_id=current_user.id,
            after_state={"ip": ip, "hostname": hostname, "host_id": host.id},
        )

    # Delete ALL processed pending rows regardless of skip/approve — the user chose to
    # act on them; a duplicate-dedup skip is not a reason to keep them queued.
    if pending_rows:
        processed_ids = [p.id for p in pending_rows]
        await db.execute(
            delete(PendingHost).where(
                PendingHost.id.in_(processed_ids),
                PendingHost.scan_config_id == config_id,
            )
        )

    await db.commit()

    # Kick off OS-facts collection for the newly-promoted hosts so os_codename
    # is populated before the user opens them.
    if approved_host_ids:
        from app.tasks import celery_app  # noqa: PLC0415

        for hid in approved_host_ids:
            celery_app.send_task("app.tasks.facts.collect_host_facts", args=[hid])

    return ApproveResponse(
        approved=approved,
        skipped=len(skipped_ips),
        skipped_ips=skipped_ips,
    )


@router.post("/{config_id}/pending/dismiss", response_model=DismissResponse)
async def dismiss_pending_hosts(
    config_id: int,
    body: DismissBody,
    current_user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove pending hosts from the review queue without adding them to inventory."""
    await _get_config_or_404(config_id, db)

    result = await db.execute(
        delete(PendingHost).where(
            PendingHost.id.in_(body.ids),
            PendingHost.scan_config_id == config_id,
        )
    )
    dismissed = result.rowcount

    await log_action(
        db,
        action="discovery.dismiss",
        entity_type="scan_config",
        entity_id=config_id,
        user_id=current_user.id,
        after_state={"count": dismissed},
    )

    await db.commit()
    return DismissResponse(dismissed=dismissed)
