import asyncssh
from celery.result import AsyncResult
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import insert as sa_insert
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_superuser
from app.config import settings
from app.crypto.encryption import decrypt_ssh_key
from app.crypto.key_management import get_master_key
from app.db import get_db
from app.discovery.scanner import validate_cidr
from app.models.host import Host, HostGroupMembership
from app.models.host_group import HostGroup
from app.models.ssh_key import SSHKey
from app.models.user import User
from app.schemas.discovery import (
    BulkAddRequest,
    BulkAddResponse,
    DiscoveredHost,
    FailedHost,
    ScanRequest,
    ScanStatus,
)
from app.schemas.hosts import HostResponse
from app.tasks import celery_app

router = APIRouter(prefix="/discovery", tags=["discovery"])


@router.post("/scan", response_model=ScanStatus)
async def start_scan(
    body: ScanRequest,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    """Start a network discovery scan. Returns job_id for polling."""
    # Validate CIDR
    try:
        network = validate_cidr(body.cidr, settings.discovery.min_prefix)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Get existing host IPs to exclude from scan
    result = await db.execute(select(Host.ip_address))
    existing_ips = [row[0] for row in result.all()]

    # Count total hosts to scan (for progress reporting)
    network_hosts = {str(h) for h in network.hosts()}
    existing_in_range = [ip for ip in existing_ips if ip in network_hosts]
    total = len(network_hosts) - len(existing_in_range)

    # Dispatch Celery task
    task = celery_app.send_task(
        "discovery.scan_network",
        kwargs={
            "cidr": body.cidr,
            "port": body.port,
            "timeout": body.timeout,
            "exclude_ips": existing_ips,
        },
    )

    return ScanStatus(job_id=task.id, status="pending", total=total)


@router.get("/scan/{job_id}", response_model=ScanStatus)
async def get_scan_status(
    job_id: str,
    _: User = Depends(current_superuser),
):
    """Poll scan job status."""
    result = AsyncResult(job_id, app=celery_app)
    state = result.state

    if state == "PENDING":
        return ScanStatus(job_id=job_id, status="pending")
    elif state == "STARTED":
        return ScanStatus(job_id=job_id, status="running")
    elif state == "PROGRESS":
        meta = result.info or {}
        return ScanStatus(
            job_id=job_id,
            status="running",
            progress=meta.get("progress", 0),
            total=meta.get("total", 0),
        )
    elif state == "SUCCESS":
        data = result.result or {}
        hosts_found = [
            DiscoveredHost(ip=h["ip"], hostname=h.get("hostname"), ssh_status=h.get("ssh_status", "open"))
            for h in data.get("hosts_found", [])
        ]
        return ScanStatus(
            job_id=job_id,
            status="done",
            hosts_found=hosts_found,
            total=data.get("total_scanned", 0),
            progress=data.get("total_scanned", 0),
         )
    else:  # FAILURE or other
        error_msg = "Scan failed"
        if result.info and isinstance(result.info, Exception):
            error_msg = str(result.info)
        elif isinstance(result.info, dict):
            error_msg = result.info.get("error", "Scan failed")
        return ScanStatus(job_id=job_id, status="error", error=error_msg)


@router.post("/add-hosts", response_model=BulkAddResponse, status_code=201)
async def add_discovered_hosts(
    body: BulkAddRequest,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    """Bulk-add discovered hosts to Barricade."""
    import socket as _socket

    # Validate request size
    if len(body.ips) > settings.discovery.max_bulk_add:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Too many hosts. Maximum is {settings.discovery.max_bulk_add}, "
                f"got {len(body.ips)}."
            )
        )

    # Validate SSH key exists and load it for hostname detection
    key_result = await db.execute(select(SSHKey).where(SSHKey.id == body.ssh_key_id))
    ssh_key = key_result.scalar_one_or_none()
    if not ssh_key:
        raise HTTPException(status_code=404, detail="SSH key not found")

    # Prepare SSH key for hostname lookups
    ssh_user = ssh_key.ssh_user
    master_key = get_master_key()
    private_pem = decrypt_ssh_key(ssh_key.encrypted_private_key, master_key)
    imported_key = asyncssh.import_private_key(private_pem)

    # Validate all group_ids exist
    for gid in body.group_ids:
        grp_result = await db.execute(select(HostGroup).where(HostGroup.id == gid))
        if not grp_result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail=f"Group {gid} not found")

    # Get existing IPs for duplicate detection
    existing_result = await db.execute(select(Host.ip_address))
    existing_ips = {row[0] for row in existing_result.all()}

    added = []
    skipped = 0
    failed: list[FailedHost] = []

    for ip in body.ips:
        if ip in existing_ips:
            skipped += 1
            continue

        # SSH verification is mandatory — we must be able to connect
        hostname = None
        try:
            async with asyncssh.connect(
                ip,
                port=body.ssh_port,
                username=ssh_user,
                client_keys=[imported_key],
                known_hosts=None,
            ) as conn:
                result = await conn.run("hostname", check=True)
                hostname = result.stdout.strip()
        except Exception as e:
            error_msg = str(e)
            if "Permission denied" in error_msg or "Auth" in error_msg:
                error_msg = f"SSH auth failed for {ssh_user}@{ip}"
            elif "refused" in error_msg.lower():
                error_msg = f"SSH connection refused on {ip}:{body.ssh_port}"
            elif "timed out" in error_msg.lower() or "Timeout" in error_msg:
                error_msg = f"SSH connection timed out for {ip}"
            else:
                error_msg = f"SSH failed: {error_msg[:120]}"
            failed.append(FailedHost(ip=ip, error=error_msg))
            continue

        # Fall back to reverse DNS if SSH returned empty hostname
        if not hostname:
            try:
                fqdn = _socket.getfqdn(ip)
                if fqdn != ip:
                    hostname = fqdn
            except Exception:
                pass

        if not hostname:
            hostname = ip

        # Ensure hostname uniqueness
        base_hostname = hostname
        suffix = 1
        while True:
            hn_result = await db.execute(select(Host).where(Host.hostname == hostname))
            if not hn_result.scalar_one_or_none():
                break
            hostname = f"{base_hostname}-{suffix}"
            suffix += 1

        host = Host(
            hostname=hostname,
            ip_address=ip,
            ssh_port=body.ssh_port,
            ssh_user=ssh_user,
            ssh_key_id=body.ssh_key_id,
        )
        db.add(host)
        await db.flush()  # get host.id

        if body.group_ids:
            await db.execute(
                sa_insert(HostGroupMembership),
                [{"host_id": host.id, "group_id": gid} for gid in body.group_ids],
            )

        existing_ips.add(ip)  # prevent dupe within same request
        added.append(host)

    await db.commit()
    for host in added:
        await db.refresh(host)

    return BulkAddResponse(
        added=len(added),
        skipped=skipped,
        failed=failed,
        hosts=[HostResponse.model_validate(h) for h in added],
    )
