from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.logger import log_action
from app.auth.users import current_active_user
from app.crypto import decrypt_ssh_key, encrypt_ssh_key, get_master_key
from app.db import get_db
from app.grafana.client import PrometheusClient, PrometheusError
from app.grafana.metrics import fetch_fleet_metrics, fetch_host_metrics
from app.grafana.models import GrafanaInstance
from app.grafana.schemas import (
    DestinationAssignment,
    EffectiveDestination,
    FleetMetrics,
    GrafanaInstanceCreate,
    GrafanaInstanceResponse,
    GrafanaInstanceUpdate,
    GrafanaTestResponse,
    HostDestinations,
    HostMetrics,
    derive_query_url,
    to_response,
)
from app.grafana.service import (
    DESTINATION_KINDS,
    client_for_instance,
    instance_in_use,
    resolve_all_hosts,
    resolve_host_instance,
)
from app.models.host import Host
from app.models.host_group import HostGroup
from app.models.user import User

router = APIRouter(prefix="/grafana", tags=["grafana"])


async def _unset_other_defaults(db: AsyncSession, kind: str, keep_id: int | None) -> None:
    """Clear the default flag on other instances of the same kind."""
    stmt = update(GrafanaInstance).where(GrafanaInstance.kind == kind).values(is_default=False)
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
    kind: str,
    url: str,
    org_id: str | None,
    token: str | None,
    verify_ssl: bool,
    ca_cert_pem: str | None,
    auth_type: str,
    username: str | None,
) -> GrafanaTestResponse:
    client = PrometheusClient(
        query_url=derive_query_url(url, kind),
        org_id=org_id,
        token=token,
        verify_ssl=verify_ssl,
        ca_cert_pem=ca_cert_pem,
        auth_type=auth_type,
        username=username,
    )
    try:
        if kind == "loki":
            # Cheap reachability check against the Loki query API.
            await client.get_ok("/api/v1/labels")
        else:
            # `vector(1)` exercises the Mimir query path without depending on
            # any particular series existing yet.
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
    result = await db.execute(
        select(GrafanaInstance).order_by(GrafanaInstance.kind, GrafanaInstance.name)
    )
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

    # First instance of its kind is implicitly the default; else honour flag.
    same_kind = (
        (await db.execute(select(GrafanaInstance.id).where(GrafanaInstance.kind == body.kind)))
        .scalars()
        .all()
    )
    make_default = body.is_default or len(same_kind) == 0

    secret = body.token if body.auth_type != "none" else None
    inst = GrafanaInstance(
        name=body.name,
        kind=body.kind,
        url=body.url,
        org_id=body.org_id,
        auth_type=body.auth_type,
        username=body.username if body.auth_type == "basic" else None,
        encrypted_token=(encrypt_ssh_key(secret, get_master_key()) if secret else None),
        verify_ssl=body.verify_ssl,
        ca_cert_pem=body.ca_cert_pem,
        is_default=make_default,
    )
    db.add(inst)
    await db.flush()
    if make_default:
        await _unset_other_defaults(db, inst.kind, keep_id=inst.id)

    await log_action(
        db=db,
        action="create",
        entity_type="grafana_instance",
        entity_id=inst.id,
        user_id=user.id,
        after_state={
            "name": inst.name,
            "kind": inst.kind,
            "url": inst.url,
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

    before = {"name": inst.name, "kind": inst.kind, "is_default": inst.is_default}

    if body.name is not None and body.name != inst.name:
        clash = await db.execute(select(GrafanaInstance).where(GrafanaInstance.name == body.name))
        if clash.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Grafana instance name already exists")
        inst.name = body.name
    if body.kind is not None:
        inst.kind = body.kind
    if body.url is not None:
        inst.url = body.url
    if body.org_id is not None:
        inst.org_id = body.org_id or None
    if body.auth_type is not None:
        inst.auth_type = body.auth_type
        if body.auth_type == "none":
            inst.encrypted_token = None
            inst.username = None
        elif body.auth_type == "bearer":
            inst.username = None
    if body.username is not None:
        inst.username = body.username or None
    if body.token is not None:
        # Blank string = clear the secret; non-blank = replace.
        inst.encrypted_token = encrypt_ssh_key(body.token, get_master_key()) if body.token else None
    if body.verify_ssl is not None:
        inst.verify_ssl = body.verify_ssl
    if body.ca_cert_pem is not None:
        inst.ca_cert_pem = body.ca_cert_pem or None
    if body.is_default is not None:
        inst.is_default = body.is_default

    await db.flush()
    if inst.is_default:
        await _unset_other_defaults(db, inst.kind, keep_id=inst.id)

    await log_action(
        db=db,
        action="update",
        entity_type="grafana_instance",
        entity_id=inst.id,
        user_id=user.id,
        before_state=before,
        after_state={"name": inst.name, "kind": inst.kind, "is_default": inst.is_default},
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
    users = await instance_in_use(db, instance_id)
    if users:
        # Falling back to the default would point the host page at a
        # backend these hosts aren't shipping to, so make the operator
        # reassign them first.
        raise HTTPException(
            status_code=409,
            detail=(
                f"{inst.name} is still the metrics destination of "
                + ", ".join(users)
                + ". Assign them another destination first."
            ),
        )
    was_default = inst.is_default
    kind = inst.kind
    await log_action(
        db=db,
        action="delete",
        entity_type="grafana_instance",
        entity_id=inst.id,
        user_id=user.id,
        before_state={"name": inst.name, "kind": inst.kind},
    )
    await db.delete(inst)
    await db.flush()
    # Promote another instance of the same kind to default so the host page
    # keeps working.
    if was_default:
        remaining = (
            (
                await db.execute(
                    select(GrafanaInstance)
                    .where(GrafanaInstance.kind == kind)
                    .order_by(GrafanaInstance.id)
                )
            )
            .scalars()
            .first()
        )
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
        kind=inst.kind,
        url=inst.url,
        org_id=inst.org_id,
        token=await _decrypt_token(inst),
        verify_ssl=inst.verify_ssl,
        ca_cert_pem=inst.ca_cert_pem,
        auth_type=inst.auth_type,
        username=inst.username,
    )


@router.post("/instances/test", response_model=GrafanaTestResponse)
async def test_draft_instance(
    body: GrafanaInstanceCreate,
    _: User = Depends(current_active_user),
):
    """Pre-save connectivity test for an unsaved form — never persists."""
    return await _run_test(
        kind=body.kind,
        url=body.url,
        org_id=body.org_id,
        token=body.token,
        verify_ssl=body.verify_ssl,
        ca_cert_pem=body.ca_cert_pem,
        auth_type=body.auth_type,
        username=body.username,
    )


# ---------------------------------------------------------------------------
# Host metrics (instant)
# ---------------------------------------------------------------------------


async def _unconfigured_reason(db: AsyncSession) -> str:
    """Why there is no Mimir to query: ``no_default`` when instances exist
    but none is marked default, ``no_instance`` when there are none."""
    registered = await db.scalar(
        select(func.count()).select_from(GrafanaInstance).where(GrafanaInstance.kind == "mimir")
    )
    return "no_default" if registered else "no_instance"


@router.get("/fleet/metrics", response_model=FleetMetrics)
async def get_fleet_metrics(
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Instant CPU, memory and root-disk use for every host, in three
    queries — the Overview's resource hot spots."""
    # Each host is read from the Mimir it ships to (see
    # resolve_host_instance), so hosts on different destinations need one
    # set of queries per destination. A host's series are only taken from
    # its own destination: if it once shipped elsewhere, stale data there
    # must not stand in for it.
    hostnames = dict((await db.execute(select(Host.id, Host.hostname))).tuples().all())
    by_host = await resolve_all_hosts(db, "mimir")
    by_instance: dict[int, dict[int, str]] = {}
    for host_id, instance_id in by_host.items():
        if instance_id is not None and host_id in hostnames:
            by_instance.setdefault(instance_id, {})[host_id] = hostnames[host_id]
    if not by_instance:
        return FleetMetrics(configured=False, unconfigured_reason=await _unconfigured_reason(db))

    rows = []
    stamps: list[float] = []
    errors: list[str] = []
    for instance_id, names in sorted(by_instance.items()):
        inst = await db.get(GrafanaInstance, instance_id)
        if inst is None:
            continue
        try:
            part, sampled = await fetch_fleet_metrics(client_for_instance(inst), names)
        except PrometheusError as exc:
            errors.append(f"{inst.name}: {exc}" if len(by_instance) > 1 else str(exc))
            continue
        rows.extend(part)
        if sampled is not None:
            stamps.append(sampled)
    if errors and not rows:
        return FleetMetrics(configured=True, error="; ".join(errors))
    rows.sort(key=lambda r: r.hostname)
    return FleetMetrics(
        configured=True,
        sampled_at=datetime.fromtimestamp(max(stamps), tz=UTC) if stamps else None,
        hosts=rows,
        error="; ".join(errors) or None,
    )


@router.get("/hosts/{host_id}/metrics", response_model=HostMetrics)
async def get_host_metrics(
    host_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    host = await db.get(Host, host_id)
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    resolved = await resolve_host_instance(db, host_id, "mimir")
    if resolved.instance is None:
        return HostMetrics(configured=False, unconfigured_reason=await _unconfigured_reason(db))
    try:
        return await fetch_host_metrics(client_for_instance(resolved.instance), host_id)
    except PrometheusError as exc:
        return HostMetrics(configured=True, error=str(exc))


# ---------------------------------------------------------------------------
# Per-host / per-group destinations
# ---------------------------------------------------------------------------


async def _check_assignment(db: AsyncSession, body: DestinationAssignment) -> None:
    for kind in DESTINATION_KINDS:
        instance_id = getattr(body, f"{kind}_instance_id")
        if instance_id is None:
            continue
        inst = await db.get(GrafanaInstance, instance_id)
        if inst is None or inst.kind != kind:
            raise HTTPException(
                status_code=422, detail=f"No {kind} destination with id {instance_id}."
            )


async def _host_destinations(db: AsyncSession, host: Host) -> HostDestinations:
    effective: dict[str, EffectiveDestination] = {}
    for kind in DESTINATION_KINDS:
        r = await resolve_host_instance(db, host.id, kind)
        effective[kind] = EffectiveDestination(
            instance_id=r.instance.id if r.instance else None,
            instance_name=r.instance.name if r.instance else None,
            url=r.instance.url if r.instance else None,
            source=r.source,
            group_id=r.group_id,
            group_name=r.group_name,
        )
    return HostDestinations(
        assigned=DestinationAssignment(
            mimir_instance_id=host.mimir_instance_id, loki_instance_id=host.loki_instance_id
        ),
        effective=effective,
    )


@router.get("/destinations/hosts/{host_id}", response_model=HostDestinations)
async def get_host_destinations(
    host_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """The Mimir and Loki instances this host names, and where its data
    actually goes once groups and the default are taken into account."""
    host = await db.get(Host, host_id)
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")
    return await _host_destinations(db, host)


@router.put("/destinations/hosts/{host_id}", response_model=HostDestinations)
async def set_host_destinations(
    host_id: int,
    body: DestinationAssignment,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Set (or clear, with null) this host's own destinations. Takes effect
    on the host page at once, and on the host itself the next time an
    action that ships metrics (alloy-install) runs on it."""
    host = await db.get(Host, host_id)
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")
    await _check_assignment(db, body)
    before = {
        "mimir_instance_id": host.mimir_instance_id,
        "loki_instance_id": host.loki_instance_id,
    }
    host.mimir_instance_id = body.mimir_instance_id
    host.loki_instance_id = body.loki_instance_id
    await log_action(
        db=db,
        action="update",
        entity_type="host_metrics_destinations",
        entity_id=host.id,
        user_id=user.id,
        before_state=before,
        after_state=body.model_dump(),
    )
    await db.commit()
    await db.refresh(host)
    return await _host_destinations(db, host)


@router.get("/destinations/groups/{group_id}", response_model=DestinationAssignment)
async def get_group_destinations(
    group_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    group = await db.get(HostGroup, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    return DestinationAssignment(
        mimir_instance_id=group.mimir_instance_id, loki_instance_id=group.loki_instance_id
    )


@router.put("/destinations/groups/{group_id}", response_model=DestinationAssignment)
async def set_group_destinations(
    group_id: int,
    body: DestinationAssignment,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Set (or clear) the destinations for member hosts that don't name
    their own. When a host is in several groups that set one, the
    highest-priority group wins."""
    group = await db.get(HostGroup, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    await _check_assignment(db, body)
    before = {
        "mimir_instance_id": group.mimir_instance_id,
        "loki_instance_id": group.loki_instance_id,
    }
    group.mimir_instance_id = body.mimir_instance_id
    group.loki_instance_id = body.loki_instance_id
    await log_action(
        db=db,
        action="update",
        entity_type="group_metrics_destinations",
        entity_id=group.id,
        user_id=user.id,
        before_state=before,
        after_state=body.model_dump(),
    )
    await db.commit()
    return body
