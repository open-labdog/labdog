import asyncssh
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_active_user, current_superuser
from app.ca_certs.actions import auto_enqueue_for_new_membership
from app.crypto.encryption import decrypt_ssh_key
from app.crypto.key_management import get_master_key
from app.db import get_db
from app.models.firewall_rule import FirewallRule
from app.models.host import Host, HostGroupMembership
from app.models.ssh_key import SSHKey
from app.models.user import User
from app.schemas.hosts import HostCreate, HostResponse, HostUpdate
from app.ssh_utils import ssh_connect


class ImportRulesRequest(BaseModel):
    group_id: int
    rules: list[dict]  # list of rule specs to import


router = APIRouter(prefix="/hosts", tags=["hosts"])


@router.get("", response_model=list[HostResponse])
async def list_hosts(
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Host))
    hosts = result.scalars().all()

    # Populate group_ids for all hosts in a single query
    if hosts:
        host_ids = [h.id for h in hosts]
        memberships = await db.execute(
            select(HostGroupMembership.c.host_id, HostGroupMembership.c.group_id).where(
                HostGroupMembership.c.host_id.in_(host_ids)
            )
        )
        groups_by_host: dict[int, list[int]] = {}
        for host_id, group_id in memberships.all():
            groups_by_host.setdefault(host_id, []).append(group_id)
        for h in hosts:
            setattr(h, "group_ids", groups_by_host.get(h.id, []))

    return hosts


@router.post("", response_model=HostResponse, status_code=201)
async def create_host(
    body: HostCreate,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    hostname = body.hostname

    # Resolve SSH user from key (if provided), falling back to body.ssh_user
    ssh_user = body.ssh_user
    ssh_key = None
    if body.ssh_key_id:
        key_result = await db.execute(select(SSHKey).where(SSHKey.id == body.ssh_key_id))
        ssh_key = key_result.scalar_one_or_none()
        if ssh_key:
            ssh_user = ssh_key.ssh_user

    # If hostname is empty, try to fetch it from the host via SSH
    source_ip = None
    if not hostname and ssh_key:
        try:
            master_key = get_master_key()
            private_pem = decrypt_ssh_key(ssh_key.encrypted_private_key, master_key)
            imported_key = asyncssh.import_private_key(private_pem)
            async with ssh_connect(
                body.ip_address,
                port=body.ssh_port,
                username=ssh_user,
                client_keys=[imported_key],
            ) as conn:
                result = await conn.run("hostname", check=True)
                hostname = result.stdout.strip()
                from app.ssh_utils import get_source_ip

                source_ip = await get_source_ip(conn)
        except Exception:
            raise HTTPException(
                status_code=422,
                detail="Hostname not provided and could not be fetched via SSH",
            )

    if not hostname:
        raise HTTPException(
            status_code=422,
            detail="Hostname is required (provide it or assign an SSH key to auto-detect)",
        )

    host = Host(
        hostname=hostname,
        ip_address=body.ip_address,
        ssh_port=body.ssh_port,
        ssh_user=ssh_user,
        ssh_key_id=body.ssh_key_id,
        barricade_source_ip=source_ip,
    )
    db.add(host)
    await db.flush()  # get host.id

    if body.group_ids:
        await db.execute(
            insert(HostGroupMembership),
            [{"host_id": host.id, "group_id": gid} for gid in body.group_ids],
        )
        await db.flush()
        for gid in body.group_ids:
            await auto_enqueue_for_new_membership(host.id, gid, db, triggered_by_user_id=user.id)

    await db.commit()
    await db.refresh(host)
    return host


@router.get("/summary")
async def list_hosts_summary(
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Return all hosts with group memberships and per-module override counts."""
    from app.ca_certs.models import CACertRule
    from app.cron.models import CronJob
    from app.hosts_mgmt.models import HostsEntry
    from app.models.firewall_rule import FirewallRule
    from app.packages.models import PackageRule
    from app.resolver.models import ResolverConfig
    from app.services.models import ServiceRule
    from app.user_mgmt.models import LinuxGroup, LinuxUser

    result = await db.execute(select(Host))
    hosts = result.scalars().all()
    if not hosts:
        return []

    host_ids = [h.id for h in hosts]

    memberships = await db.execute(
        select(HostGroupMembership.c.host_id, HostGroupMembership.c.group_id).where(
            HostGroupMembership.c.host_id.in_(host_ids)
        )
    )
    groups_by_host: dict[int, list[int]] = {}
    for hid, gid in memberships.all():
        groups_by_host.setdefault(hid, []).append(gid)

    async def _counts(model):
        rows = await db.execute(
            select(model.host_id, func.count())
            .where(model.host_id.in_(host_ids), model.host_id.is_not(None))
            .group_by(model.host_id)
        )
        return {r[0]: r[1] for r in rows}

    fw = await _counts(FirewallRule)
    he = await _counts(HostsEntry)
    svc = await _counts(ServiceRule)
    lu = await _counts(LinuxUser)
    lg = await _counts(LinuxGroup)
    cj = await _counts(CronJob)
    pkg = await _counts(PackageRule)
    res = await _counts(ResolverConfig)
    ca = await _counts(CACertRule)

    out = []
    for h in hosts:
        hid = h.id
        out.append(
            {
                "id": hid,
                "hostname": h.hostname,
                "ip_address": h.ip_address,
                "ssh_port": h.ssh_port,
                "ssh_user": h.ssh_user,
                "firewall_backend": h.firewall_backend.value
                if hasattr(h.firewall_backend, "value")
                else h.firewall_backend,
                "sync_status": h.sync_status.value
                if hasattr(h.sync_status, "value")
                else h.sync_status,
                "barricade_source_ip": h.barricade_source_ip,
                "drift_check_enabled": h.drift_check_enabled,
                "last_sync_at": h.last_sync_at.isoformat() if h.last_sync_at else None,
                "last_drift_check_at": h.last_drift_check_at.isoformat()
                if h.last_drift_check_at
                else None,
                "ssh_key_id": h.ssh_key_id,
                "group_ids": groups_by_host.get(hid, []),
                "created_at": h.created_at.isoformat() if h.created_at else None,
                "updated_at": h.updated_at.isoformat() if h.updated_at else None,
                "override_counts": {
                    "firewall": fw.get(hid, 0),
                    "hosts_file": he.get(hid, 0),
                    "services": svc.get(hid, 0),
                    "users": lu.get(hid, 0) + lg.get(hid, 0),
                    "cron": cj.get(hid, 0),
                    "packages": pkg.get(hid, 0),
                    "resolver": res.get(hid, 0),
                    "ca_certs": ca.get(hid, 0),
                },
            }
        )
    return out


@router.get("/{host_id}", response_model=HostResponse)
async def get_host(
    host_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Host).where(Host.id == host_id))
    host = result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    memberships = await db.execute(
        select(HostGroupMembership.c.group_id).where(HostGroupMembership.c.host_id == host_id)
    )
    setattr(host, "group_ids", [r[0] for r in memberships.all()])
    return host


@router.put("/{host_id}", response_model=HostResponse)
async def update_host(
    host_id: int,
    body: HostUpdate,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Host).where(Host.id == host_id))
    host = result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    update_data = body.model_dump(exclude_none=True)
    group_ids = update_data.pop("group_ids", None)

    for field, value in update_data.items():
        setattr(host, field, value)

    new_group_ids: list[int] = []
    if group_ids is not None:
        # Determine which memberships are *new* so we only enqueue
        # actions for hosts joining a group, not for existing memberships.
        existing = await db.execute(
            select(HostGroupMembership.c.group_id).where(HostGroupMembership.c.host_id == host_id)
        )
        existing_set = {r[0] for r in existing.all()}
        new_group_ids = [gid for gid in group_ids if gid not in existing_set]

        await db.execute(
            delete(HostGroupMembership).where(HostGroupMembership.c.host_id == host_id)
        )
        if group_ids:
            await db.execute(
                insert(HostGroupMembership),
                [{"host_id": host_id, "group_id": gid} for gid in group_ids],
            )
        await db.flush()

        for gid in new_group_ids:
            await auto_enqueue_for_new_membership(host_id, gid, db, triggered_by_user_id=user.id)

    await db.commit()
    await db.refresh(host)

    memberships = await db.execute(
        select(HostGroupMembership.c.group_id).where(HostGroupMembership.c.host_id == host_id)
    )
    setattr(host, "group_ids", [r[0] for r in memberships.all()])
    return host


@router.get("/{host_id}/dependents")
async def get_host_dependents_endpoint(
    host_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Return rules and hosts entries that reference this host.

    UIs should call this before deleting a host — a non-empty response means
    the delete will fail at the DB level (FK RESTRICT).
    """
    from app.hosts.dependents import get_host_dependents

    host_row = await db.execute(select(Host).where(Host.id == host_id))
    if host_row.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Host not found")
    deps = await get_host_dependents(db, host_id)
    return {
        "rule_ids": deps.rule_ids,
        "hosts_entry_ids": deps.hosts_entry_ids,
    }


@router.delete("/{host_id}", status_code=204)
async def delete_host(
    host_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    from app.hosts.dependents import get_host_dependents

    result = await db.execute(select(Host).where(Host.id == host_id))
    host = result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    deps = await get_host_dependents(db, host_id)
    if not deps.empty:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Host is referenced by firewall rules or /etc/hosts entries",
                "rule_ids": deps.rule_ids,
                "hosts_entry_ids": deps.hosts_entry_ids,
            },
        )

    await db.delete(host)
    await db.commit()


@router.post("/{host_id}/detect-firewall", response_model=HostResponse)
async def detect_firewall(
    host_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    """Detect firewall backend on host via Ansible. Stub for now — returns current state."""
    result = await db.execute(select(Host).where(Host.id == host_id))
    host = result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    # Real detection implemented in T16 (Ansible integration)
    # For now, return current state unchanged
    return host


@router.post("/{host_id}/facts/refresh", status_code=202)
async def refresh_host_facts(
    host_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    from app.tasks.facts import collect_host_facts

    result = await db.execute(select(Host).where(Host.id == host_id))
    host = result.scalar_one_or_none()
    if host is None:
        raise HTTPException(status_code=404, detail="Host not found")
    collect_host_facts.delay(host_id)
    return {"status": "queued"}


@router.get("/{host_id}/current-rules")
async def get_current_rules(
    host_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch current firewall rules from host via SSH."""
    from app.sync.diff import fetch_current_state

    result = await db.execute(select(Host).where(Host.id == host_id))
    host = result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    try:
        rules = await fetch_current_state(host_id, db)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch rules from host: {e}")

    return [
        {
            "action": r.action,
            "protocol": r.protocol,
            "direction": r.direction,
            "source_cidr": r.source_cidr,
            "destination_cidr": r.destination_cidr,
            "port_start": r.port_start,
            "port_end": r.port_end,
            "comment": r.comment,
        }
        for r in rules
    ]


@router.post("/{host_id}/import-rules", status_code=201)
async def import_rules(
    host_id: int,
    body: ImportRulesRequest,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    """Import selected rules from host into a group."""
    result = await db.execute(select(Host).where(Host.id == host_id))
    host = result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    created = []
    for rule_data in body.rules:
        rule = FirewallRule(
            group_id=body.group_id,
            action=rule_data.get("action", "allow"),
            protocol=rule_data.get("protocol", "tcp"),
            direction=rule_data.get("direction", "input"),
            source_cidr=rule_data.get("source_cidr"),
            destination_cidr=rule_data.get("destination_cidr"),
            port_start=rule_data.get("port_start"),
            port_end=rule_data.get("port_end"),
            comment=rule_data.get("comment", f"Imported from {host.hostname}"),
            priority=rule_data.get("priority", 0),
            is_system=False,
        )
        db.add(rule)
        created.append(rule)

    await db.commit()
    return {"imported": len(created), "group_id": body.group_id}
