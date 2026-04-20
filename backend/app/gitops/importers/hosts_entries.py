"""Hosts entries module GitOps import handler.

YAML list order is informational only.  The rendered ``/etc/hosts`` file is
priority-driven (higher ``priority`` value → earlier in the file), not
YAML-position-driven.  The drift detector therefore compares field tuples, not
list indices.  Aliases are sorted for comparison only — the user-provided order
is preserved in the database for display consistency.
"""

import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.logger import log_action
from app.gitops.importers.firewall import ModuleImportResult
from app.gitops.schema import BarricadeGroupYAML, HostsEntryYAML
from app.hosts_mgmt.models import HostsEntry
from app.models.host import Host
from app.models.host_group import HostGroup

logger = logging.getLogger(__name__)


def _entry_tuple(
    ip_address: str | None,
    hostname: str | None,
    host_ref_id: int | None,
    aliases: list[str],
    comment: str | None,
    priority: int,
) -> tuple:
    """Return a comparable tuple of hosts-entry fields for diffing.

    Aliases are sorted so that reordering the list in YAML does not produce
    a spurious diff.  The DB always stores the original user-provided order.
    """
    return (
        ip_address,
        hostname,
        host_ref_id,
        tuple(sorted(aliases)),
        comment,
        priority,
    )


def _orm_to_tuple(entry: HostsEntry) -> tuple:
    """Extract a comparable tuple from a ``HostsEntry`` ORM instance."""
    return _entry_tuple(
        ip_address=entry.ip_address,
        hostname=entry.hostname,
        host_ref_id=entry.host_ref_id,
        aliases=list(entry.aliases or []),
        comment=entry.comment,
        priority=entry.priority,
    )


def _yaml_to_tuple(entry: HostsEntryYAML) -> tuple:
    """Extract a comparable tuple from a ``HostsEntryYAML`` schema instance."""
    return _entry_tuple(
        ip_address=entry.ip_address,
        hostname=entry.hostname,
        host_ref_id=entry.host_ref_id,
        aliases=list(entry.aliases),
        comment=entry.comment,
        priority=entry.priority,
    )


async def import_hosts_entries(
    group: HostGroup,
    parsed: BarricadeGroupYAML,
    commit_sha: str,
    db: AsyncSession,
) -> ModuleImportResult:
    """Import ``/etc/hosts`` entries from *parsed* YAML into *group*.

    Derives desired entries from ``parsed.hosts_entries``, diffs against
    current non-system group-scoped ``HostsEntry`` rows, replaces them when
    there are changes, and emits a ``gitops.import.hosts_entries`` audit event.

    Missing or ``None`` ``hosts_entries`` section and an empty list both
    trigger wipe semantics — all existing non-system group-scoped rows are
    deleted.

    System entries (``is_system=True``) are never touched by GitOps import,
    mirroring the same filter applied in the firewall handler.

    Does **not** touch ``group.gitops_status`` — that is the dispatcher's
    responsibility.

    Args:
        group: The target ``HostGroup`` ORM instance.
        parsed: Validated ``BarricadeGroupYAML`` from the current commit.
        commit_sha: Full commit SHA string (for audit trail).
        db: Active async database session.

    Returns:
        A :class:`ModuleImportResult` describing what changed (or the error).
    """
    group_id = group.id

    # Build desired list and validate host references.
    desired_entries: list[HostsEntryYAML] = []

    raw_entries = parsed.hosts_entries or []  # None → wipe (same as [])
    if parsed.hosts_entries is None:
        logger.warning(
            "Group %d: YAML has no hosts_entries section — wiping hosts entries",
            group_id,
        )
    elif not parsed.hosts_entries:
        logger.warning(
            "Group %d: YAML has empty hosts_entries list — wiping hosts entries",
            group_id,
        )

    for entry in raw_entries:
        if entry.host_ref_id is not None:
            # Verify the referenced host exists before touching the DB.
            row = await db.execute(
                select(Host.id).where(Host.id == entry.host_ref_id)
            )
            if row.scalar_one_or_none() is None:
                return ModuleImportResult(
                    module="hosts_entries",
                    error_message=(
                        f"Referenced host id {entry.host_ref_id} does not exist"
                    ),
                )
        desired_entries.append(entry)

    # Fetch current non-system group-scoped rows.
    # System entries are never GitOps-managed (same pattern as firewall's is_system filter).
    current_result = await db.execute(
        select(HostsEntry).where(
            HostsEntry.group_id == group_id,
            HostsEntry.is_system == False,  # noqa: E712
        )
    )
    current_entries: list[HostsEntry] = list(current_result.scalars().all())

    # Diff by comparing sets of field tuples.
    current_tuples = {_orm_to_tuple(e) for e in current_entries}
    desired_tuples = {_yaml_to_tuple(e) for e in desired_entries}

    tuples_added = desired_tuples - current_tuples
    tuples_removed = current_tuples - desired_tuples
    tuples_unchanged = current_tuples & desired_tuples

    has_changes = bool(tuples_added or tuples_removed)

    module_result = ModuleImportResult(
        module="hosts_entries",
        added=len(tuples_added),
        removed=len(tuples_removed),
        unchanged=len(tuples_unchanged),
        changed=has_changes,
    )

    if has_changes:
        # Capture before state for audit.
        before_state = {
            "entries": [
                {
                    "ip_address": e.ip_address,
                    "hostname": e.hostname,
                    "host_ref_id": e.host_ref_id,
                    "aliases": list(e.aliases or []),
                    "comment": e.comment,
                    "priority": e.priority,
                }
                for e in current_entries
            ],
            "count": len(current_entries),
        }

        # Delete-and-replace: remove all existing non-system group-scoped rows.
        # The host_ref_id FK uses ondelete="RESTRICT" but that only prevents
        # deletion of a referenced *host* while entries referencing it exist —
        # it does not prevent deletion of the entries themselves.  Safe to
        # delete-and-replace here.
        await db.execute(
            delete(HostsEntry).where(
                HostsEntry.group_id == group_id,
                HostsEntry.is_system == False,  # noqa: E712
            )
        )

        # Insert desired entries in YAML list order (display order).
        # Emission order in /etc/hosts is priority-driven, not insertion order.
        for entry in desired_entries:
            row = HostsEntry(
                group_id=group_id,
                ip_address=entry.ip_address,
                hostname=entry.hostname,
                host_ref_id=entry.host_ref_id,
                aliases=list(entry.aliases),  # preserve user-provided order
                comment=entry.comment,
                priority=entry.priority,
            )
            db.add(row)

        after_state = {
            "entries": [
                {
                    "ip_address": e.ip_address,
                    "hostname": e.hostname,
                    "host_ref_id": e.host_ref_id,
                    "aliases": list(e.aliases),
                    "comment": e.comment,
                    "priority": e.priority,
                }
                for e in desired_entries
            ],
            "count": len(desired_entries),
            "commit_sha": commit_sha,
            "file_path": group.gitops_file_path,
        }

        await log_action(
            db=db,
            action="gitops.import.hosts_entries",
            entity_type="group",
            entity_id=group_id,
            before_state=before_state,
            after_state=after_state,
        )

    logger.info(
        "GitOps hosts_entries import for group %d: +%d -%d =%d (SHA: %s)",
        group_id,
        module_result.added,
        module_result.removed,
        module_result.unchanged,
        commit_sha[:8],
    )

    return module_result
