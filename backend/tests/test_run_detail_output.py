"""BUG-161: the run detail endpoint does not read any host's output.

``GET /api/actions/runs/{id}`` loaded whole ``ActionHostRun`` rows, output
included (up to 1 MiB each), though ``ActionHostRunOut`` has no output, and
the run page polls it every few seconds while a run is open.
"""

import uuid

from sqlalchemy import event

from app.api.actions import get_run
from app.models.action_run import ActionHostRun, ActionRun
from tests.conftest import create_host


async def test_host_output_is_not_loaded(db):
    host = await create_host(db, hostname=f"h-{uuid.uuid4().hex[:8]}")
    run = ActionRun(
        action_key="pkg.upgrade",
        action_version="1.0",
        host_id=host.id,
        parameters={},
        parallelism=1,
        status="running",
        target_kind="host",
        target_label=host.hostname,
    )
    db.add(run)
    await db.flush()
    db.add(
        ActionHostRun(
            action_run_id=run.id,
            host_id=host.id,
            hostname=host.hostname,
            status="running",
            output="x" * 100_000,
        )
    )
    await db.flush()
    run_id = run.id
    db.expunge_all()

    statements: list[str] = []
    engine = db.sync_session.get_bind().engine

    def _record(conn, cursor, statement, *args):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        out = await get_run(run_id, None, db)
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    assert [hr.status for hr in out.host_runs] == ["running"]
    host_run_reads = [s for s in statements if "FROM action_host_runs" in s]
    assert host_run_reads
    assert not any("action_host_runs.output" in s for s in host_run_reads)
