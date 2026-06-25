"""Normalized multi-module preview ("plan") for a single host.

The firewall module has long had a dedicated plan/preview flow
(``app.api.sync.plan_host`` → ``RulesetDiff``). Every other module
already owns the same building blocks — a collector that fetches the
current on-host state via SSH, a ``get_effective_*`` desired-state
merge, and a diff function — but only exposes them through its own
``drift-check`` endpoint with a module-specific response shape.

This module normalizes all seven modules behind one shape
(``ModuleDiff`` / ``DiffChange``) so the host page can render a single
preview before applying any manual sync — per-module or "sync all".

Read-only: unlike the ``drift-check`` endpoints, ``plan_host_modules``
does NOT write ``HostModuleStatus``; previewing is a pure read.

Each module is run independently; a collection failure for one module
yields a ``ModuleDiff`` with ``error`` set rather than failing the
whole request — mirroring ``app.api.sync.plan_group``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from app.ansible_runtime.outcomes import determine_modules_to_run

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.host import Host

Op = Literal["add", "remove", "update", "unchanged"]


class DiffChange(BaseModel):
    op: Op
    summary: str
    detail: dict | None = None


class ModuleDiff(BaseModel):
    module: str  # canonical name (firewall, services, packages, ...)
    has_changes: bool
    error: str | None = None
    changes: list[DiffChange] = []


def _has_changes(changes: list[DiffChange]) -> bool:
    return any(c.op != "unchanged" for c in changes)


# ---------------------------------------------------------------------------
# Per-module adapters: (current, desired) -> list[DiffChange]
# Each adapter owns its module's collector + desired-state + diff calls.
# ---------------------------------------------------------------------------


def _fmt_port(port_start: int | None, port_end: int | None) -> str:
    if not port_start:
        return "any"
    if port_end and port_end != port_start:
        return f"{port_start}-{port_end}"
    return str(port_start)


def _fmt_rule(r) -> str:
    # Mirrors the frontend firewall renderer (groups/[id]/sync formatRule)
    # and the nftables renderer fallback comment.
    port = _fmt_port(r.port_start, r.port_end)
    comment = r.comment or "Managed by LabDog"
    src = r.source_cidr or "any"
    dst = r.destination_cidr or "any"
    return f"{r.action} {r.protocol} {r.direction} {src} → {dst} port={port} ({comment})"


async def _firewall_changes(host: Host, db: AsyncSession) -> list[DiffChange]:
    from app.rules.desired_state import get_desired_state
    from app.sync.diff import compute_diff, fetch_current_firewall_state

    desired, desired_policies = await get_desired_state(
        host.id, db, host_source_ip=host.labdog_source_ip
    )
    state = await fetch_current_firewall_state(host.id, db)
    diff = compute_diff(
        state.rules,
        desired,
        current_policies=state.policies,
        desired_policies=desired_policies,
    )
    changes: list[DiffChange] = []
    for r in diff.rules_to_add:
        changes.append(DiffChange(op="add", summary=_fmt_rule(r)))
    for r in diff.rules_to_remove:
        changes.append(DiffChange(op="remove", summary=_fmt_rule(r)))
    for chain, (cur, des) in diff.policy_changes.items():
        changes.append(
            DiffChange(
                op="update",
                summary=f"policy {chain}: {cur} → {des}",
                detail={"chain": chain, "current": cur, "desired": des},
            )
        )
    for r in diff.rules_unchanged:
        changes.append(DiffChange(op="unchanged", summary=_fmt_rule(r)))
    return changes


async def _services_changes(
    host: Host, db: AsyncSession, private_key_pem: str, ssh_user: str
) -> list[DiffChange]:
    from app.services.collector import collect_service_states
    from app.services.diff import compute_service_diff
    from app.services.merge import get_effective_services

    desired = await get_effective_services(host.id, db)
    service_names = [s.service_name for s in desired]
    current = await collect_service_states(
        host.ip_address, host.ssh_port, private_key_pem, service_names, ssh_user
    )
    diff = compute_service_diff(current, desired)

    changes: list[DiffChange] = []
    for item in diff.services_to_update:
        cur = f"{item.actual_state}/{'enabled' if item.actual_enabled else 'disabled'}"
        des = f"{item.desired_state}/{'enabled' if item.desired_enabled else 'disabled'}"
        changes.append(
            DiffChange(
                op="update",
                summary=f"{item.service_name}: {cur} → {des}",
                detail={"reason": item.reason},
            )
        )
    for name in diff.services_with_errors:
        changes.append(
            DiffChange(op="update", summary=f"{name}: state unreadable", detail={"reason": "error"})
        )
    for name in diff.services_in_sync:
        changes.append(DiffChange(op="unchanged", summary=name))
    return changes


async def _packages_changes(
    host: Host, db: AsyncSession, private_key_pem: str, ssh_user: str
) -> list[DiffChange]:
    from app.packages.collector import collect_package_states
    from app.packages.diff import compute_diff as compute_package_diff
    from app.packages.merge import get_effective_packages

    effective = await get_effective_packages(host.id, db)
    desired_dicts = [p.model_dump() for p in effective]
    package_names = [p.package_name for p in effective]
    actual = await collect_package_states(
        host.ip_address, host.ssh_port, private_key_pem, package_names, ssh_user
    )
    diff = compute_package_diff(desired_dicts, actual)

    changes: list[DiffChange] = []
    for e in diff.to_install:
        ver = f" {e.desired_version}" if e.desired_version else ""
        changes.append(DiffChange(op="add", summary=f"{e.package_name} (install{ver})"))
    for e in diff.to_remove:
        changes.append(DiffChange(op="remove", summary=f"{e.package_name} (remove)"))
    for e in diff.to_upgrade:
        cur_ver = e.actual_version or "?"
        des_ver = e.desired_version or "latest"
        changes.append(DiffChange(op="update", summary=f"{e.package_name}: {cur_ver} → {des_ver}"))
    for e in diff.in_sync:
        changes.append(DiffChange(op="unchanged", summary=e.package_name))
    return changes


def _cron_label(key: str) -> str:
    # diff_cron_jobs formats keys as "name|user".
    name, _, user = key.partition("|")
    return f"{name} ({user})" if user else name


async def _cron_changes(
    host: Host, db: AsyncSession, private_key_pem: str, ssh_user: str
) -> list[DiffChange]:
    from app.cron.collector import collect_cron_jobs
    from app.cron.diff import diff_cron_jobs
    from app.cron.merge import get_effective_cron_jobs

    effective = await get_effective_cron_jobs(host.id, db)
    desired_dicts = [
        {"name": j.name, "user": j.user, "schedule": j.schedule, "command": j.command}
        for j in effective
    ]
    users = list({j.user for j in effective})
    actual = await collect_cron_jobs(
        host.ip_address, host.ssh_port, private_key_pem, users, ssh_user
    )
    diff = diff_cron_jobs(desired_dicts, actual)

    changes: list[DiffChange] = []
    for k in diff.jobs_to_add:
        changes.append(DiffChange(op="add", summary=_cron_label(k)))
    for k in diff.jobs_to_remove:
        changes.append(DiffChange(op="remove", summary=_cron_label(k)))
    for k in diff.jobs_to_update:
        changes.append(DiffChange(op="update", summary=_cron_label(k)))
    for k in diff.jobs_in_sync:
        changes.append(DiffChange(op="unchanged", summary=_cron_label(k)))
    return changes


def _hosts_entry_label(e) -> str:
    aliases = " ".join(e.aliases) if e.aliases else ""
    return f"{e.ip_address} {e.hostname} {aliases}".strip()


async def _hosts_file_changes(
    host: Host, db: AsyncSession, private_key_pem: str, ssh_user: str
) -> list[DiffChange]:
    from app.hosts_mgmt.collector import collect_hosts_file
    from app.hosts_mgmt.diff import compute_hosts_diff
    from app.hosts_mgmt.merge import get_effective_hosts_entries

    desired = await get_effective_hosts_entries(host.id, db)
    current = await collect_hosts_file(host.ip_address, host.ssh_port, private_key_pem, ssh_user)
    diff = compute_hosts_diff(current, desired)

    changes: list[DiffChange] = []
    for e in diff.entries_to_add:
        changes.append(DiffChange(op="add", summary=_hosts_entry_label(e)))
    for e in diff.entries_to_remove:
        changes.append(DiffChange(op="remove", summary=_hosts_entry_label(e)))
    for e in diff.entries_to_update:
        changes.append(
            DiffChange(op="update", summary=_hosts_entry_label(e), detail={"reason": e.reason})
        )
    for ip in diff.entries_in_sync:
        changes.append(DiffChange(op="unchanged", summary=ip))
    return changes


async def _linux_users_changes(
    host: Host, db: AsyncSession, private_key_pem: str, ssh_user: str
) -> list[DiffChange]:
    from app.user_mgmt.collector import collect_group_states, collect_user_states
    from app.user_mgmt.diff import diff_groups, diff_users
    from app.user_mgmt.merge import get_effective_groups, get_effective_users

    desired_users = await get_effective_users(host.id, db)
    desired_groups = await get_effective_groups(host.id, db)
    desired_user_dicts = [u.model_dump() for u in desired_users]
    desired_group_dicts = [g.model_dump() for g in desired_groups]
    usernames = [u.username for u in desired_users]
    groupnames = [g.groupname for g in desired_groups]

    actual_users = await collect_user_states(
        host.ip_address, host.ssh_port, private_key_pem, usernames, ssh_user
    )
    actual_groups = await collect_group_states(
        host.ip_address, host.ssh_port, private_key_pem, groupnames, ssh_user
    )
    user_diff = diff_users(desired_user_dicts, actual_users)
    group_diff = diff_groups(desired_group_dicts, actual_groups)

    changes: list[DiffChange] = []
    for n in user_diff.users_to_add:
        changes.append(DiffChange(op="add", summary=f"user {n}"))
    for n in user_diff.users_to_remove:
        changes.append(DiffChange(op="remove", summary=f"user {n}"))
    for n in user_diff.users_to_update:
        changes.append(DiffChange(op="update", summary=f"user {n}"))
    for n in group_diff.groups_to_add:
        changes.append(DiffChange(op="add", summary=f"group {n}"))
    for n in group_diff.groups_to_remove:
        changes.append(DiffChange(op="remove", summary=f"group {n}"))
    for n in group_diff.groups_to_update:
        changes.append(DiffChange(op="update", summary=f"group {n}"))
    for n in user_diff.users_in_sync:
        changes.append(DiffChange(op="unchanged", summary=f"user {n}"))
    for n in group_diff.groups_in_sync:
        changes.append(DiffChange(op="unchanged", summary=f"group {n}"))
    return changes


async def _resolver_changes(
    host: Host, db: AsyncSession, private_key_pem: str, ssh_user: str
) -> list[DiffChange]:
    from app.resolver.collector import collect_resolver_state
    from app.resolver.diff import compute_resolver_diff
    from app.resolver.merge import get_effective_resolver

    effective = await get_effective_resolver(host.id, db)
    # No resolver config applies → DNS is unmanaged for this host → no-op.
    if not effective:
        return []

    actual = await collect_resolver_state(
        host.ip_address, host.ssh_port, private_key_pem, effective.resolver_type, ssh_user
    )
    desired = {
        "nameservers": effective.nameservers,
        "search_domains": effective.search_domains,
        "options": effective.options,
    }
    diff = compute_resolver_diff(actual, desired)

    if not diff.has_changes:
        return [DiffChange(op="unchanged", summary="resolver config")]

    changes: list[DiffChange] = []
    cur = diff.current or {}
    des = diff.desired or {}
    if diff.nameservers_changed:
        changes.append(
            DiffChange(
                op="update",
                summary=f"nameservers: {cur.get('nameservers', [])} → {des.get('nameservers', [])}",
            )
        )
    if diff.search_domains_changed:
        changes.append(
            DiffChange(
                op="update",
                summary=(
                    f"search domains: {cur.get('search_domains', [])} → "
                    f"{des.get('search_domains', [])}"
                ),
            )
        )
    if diff.options_changed:
        changes.append(
            DiffChange(
                op="update",
                summary=f"options: {cur.get('options', {})} → {des.get('options', {})}",
            )
        )
    return changes


async def _module_changes(
    module: str, host: Host, db: AsyncSession, private_key_pem: str | None, ssh_user: str
) -> list[DiffChange]:
    if module == "firewall":
        # Firewall owns its SSH-key handling and backend quirks inside
        # fetch_current_firewall_state, so it doesn't need the shared key.
        return await _firewall_changes(host, db)

    if private_key_pem is None:
        raise ValueError("Host has no SSH key assigned")

    if module == "services":
        return await _services_changes(host, db, private_key_pem, ssh_user)
    if module == "packages":
        return await _packages_changes(host, db, private_key_pem, ssh_user)
    if module == "cron":
        return await _cron_changes(host, db, private_key_pem, ssh_user)
    if module == "hosts-file":
        return await _hosts_file_changes(host, db, private_key_pem, ssh_user)
    if module == "linux-users":
        return await _linux_users_changes(host, db, private_key_pem, ssh_user)
    if module == "resolver":
        return await _resolver_changes(host, db, private_key_pem, ssh_user)
    raise ValueError(f"Unknown module: {module}")


async def plan_host_modules(
    host_id: int,
    module_filter: list[str] | None,
    db: AsyncSession,
) -> list[ModuleDiff]:
    """Preview pending changes for one host across the requested modules.

    ``module_filter`` is ``None`` (every module) or a non-empty list of
    canonical module names (see ``CANONICAL_ORDER``). Returns one
    ``ModuleDiff`` per requested module in canonical order. Per-module
    SSH/collection failures are reported via ``ModuleDiff.error`` and do
    not abort the others.

    Raises:
        LookupError: host not found.
        ValueError: invalid ``module_filter`` (empty / unknown module).
    """
    from sqlalchemy import select

    from app.crypto import decrypt_ssh_key, get_master_key
    from app.models.host import Host
    from app.models.ssh_key import SSHKey

    host = (await db.execute(select(Host).where(Host.id == host_id))).scalar_one_or_none()
    if host is None:
        raise LookupError(f"Host {host_id} not found")

    modules = determine_modules_to_run(module_filter)

    # Decrypt the SSH key once for the modules that need it. Firewall
    # handles its own key lookup; the others share this.
    private_key_pem: str | None = None
    ssh_user = "root"
    if host.ssh_key_id is not None:
        ssh_key = (
            await db.execute(select(SSHKey).where(SSHKey.id == host.ssh_key_id))
        ).scalar_one_or_none()
        if ssh_key is not None:
            private_key_pem = decrypt_ssh_key(ssh_key.encrypted_private_key, get_master_key())
            ssh_user = ssh_key.ssh_user

    results: list[ModuleDiff] = []
    for module in modules:
        try:
            changes = await _module_changes(module, host, db, private_key_pem, ssh_user)
            results.append(
                ModuleDiff(module=module, has_changes=_has_changes(changes), changes=changes)
            )
        except Exception as exc:  # noqa: BLE001 — surface per-module, keep going
            results.append(ModuleDiff(module=module, has_changes=False, error=str(exc), changes=[]))
    return results
