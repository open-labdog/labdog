import logging

import asyncssh
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.logger import log_action
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
from app.ssh_utils import _format_known_hosts_line, ssh_connect

logger = logging.getLogger(__name__)


async def _load_hosts_resilient(db: AsyncSession) -> list[Host]:
    """Load every Host row, skipping any that fail to materialise.

    Bulk ``scalars().all()`` fails the whole query if a single row has
    an invalid enum value or similar data corruption — that would 500
    the list endpoints and leave the UI blank. Fall back to per-row
    loading only on bulk failure, so the common case stays a single
    query; the diagnostic path pays N+1 once and tells us which rows
    are bad.
    """
    try:
        result = await db.execute(select(Host))
        return list(result.scalars().all())
    except Exception:
        logger.exception(
            "list_hosts: bulk load failed; falling back to per-row. "
            "Look for 'skipping unloadable host' lines below to find the "
            "offending row(s)."
        )

    id_result = await db.execute(select(Host.id).order_by(Host.id))
    ids = [row[0] for row in id_result.all()]
    survivors: list[Host] = []
    for host_id in ids:
        try:
            row_result = await db.execute(select(Host).where(Host.id == host_id))
            survivors.append(row_result.scalar_one())
        except Exception:
            logger.exception("list_hosts: skipping unloadable host id=%d", host_id)
    return survivors


class ImportRulesRequest(BaseModel):
    group_id: int
    rules: list[dict]  # list of rule specs to import


router = APIRouter(prefix="/hosts", tags=["hosts"])


def _enqueue_facts(host_id: int) -> None:
    """Best-effort enqueue of collect_host_facts. Broker outages are silent."""
    try:
        from app.tasks import celery_app  # noqa: PLC0415

        celery_app.send_task("app.tasks.facts.collect_host_facts", args=[host_id])
    except Exception:
        import logging  # noqa: PLC0415

        logging.getLogger(__name__).warning(
            "could not enqueue collect_host_facts for host %d — broker unavailable?",
            host_id,
        )


@router.get("", response_model=list[HostResponse])
async def list_hosts(
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    hosts = await _load_hosts_resilient(db)

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
    captured_host_key_entry: str | None = None
    if not hostname and ssh_key:
        try:
            master_key = get_master_key()
            private_pem = decrypt_ssh_key(ssh_key.encrypted_private_key, master_key)
            imported_key = asyncssh.import_private_key(private_pem)
            # First contact: no Host row yet, so accept any key (TOFU) and
            # capture the server key to persist on the new row at create time.
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
                server_key = conn.get_server_host_key()
                if server_key is not None:
                    captured_host_key_entry = _format_known_hosts_line(body.ip_address, server_key)
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
        labdog_source_ip=source_ip,
        ssh_host_key_entry=captured_host_key_entry,
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

    # Kick off OS-facts collection so os_codename / os_pretty_name are populated
    # by the time the user next lands on the host overview page. Best-effort:
    # a broker hiccup must not fail the create — the next tab load retriggers.
    if host.ssh_key_id is not None:
        _enqueue_facts(host.id)

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

    hosts = await _load_hosts_resilient(db)
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
        try:
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
                    "labdog_source_ip": h.labdog_source_ip,
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
        except Exception:
            # A single malformed row shouldn't blank the whole list. The
            # Host already loaded (otherwise _load_hosts_resilient would
            # have skipped it); this guards against edge cases in value
            # serialisation (datetime tz mismatch, enum.value attribute
            # errors on a corrupt enum instance, etc.).
            logger.exception(
                "list_hosts_summary: skipping host id=%d during response build",
                hid,
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


@router.post("/{host_id}/detect-firewall", status_code=202)
async def detect_firewall(
    host_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    """Re-run host facts collection, which re-probes the firewall backend.

    The facts task writes nftables / iptables / unknown based on which
    binary is present on the host. Returns 202 queued — the updated
    firewall_backend will be visible after the task completes.
    """
    result = await db.execute(select(Host).where(Host.id == host_id))
    host = result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    from app.tasks.facts import collect_host_facts  # noqa: PLC0415

    collect_host_facts.delay(host_id)
    return {"status": "queued"}


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


@router.post("/{host_id}/trust-host-key", status_code=204)
async def trust_host_key(
    host_id: int,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    """Clear the stored SSH host key so the next connection re-TOFUs.

    Use this when a host was legitimately re-keyed (OS reinstall, key
    rotation).  Superuser-only.  Emits an audit log row.
    """
    result = await db.execute(select(Host).where(Host.id == host_id))
    host = result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    host.ssh_host_key_entry = None
    await log_action(
        db,
        action="trust_host_key",
        entity_type="host",
        entity_id=host_id,
        user_id=user.id,
    )
    await db.commit()
