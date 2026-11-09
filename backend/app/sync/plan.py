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

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Literal

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
# Per-module adapters
#
# Each adapter reads what it needs from the database and returns a
# ``Collect``: a coroutine function that takes an open SSH connection,
# collects the module's current state over it and diffs it against what
# was read. The split is so a preview reads the database first, then
# collects every module at once over one connection (BUG-159) without
# touching the session again.
# ---------------------------------------------------------------------------

Collect = Callable[[Any], Awaitable[list[DiffChange]]]


def _fmt_port(port_start: int | None, port_end: int | None) -> str:
    if not port_start:
        return "any"
    if port_end and port_end != port_start:
        return f"{port_start}-{port_end}"
    return str(port_start)


def _fmt_rule(r) -> str:
    # Mirrors the nftables renderer's _safe_comment: every LabDog-managed rule
    # carries the "Managed by LabDog" prefix on the host, with the user's
    # comment appended when present. Kept in sync with the frontend
    # groups/[id]/sync formatRule.
    port = _fmt_port(r.port_start, r.port_end)
    text = (r.comment or "").strip()
    comment = f"Managed by LabDog: {text}" if text else "Managed by LabDog"
    src = r.source_cidr or "any"
    dst = r.destination_cidr or "any"
    return f"{r.action} {r.protocol} {r.direction} {src} → {dst} port={port} ({comment})"


async def _firewall_plan(host: Host, db: AsyncSession, private_key_pem: str | None) -> Collect:
    from app.rules.desired_state import get_desired_state
    from app.sync.diff import compute_diff, read_firewall_state

    desired, desired_policies = await get_desired_state(
        host.id, db, host_source_ip=host.labdog_source_ip
    )

    async def collect(conn: Any) -> list[DiffChange]:
        # Nothing to read on a host whose backend is not known yet or that
        # has no key, so every desired rule is an add.
        state = await read_firewall_state(host, private_key_pem, conn=conn)
        return _firewall_changes(
            compute_diff(
                state.rules,
                desired,
                current_policies=state.policies,
                desired_policies=desired_policies,
            )
        )

    return collect


def _firewall_changes(diff) -> list[DiffChange]:
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


async def _services_plan(host: Host, db: AsyncSession, private_key_pem: str) -> Collect:
    from app.services.merge import get_effective_services

    desired = await get_effective_services(host.id, db)

    async def collect(conn: Any) -> list[DiffChange]:
        from app.services.collector import collect_service_states
        from app.services.diff import compute_service_diff

        service_names = [s.service_name for s in desired]
        current = await collect_service_states(
            host, None, private_key_pem, service_names, conn=conn
        )
        return _services_changes(compute_service_diff(current, desired))

    return collect


def _services_changes(diff) -> list[DiffChange]:
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


async def _packages_plan(host: Host, db: AsyncSession, private_key_pem: str) -> Collect:
    from app.packages.merge import get_effective_packages

    effective = await get_effective_packages(host.id, db)

    async def collect(conn: Any) -> list[DiffChange]:
        from app.packages.collector import collect_package_states
        from app.packages.diff import compute_diff as compute_package_diff

        desired_dicts = [p.model_dump() for p in effective]
        package_names = [p.package_name for p in effective]
        actual = await collect_package_states(host, None, private_key_pem, package_names, conn=conn)
        return _packages_changes(compute_package_diff(desired_dicts, actual))

    return collect


def _packages_changes(diff) -> list[DiffChange]:
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
    for e in diff.to_hold_change:
        action = "hold" if e.desired_hold else "unhold"
        changes.append(DiffChange(op="update", summary=f"{e.package_name} ({action})"))
    for e in diff.in_sync:
        changes.append(DiffChange(op="unchanged", summary=e.package_name))
    return changes


def _cron_label(key: str) -> str:
    # diff_cron_jobs formats keys as "name|user".
    name, _, user = key.partition("|")
    return f"{name} ({user})" if user else name


async def _cron_plan(host: Host, db: AsyncSession, private_key_pem: str) -> Collect:
    from app.cron.merge import get_effective_cron_jobs

    effective = await get_effective_cron_jobs(host.id, db)

    async def collect(conn: Any) -> list[DiffChange]:
        from app.cron.collector import collect_cron_jobs
        from app.cron.diff import diff_cron_jobs

        desired_dicts = [
            {"name": j.name, "user": j.user, "schedule": j.schedule, "command": j.command}
            for j in effective
        ]
        users = list({j.user for j in effective})
        actual = await collect_cron_jobs(host, None, private_key_pem, users, conn=conn)
        return _cron_changes(diff_cron_jobs(desired_dicts, actual))

    return collect


def _cron_changes(diff) -> list[DiffChange]:
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


async def _hosts_file_plan(host: Host, db: AsyncSession, private_key_pem: str) -> Collect:
    from app.hosts_mgmt.merge import get_effective_hosts_entries

    desired = await get_effective_hosts_entries(host.id, db)

    async def collect(conn: Any) -> list[DiffChange]:
        from app.hosts_mgmt.collector import collect_hosts_file
        from app.hosts_mgmt.diff import compute_hosts_diff

        # No entry applies → /etc/hosts is unmanaged for this host → no-op.
        if not desired:
            return []
        current = await collect_hosts_file(host, None, private_key_pem, conn=conn)
        return _hosts_file_changes(compute_hosts_diff(current, desired))

    return collect


def _hosts_file_changes(diff) -> list[DiffChange]:
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


async def _linux_users_plan(host: Host, db: AsyncSession, private_key_pem: str) -> Collect:
    from app.user_mgmt.merge import get_effective_groups, get_effective_users

    desired_users = await get_effective_users(host.id, db)
    desired_groups = await get_effective_groups(host.id, db)

    async def collect(conn: Any) -> list[DiffChange]:
        from app.user_mgmt.collector import collect_group_states, collect_user_states
        from app.user_mgmt.diff import diff_groups, diff_users

        desired_user_dicts = [u.model_dump() for u in desired_users]
        desired_group_dicts = [g.model_dump() for g in desired_groups]
        usernames = [u.username for u in desired_users]
        groupnames = [g.groupname for g in desired_groups]
        actual_users, actual_groups = await asyncio.gather(
            collect_user_states(host, None, private_key_pem, usernames, conn=conn),
            collect_group_states(host, None, private_key_pem, groupnames, conn=conn),
        )
        return _linux_users_changes(
            diff_users(desired_user_dicts, actual_users),
            diff_groups(desired_group_dicts, actual_groups),
        )

    return collect


def _linux_users_changes(user_diff, group_diff) -> list[DiffChange]:
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


async def _resolver_plan(host: Host, db: AsyncSession, private_key_pem: str) -> Collect:
    from app.resolver.merge import get_effective_resolver

    effective = await get_effective_resolver(host.id, db)

    async def collect(conn: Any) -> list[DiffChange]:
        from app.resolver.collector import collect_resolver_state
        from app.resolver.diff import compute_resolver_diff

        # No resolver config applies → DNS is unmanaged for this host → no-op.
        if not effective:
            return []
        actual = await collect_resolver_state(
            host, None, private_key_pem, effective.resolver_type, conn=conn
        )
        desired = {
            "nameservers": effective.nameservers,
            "search_domains": effective.search_domains,
            "options": effective.options,
        }
        return _resolver_changes(compute_resolver_diff(actual, desired))

    return collect


def _resolver_changes(diff) -> list[DiffChange]:
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


_MODULE_PLANS = {
    "services": _services_plan,
    "packages": _packages_plan,
    "cron": _cron_plan,
    "hosts-file": _hosts_file_plan,
    "linux-users": _linux_users_plan,
    "resolver": _resolver_plan,
}


async def _module_plan(
    module: str, host: Host, db: AsyncSession, private_key_pem: str | None
) -> Collect:
    if module == "firewall":
        # A host without a key still previews its firewall: every desired
        # rule is an add, as before.
        return await _firewall_plan(host, db, private_key_pem)
    if private_key_pem is None:
        raise ValueError("Host has no SSH key assigned")
    plan = _MODULE_PLANS.get(module)
    if plan is None:
        raise ValueError(f"Unknown module: {module}")
    return await plan(host, db, private_key_pem)


class HostPlan:
    """A preview whose database reads are done and whose SSH work is not.

    :func:`prepare_host_plan` makes one; :meth:`collect` finishes it. The
    caller can hand its session's connection back in between (BUG-153),
    since collecting uses the session only to record the host key the
    first time the host is reached (TOFU).
    """

    def __init__(
        self,
        host: Host,
        private_key_pem: str | None,
        plans: list[tuple[str, Collect | Exception]],
    ):
        self.host = host
        self.private_key_pem = private_key_pem
        self.plans = plans

    async def collect(self, db: AsyncSession | None) -> list[ModuleDiff]:
        """Collect every module at once over one SSH connection (BUG-159).

        The connection is opened by the first module that has something
        to read, so a preview with nothing to read never connects. A module
        whose collection fails gets ``error`` set; the others still finish.
        """
        from app.ssh_utils import shared_host_connection

        async def _one(module: str, plan: Collect | Exception, conn: Any) -> ModuleDiff:
            try:
                if isinstance(plan, Exception):
                    raise plan
                changes = await plan(conn)
            except Exception as exc:  # noqa: BLE001 — surface per-module, keep going
                return ModuleDiff(module=module, has_changes=False, error=str(exc), changes=[])
            return ModuleDiff(module=module, has_changes=_has_changes(changes), changes=changes)

        async with shared_host_connection(self.host, db, self.private_key_pem) as conn:
            return list(await asyncio.gather(*(_one(m, p, conn) for m, p in self.plans)))


async def prepare_host_plan(
    host_id: int,
    module_filter: list[str] | None,
    db: AsyncSession,
) -> HostPlan:
    """Read everything a preview needs from the database.

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

    private_key_pem: str | None = None
    if host.ssh_key_id is not None:
        ssh_key = (
            await db.execute(select(SSHKey).where(SSHKey.id == host.ssh_key_id))
        ).scalar_one_or_none()
        if ssh_key is not None:
            private_key_pem = decrypt_ssh_key(ssh_key.encrypted_private_key, get_master_key())

    plans: list[tuple[str, Collect | Exception]] = []
    for module in modules:
        try:
            plans.append((module, await _module_plan(module, host, db, private_key_pem)))
        except Exception as exc:  # noqa: BLE001 — surface per-module, keep going
            plans.append((module, exc))
    return HostPlan(host, private_key_pem, plans)


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
    plan = await prepare_host_plan(host_id, module_filter, db)
    return await plan.collect(db)
