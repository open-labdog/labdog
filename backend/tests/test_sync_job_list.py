"""BUG-162: listing sync jobs reads three columns of module status and
caps its page size."""

from sqlalchemy import event

from app.models.host_module_status import HostModuleStatus
from app.models.sync_job import JobStatus, SyncJob
from tests.conftest import create_host


async def test_the_module_status_is_read_without_its_state(superuser_client, db):
    host = await create_host(db)
    db.add(SyncJob(host_id=host.id, status=JobStatus.failed, module_type="bulk"))
    db.add(
        HostModuleStatus(
            host_id=host.id,
            module_type="package",
            sync_status="error",
            error_message="dpkg locked",
            collected_state={"packages": [{"name": "x" * 1000}]},
        )
    )
    await db.flush()
    statements: list[str] = []
    engine = db.sync_session.get_bind().engine

    def _record(conn, cursor, statement, *args):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        resp = await superuser_client.get(f"/api/sync/jobs?host_id={host.id}")
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    assert resp.status_code == 200, resp.text
    [job] = resp.json()
    assert job["modules"] == [
        {"module_type": "package", "sync_status": "error", "error_message": "dpkg locked"}
    ]
    module_reads = [s for s in statements if "FROM host_module_status" in s]
    assert module_reads
    assert not any("collected_state" in s or "desired_state" in s for s in module_reads)


async def test_the_page_size_is_capped(superuser_client):
    assert (await superuser_client.get("/api/sync/jobs?limit=500")).status_code == 200
    assert (await superuser_client.get("/api/sync/jobs?limit=501")).status_code == 422
    assert (await superuser_client.get("/api/sync/jobs?limit=0")).status_code == 422
