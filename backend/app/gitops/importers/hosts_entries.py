"""Hosts entries module GitOps import handler.

YAML list order is informational only.  The rendered ``/etc/hosts`` file is
priority-driven (higher ``priority`` value → earlier in the file), not
YAML-position-driven.  The drift detector therefore compares field tuples, not
list indices.  Aliases are sorted for comparison only — the user-provided order
is preserved in the database for display consistency.
"""

import logging

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.logger import log_action
from app.gitops.importers._common import (
    ModuleImportResult,
    TupleDiff,
    log_import_summary,
    replace_group_rows,
    warn_if_section_empty,
)
from app.gitops.schema import HostsEntryYAML, LabDogGroupYAML
from app.hosts_mgmt.models import HostsEntry
from app.hosts_mgmt.schemas import HostsEntryCreate
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


def _new_entry(entry: HostsEntryYAML, group_id: int) -> HostsEntry:
    return HostsEntry(
        group_id=group_id,
        ip_address=entry.ip_address,
        hostname=entry.hostname,
        host_ref_id=entry.host_ref_id,
        aliases=list(entry.aliases),  # preserve user-provided order
        comment=entry.comment,
        priority=entry.priority,
    )


def _entry_state(e: HostsEntry) -> dict:
    return {
        "ip_address": e.ip_address,
        "hostname": e.hostname,
        "host_ref_id": e.host_ref_id,
        "aliases": list(e.aliases or []),
        "comment": e.comment,
        "priority": e.priority,
    }


def _yaml_state(e: HostsEntryYAML) -> dict:
    return {
        "ip_address": e.ip_address,
        "hostname": e.hostname,
        "host_ref_id": e.host_ref_id,
        "aliases": list(e.aliases),
        "comment": e.comment,
        "priority": e.priority,
    }


async def import_hosts_entries(
    group: HostGroup,
    parsed: LabDogGroupYAML,
    commit_sha: str,
    db: AsyncSession,
) -> ModuleImportResult:
    """Import ``/etc/hosts`` entries from *parsed* YAML into *group*.

    Derives desired entries from ``parsed.hosts_entries``, diffs against
    current non-system group-scoped ``HostsEntry`` rows, replaces them when
    there are changes, and emits a ``gitops.import.hosts_entries`` audit event.

    Missing or ``None`` ``hosts_entries`` section and an empty list both
    trigger wipe semantics — all existing group-scoped rows are deleted.

    The loopback entries unbork injects are not rows and are therefore not
    at risk here: they are synthesised at merge time from
    ``app.hosts_mgmt.merge.SYSTEM_ENTRIES``.

    Does **not** touch ``group.gitops_status`` — that is the dispatcher's
    responsibility.

    Args:
        group: The target ``HostGroup`` ORM instance.
        parsed: Validated ``LabDogGroupYAML`` from the current commit.
        commit_sha: Full commit SHA string (for audit trail).
        db: Active async database session.

    Returns:
        A :class:`ModuleImportResult` describing what changed (or the error).
    """
    group_id = group.id

    # Build desired list and validate host references.
    desired_entries: list[HostsEntryYAML] = []

    raw_entries = parsed.hosts_entries or []  # None → wipe (same as [])
    warn_if_section_empty(logger, group_id, parsed.hosts_entries, "hosts_entries", "hosts entries")

    for entry in raw_entries:
        # SEC-43: one rule per field. Round-trip through the API's create
        # schema, so a value the API refuses cannot arrive through Git instead.
        try:
            HostsEntryCreate.model_validate(entry.model_dump())
        except ValidationError as exc:
            return ModuleImportResult(
                module="hosts_entries",
                error_message=f"Invalid hosts entry '{entry.hostname or entry.host_ref_id}': {exc}",
            )
        if entry.host_ref_id is not None:
            # Verify the referenced host exists before touching the DB.
            row = await db.execute(select(Host.id).where(Host.id == entry.host_ref_id))
            if row.scalar_one_or_none() is None:
                return ModuleImportResult(
                    module="hosts_entries",
                    error_message=(f"Referenced host id {entry.host_ref_id} does not exist"),
                )
        desired_entries.append(entry)

    current_result = await db.execute(select(HostsEntry).where(HostsEntry.group_id == group_id))
    current_entries: list[HostsEntry] = list(current_result.scalars().all())

    # Diff by comparing sets of field tuples.
    diff = TupleDiff.of(
        {_orm_to_tuple(e) for e in current_entries},
        {_yaml_to_tuple(e) for e in desired_entries},
    )
    module_result = diff.result("hosts_entries")

    if diff.changed:
        # Capture before state for audit.
        before_state = {
            "entries": [_entry_state(e) for e in current_entries],
            "count": len(current_entries),
        }

        # Delete-and-replace: remove all existing non-system group-scoped rows.
        # The host_ref_id FK uses ondelete="RESTRICT" but that only prevents
        # deletion of a referenced *host* while entries referencing it exist —
        # it does not prevent deletion of the entries themselves.  Safe to
        # delete-and-replace here.
        #
        # Desired entries are inserted in YAML list order (display order).
        # Emission order in /etc/hosts is priority-driven, not insertion order.
        await replace_group_rows(db, HostsEntry, group_id, desired_entries, _new_entry)

        after_state = {
            "entries": [_yaml_state(e) for e in desired_entries],
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

    log_import_summary(logger, "hosts_entries", group_id, module_result, commit_sha)

    return module_result
