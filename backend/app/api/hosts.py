import asyncssh
from fastapi import APIRouter, Depends, HTTPException
from app.ssh_utils import ssh_connect
from pydantic import BaseModel
from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto.encryption import decrypt_ssh_key
from app.crypto.key_management import get_master_key
from app.db import get_db
from app.models.host import Host, HostGroupMembership
from app.models.firewall_rule import FirewallRule
from app.models.ssh_key import SSHKey
from app.models.user import User
from app.auth.users import current_active_user, current_superuser
from app.schemas.hosts import HostCreate, HostUpdate, HostResponse


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
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    hostname = body.hostname

    # Resolve SSH user from key (if provided), falling back to body.ssh_user
    ssh_user = body.ssh_user
    ssh_key = None
    if body.ssh_key_id:
        key_result = await db.execute(
            select(SSHKey).where(SSHKey.id == body.ssh_key_id)
        )
        ssh_key = key_result.scalar_one_or_none()
        if ssh_key:
            ssh_user = ssh_key.ssh_user

    # If hostname is empty, try to fetch it from the host via SSH
    source_ip = None
    if not hostname and ssh_key:
        try:
            master_key = get_master_key()
            private_pem = decrypt_ssh_key(
                ssh_key.encrypted_private_key, master_key
            )
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

    await db.commit()
    await db.refresh(host)
    return host


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
        select(HostGroupMembership.c.group_id).where(
            HostGroupMembership.c.host_id == host_id
        )
    )
    setattr(host, "group_ids", [r[0] for r in memberships.all()])
    return host


@router.put("/{host_id}", response_model=HostResponse)
async def update_host(
    host_id: int,
    body: HostUpdate,
    _: User = Depends(current_superuser),
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

    if group_ids is not None:
        await db.execute(
            delete(HostGroupMembership).where(HostGroupMembership.c.host_id == host_id)
        )
        if group_ids:
            await db.execute(
                insert(HostGroupMembership),
                [{"host_id": host_id, "group_id": gid} for gid in group_ids],
            )

    await db.commit()
    await db.refresh(host)

    memberships = await db.execute(
        select(HostGroupMembership.c.group_id).where(
            HostGroupMembership.c.host_id == host_id
        )
    )
    setattr(host, "group_ids", [r[0] for r in memberships.all()])
    return host


@router.delete("/{host_id}", status_code=204)
async def delete_host(
    host_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Host).where(Host.id == host_id))
    host = result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

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
