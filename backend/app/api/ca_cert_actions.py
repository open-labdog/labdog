"""API endpoints for CA cert deploy actions.

CA cert management is modeled as a one-time *Action*, not a declarative
sync. This module exposes:

- ``POST /api/ca-certs/hosts/{host_id}/deploy`` — run for one host
- ``POST /api/ca-certs/groups/{group_id}/deploy`` — run for every host in group
- ``GET  /api/ca-certs/hosts/{host_id}/runs`` — recent runs for a host
- ``GET  /api/ca-certs/groups/{group_id}/runs`` — recent runs for a group
- ``GET  /api/ca-certs/runs/{run_id}`` — single run with output

Action runs are stored as ``SyncJob`` rows with ``module_type='ca_cert'``.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_superuser
from app.ca_certs.actions import (
    CA_CERT_MODULE_TYPE,
    enqueue_ca_cert_action_for_host,
    host_has_running_ca_cert_action,
)
from app.db import get_db
from app.models.host import Host, HostGroupMembership
from app.models.host_group import HostGroup
from app.models.sync_job import SyncJob
from app.models.user import User

router = APIRouter(prefix="/ca-certs", tags=["ca-cert-actions"])


class CACertActionRunResponse(BaseModel):
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


# ---------------------------------------------------------------------------
# Trigger
# ---------------------------------------------------------------------------


@router.post(
    "/hosts/{host_id}/deploy",
    response_model=CACertActionRunResponse,
    status_code=201,
)
async def deploy_ca_certs_to_host(
    host_id: int,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    host = (await db.execute(
        select(Host).where(Host.id == host_id)
    )).scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")
    if not host.ssh_key_id:
        raise HTTPException(
            status_code=400, detail="Host has no SSH key assigned"
        )
    if await host_has_running_ca_cert_action(host_id, db):
        raise HTTPException(
            status_code=409,
            detail="A CA cert deploy is already in progress for this host",
        )

    job = await enqueue_ca_cert_action_for_host(
        host_id, db, triggered_by_user_id=user.id
    )
    if job is None:
        raise HTTPException(
            status_code=400, detail="Could not enqueue CA cert deploy"
        )

    await db.commit()
    await db.refresh(job)
    return job


@router.post("/groups/{group_id}/deploy", status_code=201)
async def deploy_ca_certs_to_group(
    group_id: int,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    group = (await db.execute(
        select(HostGroup).where(HostGroup.id == group_id)
    )).scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    memberships = await db.execute(
        select(HostGroupMembership.c.host_id).where(
            HostGroupMembership.c.group_id == group_id
        )
    )
    host_ids = [r[0] for r in memberships.all()]
    if not host_ids:
        raise HTTPException(status_code=400, detail="No hosts in this group")

    triggered = 0
    skipped = 0
    for hid in host_ids:
        job = await enqueue_ca_cert_action_for_host(
            hid,
            db,
            triggered_by_user_id=user.id,
            group_id=group_id,
        )
        if job is None:
            skipped += 1
        else:
            triggered += 1

    await db.commit()
    return {"triggered": triggered, "skipped": skipped, "total_hosts": len(host_ids)}


# ---------------------------------------------------------------------------
# Run history
# ---------------------------------------------------------------------------


@router.get(
    "/hosts/{host_id}/runs",
    response_model=list[CACertActionRunResponse],
)
async def list_host_ca_cert_runs(
    host_id: int,
    limit: int = 20,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    host = await db.scalar(select(Host).where(Host.id == host_id))
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    result = await db.execute(
        select(SyncJob)
        .where(
            SyncJob.host_id == host_id,
            SyncJob.module_type == CA_CERT_MODULE_TYPE,
        )
        .order_by(SyncJob.created_at.desc())
        .limit(max(1, min(limit, 100)))
    )
    return result.scalars().all()


@router.get(
    "/groups/{group_id}/runs",
    response_model=list[CACertActionRunResponse],
)
async def list_group_ca_cert_runs(
    group_id: int,
    limit: int = 50,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    group = await db.scalar(select(HostGroup).where(HostGroup.id == group_id))
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    result = await db.execute(
        select(SyncJob)
        .where(
            SyncJob.group_id == group_id,
            SyncJob.module_type == CA_CERT_MODULE_TYPE,
        )
        .order_by(SyncJob.created_at.desc())
        .limit(max(1, min(limit, 200)))
    )
    return result.scalars().all()


@router.get("/runs/{run_id}", response_model=CACertActionRunResponse)
async def get_ca_cert_run(
    run_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    job = (await db.execute(
        select(SyncJob).where(
            SyncJob.id == run_id,
            SyncJob.module_type == CA_CERT_MODULE_TYPE,
        )
    )).scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="CA cert action run not found")
    return job
