"""API endpoints for reading and refreshing collected host state."""

import asyncio
import logging
from datetime import datetime, timezone

import asyncssh
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_active_user
from app.crypto.encryption import decrypt_ssh_key
from app.crypto.key_management import get_master_key
from app.db import get_db
from app.models.host import Host, SyncStatus
from app.models.host_module_status import HostModuleStatus
from app.models.ssh_key import SSHKey
from app.models.user import User
from app.ssh_utils import get_source_ip, ssh_connect

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/hosts", tags=["host-state"])


class ModuleState(BaseModel):
    module_type: str
    sync_status: str
    collected_state: dict | list | None = None
    collected_at: datetime | None = None
    drift_check_enabled: bool = False
    error_message: str | None = None


@router.get("/{host_id}/current-state", response_model=list[ModuleState])
async def get_current_state(
    host_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Return all cached collected states for a host."""
    result = await db.execute(
        select(HostModuleStatus).where(HostModuleStatus.host_id == host_id)
    )
    statuses = result.scalars().all()
    return [
        ModuleState(
            module_type=hms.module_type,
            sync_status=hms.sync_status,
            collected_state=hms.collected_state,
            collected_at=hms.collected_at,
            drift_check_enabled=hms.drift_check_enabled,
            error_message=hms.error_message,
        )
        for hms in statuses
    ]


@router.post("/{host_id}/collect-state", response_model=list[ModuleState])
async def collect_state(
    host_id: int,
    module: str | None = None,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """SSH into host and collect current state.

    Pass ?module=service to collect a single module, or omit for all.
    """
    host_result = await db.execute(select(Host).where(Host.id == host_id))
    host = host_result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")
    if not host.ssh_key_id:
        raise HTTPException(status_code=400, detail="Host has no SSH key assigned")

    key_result = await db.execute(select(SSHKey).where(SSHKey.id == host.ssh_key_id))
    ssh_key = key_result.scalar_one_or_none()
    if not ssh_key:
        raise HTTPException(status_code=400, detail="SSH key not found")

    private_pem = decrypt_ssh_key(ssh_key.encrypted_private_key, get_master_key())

    all_collectors = _build_collectors(host, private_pem, ssh_key.ssh_user, db)

    if module:
        if module not in all_collectors:
            raise HTTPException(status_code=400, detail=f"Unknown module: {module}")
        collectors = {module: all_collectors[module]}
    else:
        collectors = all_collectors

    now = datetime.now(timezone.utc)
    results: list[ModuleState] = []

    # Connectivity check: probe SSH before running any collectors.
    # If the host is unreachable, mark ALL modules and return immediately.
    try:
        imported_key = asyncssh.import_private_key(private_pem)
        async with ssh_connect(
            host.ip_address, port=host.ssh_port, username=ssh_key.ssh_user,
            client_keys=[imported_key],
        ) as probe:
            host.barricade_source_ip = await get_source_ip(probe)
    except (OSError, asyncssh.Error, asyncio.TimeoutError) as e:
        msg = str(e) or "connection timed out"
        logger.warning("Host %d unreachable, skipping all collectors: %s", host_id, msg)
        error_msg = f"Host unreachable: {msg}"
        host.sync_status = SyncStatus.error
        for module_type in collectors:
            hms = await _get_or_create_hms(db, host_id, module_type)
            hms.collected_at = now
            hms.sync_status = "unknown"
            hms.error_message = error_msg
            results.append(ModuleState(
                module_type=hms.module_type,
                sync_status=hms.sync_status,
                collected_state=hms.collected_state,
                collected_at=hms.collected_at,
                drift_check_enabled=hms.drift_check_enabled,
                error_message=hms.error_message,
            ))
        await db.commit()
        return results

    for module_type, collect_fn in collectors.items():
        hms = await _get_or_create_hms(db, host_id, module_type)
        try:
            state = await collect_fn()
            hms.collected_state = state
            hms.collected_at = now
            hms.error_message = None
        except Exception as e:
            logger.warning("Collection failed for %s on host %d: %s", module_type, host_id, e)
            hms.collected_state = None
            hms.collected_at = now
            hms.sync_status = "error"
            hms.error_message = str(e)

        results.append(ModuleState(
            module_type=hms.module_type,
            sync_status=hms.sync_status,
            collected_state=hms.collected_state,
            collected_at=hms.collected_at,
            drift_check_enabled=hms.drift_check_enabled,
            error_message=hms.error_message,
        ))

    await db.commit()
    return results


def _build_collectors(
    host: Host, private_pem: str, ssh_user: str, db: AsyncSession
) -> dict:
    """Build a dict of module_type -> async collect function."""
    collectors = {}

    async def _collect_services():
        from app.services.collector import list_all_services
        return await list_all_services(
            host.ip_address, host.ssh_port, private_pem,
            ssh_user=ssh_user,
        )

    async def _collect_hosts_file():
        from app.hosts_mgmt.collector import collect_hosts_file
        current = await collect_hosts_file(
            host.ip_address, host.ssh_port, private_pem,
            ssh_user=ssh_user,
        )
        return [
            {"ip_address": e.ip_address, "hostname": e.hostname, "aliases": e.aliases}
            for e in current
        ]

    async def _collect_users():
        import asyncssh as _asyncssh
        private_key = _asyncssh.import_private_key(private_pem)
        async with ssh_connect(
            host.ip_address, port=host.ssh_port, username=ssh_user,
            client_keys=[private_key],
        ) as conn:
            # Collect all real users (uid >= 1000 or uid 0) and groups
            user_result = await conn.run(
                "getent passwd | awk -F: '$3 == 0 || $3 >= 1000 {print $1, $3, $6, $7}'",
                check=False,
            )
            users = []
            for line in (user_result.stdout or "").strip().splitlines():
                parts = line.split()
                if len(parts) >= 4:
                    users.append({
                        "username": parts[0], "uid": int(parts[1]),
                        "home": parts[2], "shell": parts[3],
                    })

            group_result = await conn.run(
                "getent group | awk -F: '$3 == 0 || $3 >= 1000 {print $1, $3}'",
                check=False,
            )
            groups = []
            for line in (group_result.stdout or "").strip().splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    groups.append({"groupname": parts[0], "gid": int(parts[1])})

        return {"users": users, "groups": groups}

    async def _collect_cron():
        import asyncssh as _asyncssh
        private_key = _asyncssh.import_private_key(private_pem)
        async with ssh_connect(
            host.ip_address, port=host.ssh_port, username=ssh_user,
            client_keys=[private_key],
        ) as conn:
            # List all user crontabs
            jobs = []
            users_result = await conn.run(
                "ls /var/spool/cron/crontabs/ 2>/dev/null || ls /var/spool/cron/ 2>/dev/null || echo ''",
                check=False,
            )
            cron_users = [u.strip() for u in (users_result.stdout or "").strip().splitlines() if u.strip()]
            if not cron_users:
                cron_users = ["root"]

            for user in cron_users:
                result = await conn.run(f"crontab -u {user} -l 2>/dev/null || true", check=False)
                for line in (result.stdout or "").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split(maxsplit=5)
                    if len(parts) >= 6:
                        jobs.append({
                            "user": user,
                            "minute": parts[0], "hour": parts[1],
                            "day": parts[2], "month": parts[3],
                            "weekday": parts[4], "command": parts[5],
                        })
            return jobs

    async def _collect_packages():
        from app.packages.collector import collect_package_states, collect_repo_sources
        from app.packages.merge import get_effective_packages
        desired = await get_effective_packages(host.id, db)
        names = [p.package_name for p in desired]
        packages = await collect_package_states(
            host.ip_address, host.ssh_port, private_pem, names,
            ssh_user=ssh_user,
        ) if names else []
        repos = await collect_repo_sources(
            host.ip_address, host.ssh_port, private_pem,
            ssh_user=ssh_user,
        )
        return {"packages": packages, "repos": repos}

    async def _collect_resolver():
        from app.resolver.collector import collect_resolver_state
        from app.resolver.merge import get_effective_resolver
        effective = await get_effective_resolver(host.id, db)
        resolver_type = effective.resolver_type if effective else "resolv_conf"
        return await collect_resolver_state(
            host.ip_address, host.ssh_port, private_pem,
            resolver_type=resolver_type,
            ssh_user=ssh_user,
        )

    async def _collect_firewall():
        from app.sync.collector import collect_current_rules
        from dataclasses import asdict
        backend = (
            host.firewall_backend.value if hasattr(host.firewall_backend, "value")
            else str(host.firewall_backend)
        )
        info_messages: list[str] = []
        if backend == "unknown":
            # Auto-detect firewall backend
            detected, info_messages = await _detect_firewall_backend(
                host.ip_address, host.ssh_port, private_pem, ssh_user,
                host_id=host.id, db=db,
            )
            if detected:
                backend = detected
                host.firewall_backend = detected
            else:
                return {"error": "No supported firewall detected (nftables or iptables)"}
        for msg in info_messages:
            logger.info("Host %d: %s", host.id, msg)
        rules = await collect_current_rules(
            host.ip_address, host.ssh_port, private_pem, backend,
            ssh_user=ssh_user,
        )
        return [asdict(r) for r in rules]

    collectors["firewall"] = _collect_firewall
    collectors["service"] = _collect_services
    collectors["hosts_file"] = _collect_hosts_file
    collectors["linux_user"] = _collect_users
    collectors["cron"] = _collect_cron
    collectors["package"] = _collect_packages
    collectors["resolver"] = _collect_resolver
    return collectors


async def _detect_firewall_backend(
    host_ip: str, ssh_port: int, private_pem: str, ssh_user: str,
    host_id: int, db: AsyncSession,
) -> tuple[str | None, list[str]]:
    """Auto-detect firewall backend by probing for known tools.

    Returns (backend, info_messages) where info_messages are user-facing
    notices about wrapper firewalls (firewalld/ufw) that have been marked
    for disabling.
    """
    from app.packages.models import PackageRule, PackageState
    from app.services.models import ServiceRule, ServiceState

    backend: str | None = None
    messages: list[str] = []

    try:
        key = asyncssh.import_private_key(private_pem)
        async with ssh_connect(
            host_ip, port=ssh_port, username=ssh_user,
            client_keys=[key],
        ) as conn:
            # Check for nftables (nft may be in /usr/sbin which isn't always in PATH)
            r = await conn.run("command -v nft || test -x /usr/sbin/nft", check=False)
            if r.exit_status == 0:
                backend = "nftables"

            # Check for iptables
            if backend is None:
                r = await conn.run("command -v iptables || test -x /usr/sbin/iptables", check=False)
                if r.exit_status == 0:
                    backend = "iptables"

            # Check for firewalld wrapper
            r = await conn.run("command -v firewall-cmd || test -x /usr/sbin/firewall-cmd", check=False)
            if r.exit_status == 0:
                if backend is None:
                    # firewalld uses nft under the hood
                    backend = "nftables"
                messages.append(
                    "firewalld detected but Barricade manages nftables directly. "
                    "firewalld has been marked for disabling."
                )
                # Auto-add package rule to remove firewalld
                existing_pkg = await db.execute(
                    select(PackageRule).where(
                        PackageRule.host_id == host_id,
                        PackageRule.package_name == "firewalld",
                    )
                )
                if not existing_pkg.scalar_one_or_none():
                    db.add(PackageRule(
                        host_id=host_id,
                        package_name="firewalld",
                        state=PackageState.absent,
                        comment="Auto-disabled by Barricade: manages nftables directly",
                    ))
                # Auto-add service rule to stop firewalld
                existing_svc = await db.execute(
                    select(ServiceRule).where(
                        ServiceRule.host_id == host_id,
                        ServiceRule.service_name == "firewalld",
                    )
                )
                if not existing_svc.scalar_one_or_none():
                    db.add(ServiceRule(
                        host_id=host_id,
                        service_name="firewalld",
                        state=ServiceState.stopped,
                        enabled=False,
                        comment="Auto-disabled by Barricade: manages nftables directly",
                    ))

            # Check for ufw wrapper
            r = await conn.run("command -v ufw || test -x /usr/sbin/ufw", check=False)
            if r.exit_status == 0:
                messages.append(
                    "ufw detected but Barricade manages iptables directly. "
                    "ufw has been marked for disabling."
                )
                if backend is None:
                    backend = "iptables"
                # Auto-add package rule to remove ufw
                existing_pkg = await db.execute(
                    select(PackageRule).where(
                        PackageRule.host_id == host_id,
                        PackageRule.package_name == "ufw",
                    )
                )
                if not existing_pkg.scalar_one_or_none():
                    db.add(PackageRule(
                        host_id=host_id,
                        package_name="ufw",
                        state=PackageState.absent,
                        comment="Auto-disabled by Barricade: manages iptables directly",
                    ))
                # Auto-add service rule to stop ufw
                existing_svc = await db.execute(
                    select(ServiceRule).where(
                        ServiceRule.host_id == host_id,
                        ServiceRule.service_name == "ufw",
                    )
                )
                if not existing_svc.scalar_one_or_none():
                    db.add(ServiceRule(
                        host_id=host_id,
                        service_name="ufw",
                        state=ServiceState.stopped,
                        enabled=False,
                        comment="Auto-disabled by Barricade: manages iptables directly",
                    ))

            if messages:
                await db.flush()

    except Exception:
        pass
    return backend, messages


async def _get_or_create_hms(
    db: AsyncSession, host_id: int, module_type: str
) -> HostModuleStatus:
    result = await db.execute(
        select(HostModuleStatus).where(
            HostModuleStatus.host_id == host_id,
            HostModuleStatus.module_type == module_type,
        )
    )
    hms = result.scalar_one_or_none()
    if not hms:
        hms = HostModuleStatus(host_id=host_id, module_type=module_type)
        db.add(hms)
        await db.flush()
    return hms
