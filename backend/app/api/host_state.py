"""API endpoints for reading and refreshing collected host state."""

import logging
from collections.abc import Iterable
from datetime import UTC, datetime

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


async def refresh_host_sync_status(host: Host, db: AsyncSession) -> None:
    """Recalculate host.sync_status from its module statuses."""
    result = await db.execute(
        select(HostModuleStatus.sync_status).where(HostModuleStatus.host_id == host.id)
    )
    statuses = {row[0] for row in result.all()}
    if "error" in statuses:
        host.sync_status = SyncStatus.error
    elif "out_of_sync" in statuses or "drifted" in statuses:
        host.sync_status = SyncStatus.out_of_sync
    elif statuses:
        host.sync_status = SyncStatus.in_sync


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
    result = await db.execute(select(HostModuleStatus).where(HostModuleStatus.host_id == host_id))
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

    now = datetime.now(UTC)
    results: list[ModuleState] = []

    # Connectivity check: probe SSH before running any collectors.
    # If the host is unreachable, mark ALL modules and return immediately.
    try:
        imported_key = asyncssh.import_private_key(private_pem)
        async with ssh_connect(
            host.ip_address,
            port=host.ssh_port,
            username=ssh_key.ssh_user,
            client_keys=[imported_key],
        ) as probe:
            host.barricade_source_ip = await get_source_ip(probe)
    except (TimeoutError, OSError, asyncssh.Error) as e:
        msg = str(e) or "connection timed out"
        logger.warning("Host %d unreachable, skipping all collectors: %s", host_id, msg)
        error_msg = f"Host unreachable: {msg}"
        host.sync_status = SyncStatus.error
        for module_type in collectors:
            hms = await _get_or_create_hms(db, host_id, module_type)
            hms.collected_at = now
            hms.sync_status = "unknown"
            hms.error_message = error_msg
            results.append(
                ModuleState(
                    module_type=hms.module_type,
                    sync_status=hms.sync_status,
                    collected_state=hms.collected_state,
                    collected_at=hms.collected_at,
                    drift_check_enabled=hms.drift_check_enabled,
                    error_message=hms.error_message,
                )
            )
        await db.commit()
        return results

    for module_type, collect_fn in collectors.items():
        hms = await _get_or_create_hms(db, host_id, module_type)
        try:
            state = await collect_fn()
            hms.collected_state = state
            hms.collected_at = now
            hms.error_message = None
            hms.sync_status = "collected"
        except Exception as e:
            logger.warning("Collection failed for %s on host %d: %s", module_type, host_id, e)
            hms.collected_state = None
            hms.collected_at = now
            hms.sync_status = "error"
            hms.error_message = str(e)

        results.append(
            ModuleState(
                module_type=hms.module_type,
                sync_status=hms.sync_status,
                collected_state=hms.collected_state,
                collected_at=hms.collected_at,
                drift_check_enabled=hms.drift_check_enabled,
                error_message=hms.error_message,
            )
        )

    # Run inline drift checks on successfully collected modules
    await _run_inline_drift(host, db, collectors.keys())

    # Rebuild results with updated sync_status from drift checks
    results = []
    for module_type in collectors:
        hms = await _get_or_create_hms(db, host_id, module_type)
        results.append(
            ModuleState(
                module_type=hms.module_type,
                sync_status=hms.sync_status,
                collected_state=hms.collected_state,
                collected_at=hms.collected_at,
                drift_check_enabled=hms.drift_check_enabled,
                error_message=hms.error_message,
            )
        )

    # Set host sync_status from module results
    await refresh_host_sync_status(host, db)

    await db.commit()
    return results


async def _run_inline_drift(
    host: Host,
    db: AsyncSession,
    module_types: Iterable[str],
) -> None:
    """Run drift checks using already-collected state (no SSH).

    For each module with collected_state, compare against desired state
    and update hms.sync_status to in_sync/out_of_sync.
    Modules that failed collection (sync_status == "error") are skipped.
    """
    host_id = host.id

    for module_type in module_types:
        hms = await _get_or_create_hms(db, host_id, module_type)
        if hms.sync_status == "error" or hms.collected_state is None:
            continue

        try:
            if module_type == "firewall":
                await _drift_firewall(host, hms, db)
            elif module_type == "service":
                await _drift_service(host_id, hms, db)
            elif module_type == "hosts_file":
                await _drift_hosts_file(host_id, hms, db)
            elif module_type == "linux_user":
                await _drift_linux_user(host_id, hms, db)
            elif module_type == "cron":
                await _drift_cron(host_id, hms, db)
            elif module_type == "package":
                await _drift_package(host_id, hms, db)
            elif module_type == "resolver":
                await _drift_resolver(host_id, hms, db)
        except Exception as exc:
            logger.warning(
                "Inline drift check failed for %s on host %d: %s", module_type, host_id, exc
            )
            # Leave sync_status as-is (from collection) rather than overwriting


async def _drift_firewall(host: Host, hms: HostModuleStatus, db: AsyncSession) -> None:
    from app.api.drift import _get_desired_state_for_host
    from app.rules.model import FirewallRuleSpec
    from app.sync.diff import compute_diff as fw_compute_diff

    desired, desired_policies = await _get_desired_state_for_host(
        host.id,
        db,
        host_source_ip=host.barricade_source_ip,
    )
    if not desired:
        hms.sync_status = "in_sync"
        hms.error_message = None
        return
    current = [
        FirewallRuleSpec(
            **{k: v for k, v in d.items() if k in FirewallRuleSpec.__dataclass_fields__}
        )
        for d in hms.collected_state
    ]
    diff = fw_compute_diff(current, desired, desired_policies=desired_policies)
    hms.sync_status = "in_sync" if not diff.has_changes else "out_of_sync"
    hms.error_message = None


async def _drift_service(host_id: int, hms: HostModuleStatus, db: AsyncSession) -> None:
    from app.services.collector import ServiceCurrentState
    from app.services.diff import compute_service_diff
    from app.services.merge import get_effective_services

    desired = await get_effective_services(host_id, db)
    if not desired:
        hms.sync_status = "in_sync"
        hms.error_message = None
        return

    # collected_state from list_all_services: [{"unit", "active_state", "sub_state", ...}]
    # Build a lookup of service states from the raw systemctl output
    svc_map: dict[str, dict] = {}
    for entry in hms.collected_state:
        name = entry.get("unit") or entry.get("service_name", "")
        svc_map[name] = entry

    current = []
    for svc in desired:
        raw = svc_map.get(svc.service_name)
        if raw:
            # list_all_services format: active_state is "active"/"inactive"/"failed"
            raw_active = raw.get("active_state", "")
            if raw_active == "active" or raw_active == "running":
                active_state = "running"
            else:
                active_state = "stopped"
            enabled = raw.get("enabled", raw.get("sub_state") == "enabled")
        else:
            active_state = "stopped"
            enabled = False
        current.append(
            ServiceCurrentState(
                service_name=svc.service_name,
                active_state=active_state,
                enabled=bool(enabled),
            )
        )

    diff = compute_service_diff(current, desired)
    hms.sync_status = "in_sync" if not diff.has_changes else "out_of_sync"
    hms.error_message = None


async def _drift_hosts_file(host_id: int, hms: HostModuleStatus, db: AsyncSession) -> None:
    from app.hosts_mgmt.collector import ParsedHostsEntry
    from app.hosts_mgmt.diff import compute_hosts_diff
    from app.hosts_mgmt.merge import get_effective_hosts_entries

    desired = await get_effective_hosts_entries(host_id, db)
    if not desired or all(e.is_system for e in desired):
        hms.sync_status = "in_sync"
        hms.error_message = None
        return
    current = [
        ParsedHostsEntry(
            ip_address=e["ip_address"],
            hostname=e["hostname"],
            aliases=e.get("aliases", []),
        )
        for e in hms.collected_state
    ]
    diff = compute_hosts_diff(current, desired)
    hms.sync_status = "in_sync" if not diff.has_changes else "out_of_sync"
    hms.error_message = None


async def _drift_linux_user(host_id: int, hms: HostModuleStatus, db: AsyncSession) -> None:
    from app.user_mgmt.diff import diff_groups, diff_users
    from app.user_mgmt.merge import get_effective_groups, get_effective_users

    desired_users = await get_effective_users(host_id, db)
    desired_groups = await get_effective_groups(host_id, db)
    if not desired_users and not desired_groups:
        hms.sync_status = "in_sync"
        hms.error_message = None
        return

    data = hms.collected_state  # {"users": [...], "groups": [...]}
    actual_users = data.get("users", [])
    actual_groups = data.get("groups", [])

    user_diff = diff_users(
        [u.model_dump() if hasattr(u, "model_dump") else u for u in desired_users],
        actual_users,
    )
    group_diff = diff_groups(
        [g.model_dump() if hasattr(g, "model_dump") else g for g in desired_groups],
        actual_groups,
    )
    users_drifted = bool(
        user_diff.users_to_add or user_diff.users_to_remove or user_diff.users_to_update
    )
    groups_drifted = bool(
        group_diff.groups_to_add or group_diff.groups_to_remove or group_diff.groups_to_update
    )
    hms.sync_status = "in_sync" if not (users_drifted or groups_drifted) else "out_of_sync"
    hms.error_message = None


async def _drift_cron(host_id: int, hms: HostModuleStatus, db: AsyncSession) -> None:
    from app.cron.diff import diff_cron_jobs
    from app.cron.merge import get_effective_cron_jobs

    desired = await get_effective_cron_jobs(host_id, db)
    if not desired:
        hms.sync_status = "in_sync"
        hms.error_message = None
        return
    desired_dicts = [j.model_dump() if hasattr(j, "model_dump") else j for j in desired]
    actual = hms.collected_state  # list of cron job dicts

    diff = diff_cron_jobs(desired_dicts, actual)
    drifted = bool(diff.jobs_to_add or diff.jobs_to_remove or diff.jobs_to_update)
    hms.sync_status = "in_sync" if not drifted else "out_of_sync"
    hms.error_message = None


async def _drift_package(host_id: int, hms: HostModuleStatus, db: AsyncSession) -> None:
    from app.packages.diff import compute_diff as pkg_compute_diff
    from app.packages.merge import get_effective_packages

    effective = await get_effective_packages(host_id, db)
    desired_dicts = [
        {"package_name": p.package_name, "state": p.state, "version": p.version, "hold": p.hold}
        if hasattr(p, "package_name")
        else p
        for p in effective
    ]
    data = hms.collected_state  # {"packages": [...], "repos": [...]}
    actual = data.get("packages", []) if isinstance(data, dict) else data

    diff = pkg_compute_diff(desired_dicts, actual)
    hms.sync_status = "in_sync" if not diff.has_drift else "out_of_sync"
    hms.error_message = None


async def _drift_resolver(host_id: int, hms: HostModuleStatus, db: AsyncSession) -> None:
    from app.resolver.diff import compute_resolver_diff
    from app.resolver.merge import get_effective_resolver

    effective = await get_effective_resolver(host_id, db)
    if not effective:
        hms.sync_status = "in_sync"
        hms.error_message = None
        return
    desired_dict = {
        "nameservers": effective.nameservers if hasattr(effective, "nameservers") else [],
        "search_domains": effective.search_domains if hasattr(effective, "search_domains") else [],
        "options": effective.options if hasattr(effective, "options") else {},
    }
    diff = compute_resolver_diff(hms.collected_state, desired_dict)
    hms.sync_status = "in_sync" if not diff.has_changes else "out_of_sync"
    hms.error_message = None


def _build_collectors(host: Host, private_pem: str, ssh_user: str, db: AsyncSession) -> dict:
    """Build a dict of module_type -> async collect function."""
    collectors = {}

    async def _collect_services():
        from app.services.collector import list_all_services

        return await list_all_services(
            host.ip_address,
            host.ssh_port,
            private_pem,
            ssh_user=ssh_user,
        )

    async def _collect_hosts_file():
        from app.hosts_mgmt.collector import collect_hosts_file

        current = await collect_hosts_file(
            host.ip_address,
            host.ssh_port,
            private_pem,
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
            host.ip_address,
            port=host.ssh_port,
            username=ssh_user,
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
                    users.append(
                        {
                            "username": parts[0],
                            "uid": int(parts[1]),
                            "home": parts[2],
                            "shell": parts[3],
                        }
                    )

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
            host.ip_address,
            port=host.ssh_port,
            username=ssh_user,
            client_keys=[private_key],
        ) as conn:
            # List all user crontabs
            jobs = []
            users_result = await conn.run(
                "ls /var/spool/cron/crontabs/ 2>/dev/null"
                " || ls /var/spool/cron/ 2>/dev/null || echo ''",
                check=False,
            )
            cron_users = [
                u.strip() for u in (users_result.stdout or "").strip().splitlines() if u.strip()
            ]
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
                        jobs.append(
                            {
                                "user": user,
                                "minute": parts[0],
                                "hour": parts[1],
                                "day": parts[2],
                                "month": parts[3],
                                "weekday": parts[4],
                                "command": parts[5],
                            }
                        )
            return jobs

    async def _collect_packages():
        from app.packages.collector import collect_package_states, collect_repo_sources
        from app.packages.merge import get_effective_packages

        desired = await get_effective_packages(host.id, db)
        names = [p.package_name for p in desired]
        packages = (
            await collect_package_states(
                host.ip_address,
                host.ssh_port,
                private_pem,
                names,
                ssh_user=ssh_user,
            )
            if names
            else []
        )
        repos = await collect_repo_sources(
            host.ip_address,
            host.ssh_port,
            private_pem,
            ssh_user=ssh_user,
        )
        return {"packages": packages, "repos": repos}

    async def _collect_resolver():
        from app.resolver.collector import collect_resolver_state
        from app.resolver.merge import get_effective_resolver

        effective = await get_effective_resolver(host.id, db)
        resolver_type = effective.resolver_type if effective else "resolv_conf"
        return await collect_resolver_state(
            host.ip_address,
            host.ssh_port,
            private_pem,
            resolver_type=resolver_type,
            ssh_user=ssh_user,
        )

    async def _collect_firewall():
        from dataclasses import asdict

        from app.sync.collector import collect_current_rules

        backend = (
            host.firewall_backend.value
            if hasattr(host.firewall_backend, "value")
            else str(host.firewall_backend)
        )
        info_messages: list[str] = []
        if backend == "unknown":
            # Auto-detect firewall backend
            detected, info_messages = await _detect_firewall_backend(
                host.ip_address,
                host.ssh_port,
                private_pem,
                ssh_user,
                host_id=host.id,
                db=db,
            )
            if detected:
                backend = detected
                host.firewall_backend = detected
            else:
                return {"error": "No supported firewall detected (nftables or iptables)"}
        for msg in info_messages:
            logger.info("Host %d: %s", host.id, msg)
        rules = await collect_current_rules(
            host.ip_address,
            host.ssh_port,
            private_pem,
            backend,
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
    host_ip: str,
    ssh_port: int,
    private_pem: str,
    ssh_user: str,
    host_id: int,
    db: AsyncSession,
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
            host_ip,
            port=ssh_port,
            username=ssh_user,
            client_keys=[key],
        ) as conn:
            # Check for nftables (nft may be in /usr/sbin which isn't always in PATH)
            r = await conn.run("command -v nft || test -x /usr/sbin/nft", check=False)
            if r.exit_status == 0:
                backend = "nftables"

            # Cached helper: check iptables availability at most once across
            # all container-runtime downgrade checks below.
            iptables_available = None

            async def _check_iptables_available() -> bool:
                nonlocal iptables_available
                if iptables_available is None:
                    r = await conn.run(
                        "command -v iptables || test -x /usr/sbin/iptables",
                        check=False,
                    )
                    iptables_available = r.exit_status == 0
                return iptables_available

            # If Docker is running, prefer iptables — Docker defaults to
            # iptables and its nftables support is experimental (v29+).
            if backend == "nftables":
                r = await conn.run(
                    "test -S /run/docker.sock || systemctl is-active --quiet docker 2>/dev/null",
                    check=False,
                )
                if r.exit_status == 0:
                    if await _check_iptables_available():
                        backend = "iptables"
                        messages.append(
                            "Docker detected; using iptables backend (Docker defaults to iptables)."
                        )

            # If kube-proxy is running in iptables mode, prefer iptables to
            # avoid conflicts with KUBE-* chains it manages.
            if backend == "nftables":
                r = await conn.run(
                    "systemctl is-active --quiet kubelet 2>/dev/null && "
                    "iptables -S 2>/dev/null | grep -q KUBE-",
                    check=False,
                )
                if r.exit_status == 0:
                    if await _check_iptables_available():
                        backend = "iptables"
                        messages.append(
                            "Kubernetes kube-proxy (iptables mode) detected; "
                            "using iptables backend to avoid rule conflicts."
                        )

            # If nerdctl with rootful CNI networking is present, prefer
            # iptables — CNI plugins default to iptables rules.
            if backend == "nftables":
                r = await conn.run(
                    "command -v nerdctl >/dev/null 2>&1 && "
                    "test -d /etc/cni/net.d && "
                    "ls /etc/cni/net.d/nerdctl-*.conflist >/dev/null 2>&1",
                    check=False,
                )
                if r.exit_status == 0:
                    if await _check_iptables_available():
                        backend = "iptables"
                        messages.append(
                            "nerdctl with CNI networking detected; using iptables "
                            "backend (CNI plugins default to iptables)."
                        )

            # Check for iptables
            if backend is None:
                r = await conn.run("command -v iptables || test -x /usr/sbin/iptables", check=False)
                if r.exit_status == 0:
                    backend = "iptables"

            # Check for firewalld wrapper
            r = await conn.run(
                "command -v firewall-cmd || test -x /usr/sbin/firewall-cmd", check=False
            )
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
                    db.add(
                        PackageRule(
                            host_id=host_id,
                            package_name="firewalld",
                            state=PackageState.absent,
                            comment="Auto-disabled by Barricade: manages nftables directly",
                        )
                    )
                # Auto-add service rule to stop firewalld
                existing_svc = await db.execute(
                    select(ServiceRule).where(
                        ServiceRule.host_id == host_id,
                        ServiceRule.service_name == "firewalld",
                    )
                )
                if not existing_svc.scalar_one_or_none():
                    db.add(
                        ServiceRule(
                            host_id=host_id,
                            service_name="firewalld",
                            state=ServiceState.stopped,
                            enabled=False,
                            comment="Auto-disabled by Barricade: manages nftables directly",
                        )
                    )

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
                    db.add(
                        PackageRule(
                            host_id=host_id,
                            package_name="ufw",
                            state=PackageState.absent,
                            comment="Auto-disabled by Barricade: manages iptables directly",
                        )
                    )
                # Auto-add service rule to stop ufw
                existing_svc = await db.execute(
                    select(ServiceRule).where(
                        ServiceRule.host_id == host_id,
                        ServiceRule.service_name == "ufw",
                    )
                )
                if not existing_svc.scalar_one_or_none():
                    db.add(
                        ServiceRule(
                            host_id=host_id,
                            service_name="ufw",
                            state=ServiceState.stopped,
                            enabled=False,
                            comment="Auto-disabled by Barricade: manages iptables directly",
                        )
                    )

            if messages:
                await db.flush()

    except Exception:
        logger.exception("Firewall backend detection failed for host %s", host_ip)
        return None, ["Detection failed — check server logs"]
    return backend, messages


async def _get_or_create_hms(db: AsyncSession, host_id: int, module_type: str) -> HostModuleStatus:
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
