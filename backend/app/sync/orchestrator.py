"""Coalesced per-host sync orchestrator (v0.2.0).

The orchestrator wires together the building blocks landed in earlier
commits — desired-state queries, fragment adapters, the playbook
composer, the inventory generator, ansible-runner dispatch, and the
outcome aggregator — into a single async function that produces a
unified playbook for a host, runs it once, and reports per-module
outcomes.

Pure-ish: only DB reads, no writes, no Celery decorator. The runner
and the SSH-key decryption function are dependency-injected so unit
tests can substitute stubs without spinning up SSH or ansible-runner.
The Celery task wrapper that handles tmpfs lifecycle, status writes,
and DB persistence lands in a follow-up commit and consumes this
function as its core.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.ansible_runtime.composer import (
    PlaybookFragment,
    compose_playbook,
    fragment_cron,
    fragment_firewall,
    fragment_hosts_file,
    fragment_linux_users,
    fragment_packages,
    fragment_resolver,
    fragment_services,
)
from app.ansible_runtime.inventory import generate_inventory
from app.ansible_runtime.outcomes import (
    aggregate_module_outcomes,
    determine_modules_to_run,
)
from app.models.host import Host
from app.models.ssh_key import SSHKey

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


# Events emitted by ansible-runner that correspond to per-task outcomes.
# Other event types (playbook_on_*, runner_item_*, etc.) are ignored
# because they don't map to module-level success/failure.
_RELEVANT_EVENT_TYPES = frozenset(
    {
        "runner_on_ok",
        "runner_on_failed",
        "runner_on_unreachable",
        "runner_on_skipped",
    }
)


def _firewall_backend_str(host: Host) -> str:
    """Return the host's firewall backend as a plain string.

    ``host.firewall_backend`` is a ``FirewallBackend`` StrEnum on a fresh
    object and a plain string on a re-loaded one. ``or "nftables"``
    handles the column-default-None edge case (shouldn't happen, but
    cheap guard).
    """
    raw = host.firewall_backend
    if raw is None:
        return "nftables"
    value = raw.value if hasattr(raw, "value") else str(raw)
    return value or "nftables"


def _runner_events_to_task_events(runner_events: Any) -> list[dict[str, Any]]:
    """Convert ansible-runner's event stream into the shape ``aggregate_module_outcomes`` expects.

    Filters to the four task-result event types and projects each into
    a ``{"tags": [...], "failed": bool, "unreachable": bool}`` dict.
    The ``task_tags`` list (injected by ``compose_playbook``) is what
    ties an event back to a module.
    """
    out: list[dict[str, Any]] = []
    for event in runner_events or []:
        et = event.get("event")
        if et not in _RELEVANT_EVENT_TYPES:
            continue
        event_data = event.get("event_data") or {}
        out.append(
            {
                "tags": list(event_data.get("task_tags") or []),
                "failed": et == "runner_on_failed",
                "unreachable": et == "runner_on_unreachable",
            }
        )
    return out


async def orchestrate_host_sync(
    host_id: int,
    module_filter: list[str] | None,
    db: AsyncSession,
    *,
    decrypt_key_fn: Callable[[bytes, bytes], bytes],
    run_ansible_fn: Callable,
    ssh_key_path: str,
    private_data_dir: str,
    timeout: int | None = None,
) -> tuple[dict[str, str], str, str]:
    """Orchestrate a coalesced per-host sync.

    Steps: resolve modules → load Host + SSHKey → decrypt + write SSH
    key → gather desired states + build fragments → compose playbook →
    build inventory → dispatch ansible-runner → aggregate outcomes.

    Args:
        host_id: target host ID.
        module_filter: subset of canonical modules to sync, or ``None`` for all.
        db: async SQLAlchemy session (read-only — orchestrator does not commit).
        decrypt_key_fn: callable ``(encrypted_key, master_key) -> plaintext``.
            Plaintext bytes are written verbatim to ``ssh_key_path`` with mode 0o600.
        run_ansible_fn: same shape as ``app.ansible_runtime.runner.run_ansible``.
        ssh_key_path: caller pre-creates this path; orchestrator writes the
            decrypted key here.
        private_data_dir: ansible-runner work directory; caller manages lifecycle.
        timeout: optional playbook timeout, forwarded to ``run_ansible_fn``.

    Returns:
        Tuple ``(module_outcomes, playbook_yaml, inventory_json)``.
        ``module_outcomes`` maps each module run to ``"in_sync"``,
        ``"error"``, or ``"no_tasks"``. ``playbook_yaml`` and
        ``inventory_json`` are returned verbatim for audit logging.

    Raises:
        LookupError: when the host or its SSH key is not found.
    """
    # 1. Resolve modules to run.
    modules_to_run = determine_modules_to_run(module_filter)

    # 2. Load Host + SSHKey.
    host = (await db.execute(select(Host).where(Host.id == host_id))).scalar_one_or_none()
    if host is None:
        raise LookupError(f"Host {host_id} not found")
    if host.ssh_key_id is None:
        raise LookupError(f"Host {host_id} has no SSH key assigned")
    ssh_key = (
        await db.execute(select(SSHKey).where(SSHKey.id == host.ssh_key_id))
    ).scalar_one_or_none()
    if ssh_key is None:
        raise LookupError(f"SSH key {host.ssh_key_id} for host {host_id} not found")

    # 3. Decrypt + write SSH key with restrictive perms.
    from app.crypto import get_master_key

    master_key = get_master_key()
    plaintext = decrypt_key_fn(ssh_key.encrypted_private_key, master_key)
    if isinstance(plaintext, str):
        plaintext_bytes = plaintext.encode()
    else:
        plaintext_bytes = plaintext
    fd = os.open(ssh_key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, plaintext_bytes)
        if not plaintext_bytes.endswith(b"\n"):
            os.write(fd, b"\n")
    finally:
        os.close(fd)
    # Belt-and-braces: tighten perms in case umask suppressed mode bits.
    os.chmod(ssh_key_path, 0o600)

    # 4. Gather desired states + build fragments. Deferred imports keep
    # the module-level import graph small and match the existing
    # pattern in app/tasks/sync.py.
    fragments: list[PlaybookFragment] = []

    if "firewall" in modules_to_run:
        from app.rules.desired_state import get_desired_state

        rules, policies = await get_desired_state(host_id, db, host_source_ip=host.labdog_source_ip)
        fragments.append(
            fragment_firewall(
                backend=_firewall_backend_str(host),
                rules=rules,
                policies=policies,
            )
        )

    if "services" in modules_to_run:
        from app.services.merge import get_effective_services

        services = await get_effective_services(host_id, db)
        services_dicts = [s.model_dump() for s in services]
        fragments.append(fragment_services(services=services_dicts, ssh_port=host.ssh_port))

    if "packages" in modules_to_run:
        from app.packages.merge import get_effective_packages, get_effective_repos

        pkgs = await get_effective_packages(host_id, db)
        repos = await get_effective_repos(host_id, db)
        pkgs_dicts = [p.model_dump() for p in pkgs]
        repos_dicts = [r.model_dump() for r in repos]
        fragments.append(fragment_packages(packages=pkgs_dicts, repos=repos_dicts))

    if "hosts-file" in modules_to_run:
        from app.hosts_mgmt.merge import get_effective_hosts_entries, render_hosts_file

        entries = await get_effective_hosts_entries(host_id, db)
        rendered = render_hosts_file(entries)
        fragments.append(fragment_hosts_file(rendered_content=rendered, ssh_port=host.ssh_port))

    if "cron" in modules_to_run:
        from app.cron.merge import get_effective_cron_jobs

        jobs = await get_effective_cron_jobs(host_id, db)
        jobs_dicts = [j.model_dump() for j in jobs]
        fragments.append(fragment_cron(cron_jobs=jobs_dicts))

    if "linux-users" in modules_to_run:
        from app.user_mgmt.merge import get_effective_groups, get_effective_users

        users = await get_effective_users(host_id, db)
        groups = await get_effective_groups(host_id, db)
        users_dicts = [u.model_dump() for u in users]
        groups_dicts = [g.model_dump() for g in groups]
        fragments.append(fragment_linux_users(users=users_dicts, groups=groups_dicts))

    if "resolver" in modules_to_run:
        from app.resolver.merge import get_effective_resolver
        from app.resolver.renderer import render_config

        effective = await get_effective_resolver(host_id, db)
        # When no resolver config applies, skip the fragment entirely.
        # The module will surface as "no_tasks" in outcomes, which is
        # the right answer — DNS is unmanaged for this host.
        if effective is not None:
            rendered = render_config(effective)
            fragments.append(
                fragment_resolver(
                    resolver_type=effective.resolver_type,
                    rendered_content=rendered,
                )
            )

    # 5. Compose playbook.
    playbook_yaml = compose_playbook(
        fragments,
        hosts_alias=host.hostname or host.ip_address,
    )

    # 6. Build inventory.
    inventory_json = generate_inventory(
        host_ip=host.ip_address,
        ssh_port=host.ssh_port,
        ssh_key_path=ssh_key_path,
        ssh_user=ssh_key.ssh_user,
        hostname=host.hostname,
    )

    # 7. Dispatch ansible-runner. Caller (the next-commit Celery
    # wrapper) owns private_data_dir lifecycle and tmpfs cleanup.
    runner = run_ansible_fn(
        playbook_yaml=playbook_yaml,
        inventory_json=inventory_json,
        private_data_dir=private_data_dir,
        timeout=timeout,
    )

    # 8. Parse runner events into the shape aggregator expects.
    task_events = _runner_events_to_task_events(getattr(runner, "events", []))
    module_outcomes = aggregate_module_outcomes(task_events, modules_to_run)

    # 9. Return. `inventory_json` is already a JSON string from
    # generate_inventory; we return it verbatim for audit. Parsing it
    # back is the caller's concern.
    return module_outcomes, playbook_yaml, inventory_json
