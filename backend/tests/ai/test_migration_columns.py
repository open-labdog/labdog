"""Every column a migration renames must actually exist.

Migration 0017 shipped renaming ``ai_sessions.estimated_cost_usd``, a column
that never existed — the name came from a design document rather than the
schema. Nothing caught it until CI ran `alembic upgrade head` against a real
Postgres, because a rename of a non-existent column is valid Python and only
fails at execution.

This reads the rename statements straight out of the migration files and
checks each one against the columns earlier migrations create, so the same
mistake fails in the unit suite instead of after a push. No database needed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"

_CREATE_TABLE = re.compile(r'create_table\(\s*"(\w+)"(.*?)(?=\n    \)|\n\s*op\.)', re.S)
_COLUMN = re.compile(r'sa\.Column\(\s*"(\w+)"')
_ADD_COLUMN = re.compile(r'add_column\(\s*"(\w+)",\s*sa\.Column\(\s*"(\w+)"')
_DROP_COLUMN = re.compile(r'drop_column\(\s*"(\w+)",\s*"(\w+)"\)')
_RENAME = re.compile(r'alter_column\(\s*"(\w+)",\s*"(\w+)",\s*new_column_name="(\w+)"\)')


def _revision_files() -> list[Path]:
    """Migration files in revision order (the numeric filename prefix)."""
    return sorted(VERSIONS.glob("[0-9][0-9][0-9][0-9]_*.py"))


def _upgrade_body(source: str) -> str:
    """Just the upgrade() function — downgrade() reverses these renames, so
    including it would report every rename as backwards."""
    start = source.find("def upgrade()")
    end = source.find("def downgrade()")
    if start == -1:
        return ""
    return source[start : end if end != -1 else len(source)]


def _schema_before(target: Path) -> dict[str, set[str]]:
    """Columns per table as of the migration immediately before ``target``."""
    schema: dict[str, set[str]] = {}
    for path in _revision_files():
        if path.name == target.name:
            break
        body = _upgrade_body(path.read_text())
        for table, block in _CREATE_TABLE.findall(body):
            schema.setdefault(table, set()).update(_COLUMN.findall(block))
        for table, column in _ADD_COLUMN.findall(body):
            schema.setdefault(table, set()).add(column)
        for table, column in _DROP_COLUMN.findall(body):
            schema.get(table, set()).discard(column)
        for table, old, new in _RENAME.findall(body):
            cols = schema.get(table)
            if cols and old in cols:
                cols.discard(old)
                cols.add(new)
    return schema


def _renames() -> list[tuple[str, str, str, str]]:
    out = []
    for path in _revision_files():
        body = _upgrade_body(path.read_text())
        for table, old, new in _RENAME.findall(body):
            out.append((path.name, table, old, new))
    return out


@pytest.mark.parametrize(
    ("migration", "table", "old", "new"),
    [pytest.param(*r, id=f"{r[0]}::{r[1]}.{r[2]}") for r in _renames()]
    or [pytest.param("none", "none", "none", "none", id="no-renames")],
)
def test_renamed_column_exists(migration: str, table: str, old: str, new: str) -> None:
    if migration == "none":
        pytest.skip("no column renames in any migration yet")

    target = VERSIONS / migration
    schema = _schema_before(target)

    assert table in schema, (
        f"{migration} renames a column on {table!r}, but no earlier migration creates that table."
    )
    assert old in schema[table], (
        f"{migration} renames {table}.{old} -> {new}, but {old!r} does not exist "
        f"at that point. Columns available: {sorted(schema[table])}"
    )


def test_the_parser_finds_the_known_renames() -> None:
    """Guard the guard: a regex that silently matches nothing would make every
    assertion above vacuously pass."""
    found = {(table, old) for _, table, old, _ in _renames()}
    assert ("ai_sessions", "cost_usd") in found, (
        "The rename parser matched nothing in 0017 — it has probably drifted "
        "from the migration syntax and is no longer checking anything."
    )
