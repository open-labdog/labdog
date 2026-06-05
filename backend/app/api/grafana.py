from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.logger import log_action
from app.auth.users import current_active_user
from app.crypto import decrypt_ssh_key, encrypt_ssh_key, get_master_key
from app.db import get_db
from app.grafana.client import PrometheusClient, PrometheusError
from app.grafana.metrics import fetch_host_metrics
from app.grafana.models import GrafanaInstance
from app.grafana.schemas import (
    GrafanaInstanceCreate,
    GrafanaInstanceResponse,
    GrafanaInstanceUpdate,
    GrafanaTestResponse,
    HostMetrics,
    to_response,
)
from app.grafana.service import get_default_instance
from app.models.host import Host
from app.models.user import User

router = APIRouter(prefix="/grafana", tags=["grafana"])


async def _unset_other_defaults(db: AsyncSession, keep_id: int | None) -> None:
    stmt = update(GrafanaInstance).values(is_default=False)
    if keep_id is not None:
        stmt = stmt.where(GrafanaInstance.id != keep_id)
    await db.execute(stmt)


async def _decrypt_token(inst: GrafanaInstance) -> str | None:
    if inst.encrypted_token is None:
        return None
    try:
        return decrypt_ssh_key(inst.encrypted_token, get_master_key())
    except Exception:
        return None


async def _run_test(
    query_url: str,
    org_id: str | None,
    token: str | None,
    verify_ssl: bool,
    ca_cert_pem: str | None,
) -> GrafanaTestResponse:
    client = PrometheusClient(
        query_url=query_url,
        org_id=org_id,
        token=token,
        verify_ssl=verify_ssl,
        ca_cert_pem=ca_cert_pem,
    )
    try:
        # `vector(1)` exercises the query path without depending on any
        # particular series existing in the backend yet.
        await client.query("vector(1)")
    except PrometheusError as exc:
        return GrafanaTestResponse(success=False, message=str(exc))
    return GrafanaTestResponse(success=True, message="Connected — query API reachable")


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.get("/instances", response_model=list[GrafanaInstanceResponse])
async def list_instances(
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(GrafanaInstance).order_by(GrafanaInstance.name))
    return [to_response(i) for i in result.scalars().all()]


@router.post("/instances", response_model=GrafanaInstanceResponse, status_code=201)
async def create_instance(
    body: GrafanaInstanceCreate,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(select(GrafanaInstance).where(GrafanaInstance.name == body.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Grafana instance name already exists")

    count = len((await db.execute(select(GrafanaInstance.id))).scalars().all())
    # First instance is implicitly the default; otherwise honour the flag.
    make_default = body.is_default or count == 0

    inst = GrafanaInstance(
        name=body.name,
        prometheus_query_url=body.prometheus_query_url,
        prometheus_push_url=body.prometheus_push_url,
        loki_push_url=body.loki_push_url,
        org_id=body.org_id,
        encrypted_token=(
            encrypt_ssh_key(body.token, get_master_key()) if body.token else None
        ),
        verify_ssl=body.verify_ssl,
        ca_cert_pem=body.ca_cert_pem,
        is_default=make_default,
    )
    db.add(inst)
    await db.flush()
    if make_default:
        await _unset_other_defaults(db, keep_id=inst.id)

    await log_action(
        db=db,
        action="create",
        entity_type="grafana_instance",
        entity_id=inst.id,
        user_id=user.id,
        after_state={
            "name": inst.name,
            "prometheus_query_url": inst.prometheus_query_url,
            "has_token": inst.encrypted_token is not None,
            "is_default": inst.is_default,
        },
    )
    await db.commit()
    await db.refresh(inst)
    return to_response(inst)


@router.get("/instances/{instance_id}", response_model=GrafanaInstanceResponse)
async def get_instance(
    instance_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    inst = await db.get(GrafanaInstance, instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Grafana instance not found")
    return to_response(inst)


@router.put("/instances/{instance_id}", response_model=GrafanaInstanceResponse)
async def update_instance(
    instance_id: int,
    body: GrafanaInstanceUpdate,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    inst = await db.get(GrafanaInstance, instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Grafana instance not found")

    before = {"name": inst.name, "is_default": inst.is_default}

    if body.name is not None and body.name != inst.name:
        clash = await db.execute(select(GrafanaInstance).where(GrafanaInstance.name == body.name))
        if clash.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Grafana instance name already exists")
        inst.name = body.name
    if body.prometheus_query_url is not None:
        inst.prometheus_query_url = body.prometheus_query_url
    if body.prometheus_push_url is not None:
        inst.prometheus_push_url = body.prometheus_push_url
    if body.loki_push_url is not None:
        inst.loki_push_url = body.loki_push_url or None
    if body.org_id is not None:
        inst.org_id = body.org_id or None
    if body.token is not None:
        # Blank string = clear the token; non-blank = replace.
        inst.encrypted_token = (
            encrypt_ssh_key(body.token, get_master_key()) if body.token else None
        )
    if body.verify_ssl is not None:
        inst.verify_ssl = body.verify_ssl
    if body.ca_cert_pem is not None:
        inst.ca_cert_pem = body.ca_cert_pem or None
    if body.is_default is not None:
        inst.is_default = body.is_default

    await db.flush()
    if inst.is_default:
        await _unset_other_defaults(db, keep_id=inst.id)

    await log_action(
        db=db,
        action="update",
        entity_type="grafana_instance",
        entity_id=inst.id,
        user_id=user.id,
        before_state=before,
        after_state={"name": inst.name, "is_default": inst.is_default},
    )
    await db.commit()
    await db.refresh(inst)
    return to_response(inst)


@router.delete("/instances/{instance_id}", status_code=204)
async def delete_instance(
    instance_id: int,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    inst = await db.get(GrafanaInstance, instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Grafana instance not found")
    was_default = inst.is_default
    await log_action(
        db=db,
        action="delete",
        entity_type="grafana_instance",
        entity_id=inst.id,
        user_id=user.id,
        before_state={"name": inst.name},
    )
    await db.delete(inst)
    await db.flush()
    # Promote another instance to default so the host page keeps working.
    if was_default:
        remaining = (
            await db.execute(select(GrafanaInstance).order_by(GrafanaInstance.id))
        ).scalars().first()
        if remaining is not None:
            remaining.is_default = True
    await db.commit()


@router.post("/instances/{instance_id}/test", response_model=GrafanaTestResponse)
async def test_instance(
    instance_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    inst = await db.get(GrafanaInstance, instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Grafana instance not found")
    return await _run_test(
        query_url=inst.prometheus_query_url,
        org_id=inst.org_id,
        token=await _decrypt_token(inst),
        verify_ssl=inst.verify_ssl,
        ca_cert_pem=inst.ca_cert_pem,
    )


@router.post("/instances/test", response_model=GrafanaTestResponse)
async def test_draft_instance(
    body: GrafanaInstanceCreate,
    _: User = Depends(current_active_user),
):
    """Pre-save connectivity test for an unsaved form — never persists."""
    return await _run_test(
        query_url=body.prometheus_query_url,
        org_id=body.org_id,
        token=body.token,
        verify_ssl=body.verify_ssl,
        ca_cert_pem=body.ca_cert_pem,
    )


# ---------------------------------------------------------------------------
# Host metrics (instant)
# ---------------------------------------------------------------------------


@router.get("/hosts/{host_id}/metrics", response_model=HostMetrics)
async def get_host_metrics(
    host_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    host = await db.get(Host, host_id)
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    inst = await get_default_instance(db)
    if inst is None:
        return HostMetrics(configured=False)

    client = PrometheusClient(
        query_url=inst.prometheus_query_url,
        org_id=inst.org_id,
        token=await _decrypt_token(inst),
        verify_ssl=inst.verify_ssl,
        ca_cert_pem=inst.ca_cert_pem,
    )
    try:
        return await fetch_host_metrics(client, host_id)
    except PrometheusError as exc:
        return HostMetrics(configured=True, error=str(exc))
