"""Discovery scan-config GitOps import handler (list-shaped, wipe semantics).

Imports the ``discovery:`` list from ``_global.yaml`` into ``scan_configs``
rows. List-shaped with **wipe** semantics — same precedent as firewall,
services, packages, etc.:

* ``discovery:`` absent or ``null`` or ``[]`` ⇒ all ``ScanConfig`` rows
  are deleted. Audit event still emitted with the before-state so the
  wipe is recoverable from logs.
* ``discovery:`` present ⇒ delete-and-replace, ordered by YAML position.

The handler resolves human-friendly cross-references at import time:

* ``ssh_key: <name>`` → ``SSHKey.id`` (lookup by ``name``; both columns
  carry a unique constraint)
* ``default_groups: [<name>, ...]`` → ``list[HostGroup.id]``

Unknown names abort the whole import with a clear error so a typo can't
silently delete every scan in the DB.

NOTE: deleting a ``ScanConfig`` cascades to its ``PendingHost`` children.
That's the correct behaviour for a wipe-and-replace shape — the pending
queue belongs to the config that produced it.
"""

import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.logger import log_action
from app.gitops.importers.firewall import ModuleImportResult
from app.gitops.schema import DiscoveryYAML, LabDogGlobalYAML
from app.models.host_group import HostGroup
from app.models.scan_config import ScanConfig
from app.models.ssh_key import SSHKey

logger = logging.getLogger(__name__)


def _scan_snapshot(s: ScanConfig) -> dict:
    """Plain-dict snapshot for audit trail."""
    return {
        "name": s.name,
        "cidrs": list(s.cidrs),
        "ssh_key_id": s.ssh_key_id,
        "ssh_port": s.ssh_port,
        "default_group_ids": list(s.default_group_ids),
        "interval_minutes": s.interval_minutes,
        "cron_expression": s.cron_expression,
        "enabled": s.enabled,
        "auto_add": s.auto_add,
    }


async def _resolve_references(
    desired: list[DiscoveryYAML],
    db: AsyncSession,
) -> tuple[dict[str, int], dict[str, int]] | str:
    """Look up SSH-key and group names referenced by *desired*.

    Returns ``(ssh_key_name_to_id, group_name_to_id)`` on success, or an
    error string when any reference can't be resolved. Bulk-fetched in
    two queries regardless of how many configs are imported.
    """
    ssh_key_names: set[str] = set()
    group_names: set[str] = set()
    for d in desired:
        ssh_key_names.add(d.ssh_key)
        group_names.update(d.default_groups)

    ssh_key_map: dict[str, int] = {}
    if ssh_key_names:
        result = await db.execute(
            select(SSHKey.name, SSHKey.id).where(SSHKey.name.in_(ssh_key_names))
        )
        ssh_key_map = {name: kid for name, kid in result.all()}
        missing = ssh_key_names - set(ssh_key_map)
        if missing:
            return f"Unknown ssh_key reference(s): {sorted(missing)}"

    group_map: dict[str, int] = {}
    if group_names:
        result = await db.execute(
            select(HostGroup.name, HostGroup.id).where(HostGroup.name.in_(group_names))
        )
        group_map = {name: gid for name, gid in result.all()}
        missing = group_names - set(group_map)
        if missing:
            return f"Unknown default_groups reference(s): {sorted(missing)}"

    return ssh_key_map, group_map


async def import_discovery(
    parsed: LabDogGlobalYAML,
    commit_sha: str,
    db: AsyncSession,
) -> ModuleImportResult:
    """Import the global ``discovery:`` list from ``_global.yaml``.

    Wipes existing ``scan_configs`` rows and re-inserts in YAML order.
    Idempotent — a re-import of the identical YAML produces no DB
    mutations and emits no audit event.
    """
    desired_list: list[DiscoveryYAML] = parsed.discovery if parsed.discovery is not None else []

    # Capture current state for diff + audit.
    result = await db.execute(select(ScanConfig).order_by(ScanConfig.id))
    existing_rows = list(result.scalars().all())
    existing_snaps = [_scan_snapshot(s) for s in existing_rows]

    # Resolve cross-references up front; aborts cleanly on typos.
    if desired_list:
        resolved = await _resolve_references(desired_list, db)
        if isinstance(resolved, str):
            return ModuleImportResult(module="discovery", error_message=resolved)
        ssh_key_map, group_map = resolved
    else:
        ssh_key_map, group_map = {}, {}

    # Build desired snapshots in YAML order so we can compare to existing
    # tuples without writing anything if they're identical.
    desired_snaps: list[dict] = []
    for d in desired_list:
        desired_snaps.append(
            {
                "name": d.name,
                "cidrs": list(d.cidrs),
                "ssh_key_id": ssh_key_map[d.ssh_key],
                "ssh_port": d.ssh_port,
                "default_group_ids": [group_map[g] for g in d.default_groups],
                "interval_minutes": d.interval_minutes,
                "cron_expression": d.cron_expression,
                "enabled": d.enabled,
                "auto_add": d.auto_add,
            }
        )

    # Compare existing-vs-desired tuples (order-sensitive on cidrs +
    # default_group_ids, which mirrors the API's behaviour).
    if existing_snaps == desired_snaps:
        logger.info(
            "GitOps discovery import: unchanged (%d config(s), SHA: %s)",
            len(existing_snaps),
            commit_sha[:8],
        )
        return ModuleImportResult(
            module="discovery",
            added=0,
            removed=0,
            unchanged=len(existing_snaps),
            changed=False,
        )

    # Delete-and-replace.
    removed = len(existing_rows)
    if existing_rows:
        await db.execute(delete(ScanConfig))
        await db.flush()

    added = 0
    for snap in desired_snaps:
        new_row = ScanConfig(
            name=snap["name"],
            cidrs=snap["cidrs"],
            ssh_key_id=snap["ssh_key_id"],
            ssh_port=snap["ssh_port"],
            default_group_ids=snap["default_group_ids"],
            interval_minutes=snap["interval_minutes"],
            cron_expression=snap["cron_expression"],
            enabled=snap["enabled"],
            auto_add=snap["auto_add"],
        )
        db.add(new_row)
        added += 1
    await db.flush()

    await log_action(
        db=db,
        action="gitops.import.discovery",
        entity_type="scan_configs",
        entity_id=None,  # Bulk import — no single row id is meaningful.
        before_state={"scan_configs": existing_snaps} if existing_snaps else None,
        after_state={
            "scan_configs": desired_snaps,
            "commit_sha": commit_sha,
        },
    )

    logger.info(
        "GitOps discovery import: +%d -%d (SHA: %s)",
        added,
        removed,
        commit_sha[:8],
    )

    return ModuleImportResult(
        module="discovery",
        added=added,
        removed=removed,
        unchanged=0,
        changed=True,
    )
