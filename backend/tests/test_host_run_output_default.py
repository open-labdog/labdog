"""BUG-90: a host run that writes no output reads back empty.

``action_host_runs.output`` was declared with ``server_default="''"``,
which SQLAlchemy quoted again, so the default was the two characters
``''``. A state collection with nothing to report kept it, and the host
page showed it as a warning after every "collect all".
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

from sqlalchemy import text

from app.models.action_run import ActionHostRun, ActionRun
from tests.conftest import create_host, create_ssh_key

VERSIONS = Path(__file__).resolve().parent.parent / "alembic" / "versions"
TWO_QUOTES = "''"


def _migration(filename: str):
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), VERSIONS / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIGRATION_0040 = _migration("0040_host_run_output_default.py")


async def _host_run(db, **kwargs) -> ActionHostRun:
    ssh = await create_ssh_key(db)
    host = await create_host(db, hostname=f"o-{uuid.uuid4().hex[:8]}", ssh_key_id=ssh.id)
    run = ActionRun(
        action_key="_builtin.collect_state",
        action_version="1.0",
        host_id=host.id,
        parameters={},
        parallelism=1,
        status="succeeded",
    )
    db.add(run)
    await db.flush()
    host_run = ActionHostRun(
        action_run_id=run.id,
        host_id=host.id,
        hostname=host.hostname,
        status="succeeded",
        **kwargs,
    )
    db.add(host_run)
    await db.flush()
    await db.refresh(host_run)
    return host_run


def test_the_model_default_renders_as_an_empty_string():
    assert ActionHostRun.__table__.c.output.server_default.arg == ""


async def test_the_database_default_is_an_empty_string(db):
    default = (
        await db.execute(
            text(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_name = 'action_host_runs' AND column_name = 'output'"
            )
        )
    ).scalar_one()
    assert default == "''::text"


async def test_a_host_run_finished_without_output_reads_back_empty(db):
    host_run = await _host_run(db)
    assert host_run.output == ""


async def test_the_rewrite_empties_only_the_two_quote_rows(db):
    stale = await _host_run(db, output=TWO_QUOTES)
    real = await _host_run(db, output="PLAY [labdog] ***\nok: [node-1]\n")
    quoted = await _host_run(db, output="changed: ''\n")

    conn = await db.connection()
    await conn.exec_driver_sql(MIGRATION_0040.REWRITE)
    for row in (stale, real, quoted):
        await db.refresh(row)

    assert stale.output == ""
    assert real.output == "PLAY [labdog] ***\nok: [node-1]\n"
    assert quoted.output == "changed: ''\n"
