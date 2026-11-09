"""Pieces every per-module GitOps import handler shares.

The row-list importers (firewall, services, cron jobs, hosts entries,
packages, users) all do the same four things around their own validation:
warn when a section is missing or empty (it wipes the group's rows), diff
the current rows against the YAML as sets of field tuples, delete and
re-insert the group's rows when they differ, and log a one-line summary.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class ModuleImportResult:
    """Result produced by a single per-module import handler.

    Attributes:
        module: Module name (e.g. ``"firewall"``).
        added: Number of rows added.
        removed: Number of rows removed.
        unchanged: Number of rows that were already correct.
        changed: ``True`` when any mutations were performed.
        error_message: Non-``None`` when the handler failed.
    """

    module: str
    added: int = 0
    removed: int = 0
    unchanged: int = 0
    changed: bool = False
    error_message: str | None = None


@dataclass(frozen=True)
class TupleDiff:
    """How the YAML's rows differ from the group's, counted as field tuples."""

    added: int
    removed: int
    unchanged: int

    @classmethod
    def of(cls, current: set[tuple], desired: set[tuple]) -> TupleDiff:
        return cls(
            added=len(desired - current),
            removed=len(current - desired),
            unchanged=len(current & desired),
        )

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed)

    def __add__(self, other: TupleDiff) -> TupleDiff:
        """Two tables imported as one module (packages + repositories, groups
        + users). The sum has changed exactly when either part has."""
        return TupleDiff(
            added=self.added + other.added,
            removed=self.removed + other.removed,
            unchanged=self.unchanged + other.unchanged,
        )

    def result(self, module: str) -> ModuleImportResult:
        return ModuleImportResult(
            module=module,
            added=self.added,
            removed=self.removed,
            unchanged=self.unchanged,
            changed=self.changed,
        )


def warn_if_section_empty(
    log: logging.Logger, group_id: int, value: list | None, section: str, wiped: str
) -> None:
    """Warn that a missing (``None``) or empty YAML section wipes *wiped*."""
    if value is None:
        log.warning("Group %d: YAML has no %s section — wiping %s", group_id, section, wiped)
    elif not value:
        log.warning("Group %d: YAML has empty %s list — wiping %s", group_id, section, wiped)


async def replace_group_rows[EntryT](
    db: AsyncSession,
    model: Any,
    group_id: int,
    entries: Iterable[EntryT],
    build: Callable[[EntryT, int], Any],
) -> None:
    """Delete the group's *model* rows, then add one built row per entry.

    Rows are built and added one at a time, after the delete, in the
    entries' order. Host-level rows are not GitOps-managed and are kept.
    """
    await db.execute(delete(model).where(model.group_id == group_id))
    for entry in entries:
        db.add(build(entry, group_id))


def log_import_summary(
    log: logging.Logger, module: str, group_id: int, result: ModuleImportResult, commit_sha: str
) -> None:
    log.info(
        "GitOps %s import for group %d: +%d -%d =%d (SHA: %s)",
        module,
        group_id,
        result.added,
        result.removed,
        result.unchanged,
        commit_sha[:8],
    )
