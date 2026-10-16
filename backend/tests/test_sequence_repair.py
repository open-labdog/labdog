"""BUG-84: repairing id sequences that seed data left behind.

The repair SQL is loaded straight from the migrations, which are not
importable by name, and run against a probe table inside the test
transaction. The table rolls back with it. ``setval`` does not roll back,
but both repairs only advance a sequence that is behind its own table's
``max(id)``, which on a freshly migrated test database moves nothing
other tests rely on.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

VERSIONS = Path(__file__).resolve().parent.parent / "alembic" / "versions"
PROBE = "public.bug84_probe"


def _repair_sql(filename: str) -> str:
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), VERSIONS / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.REPAIR


REPAIR_0018 = _repair_sql("0018_repair_id_sequences.py")
REPAIR_0039 = _repair_sql("0039_repair_uncalled_sequences.py")


async def _sql(db, statement: str):
    conn = await db.connection()
    return await conn.exec_driver_sql(statement)


async def _probe(db, *, seeded: list[int], called_first: bool = False) -> None:
    """A serial table whose rows were inserted with explicit ids, the way
    0001 seeds, which leaves the sequence where it was."""
    await _sql(db, f"CREATE TABLE {PROBE} (id serial PRIMARY KEY)")
    if called_first:
        await _sql(db, f"INSERT INTO {PROBE} DEFAULT VALUES")
    for row_id in seeded:
        await _sql(db, f"INSERT INTO {PROBE} (id) VALUES ({row_id})")


async def _next_id(db) -> int:
    result = await _sql(db, f"SELECT nextval(pg_get_serial_sequence('{PROBE}', 'id'))")
    return result.scalar_one()


async def test_0018_skips_a_never_called_sequence_at_one_seeded_row(db):
    """The premise: this is the state 0001 left git_repositories and
    action_packs in, and 0018 walks past it."""
    await _probe(db, seeded=[1])
    await _sql(db, REPAIR_0018)
    assert await _next_id(db) == 1  # the seeded row's id: a duplicate key


@pytest.mark.parametrize(
    ("seeded", "called_first", "expected"),
    [
        pytest.param([1], False, 2, id="never-called-one-seeded-row"),
        pytest.param([1, 2, 3], False, 4, id="never-called-several-seeded-rows"),
        pytest.param([2, 3], True, 4, id="called-but-behind"),
    ],
)
async def test_0039_advances_a_sequence_whose_next_id_is_taken(db, seeded, called_first, expected):
    await _probe(db, seeded=seeded, called_first=called_first)
    await _sql(db, REPAIR_0039)
    assert await _next_id(db) == expected


async def test_0039_leaves_a_healthy_sequence_alone(db):
    await _probe(db, seeded=[])
    for _ in range(2):
        await _sql(db, f"INSERT INTO {PROBE} DEFAULT VALUES")
    await _sql(db, REPAIR_0039)
    assert await _next_id(db) == 3


async def test_0039_leaves_an_empty_table_alone(db):
    await _probe(db, seeded=[])
    await _sql(db, REPAIR_0039)
    assert await _next_id(db) == 1


async def test_0039_never_rewinds_a_sequence_that_is_ahead(db):
    await _probe(db, seeded=[1])
    await _sql(db, f"SELECT setval(pg_get_serial_sequence('{PROBE}', 'id'), 100)")
    await _sql(db, REPAIR_0039)
    assert await _next_id(db) == 101


async def test_0039_is_idempotent(db):
    await _probe(db, seeded=[1])
    await _sql(db, REPAIR_0039)
    await _sql(db, REPAIR_0039)
    assert await _next_id(db) == 2
