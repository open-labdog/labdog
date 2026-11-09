"""BUG-139: a second identical run is refused while the first is in flight.

The guard was a ``pg_try_advisory_xact_lock`` released at the request's
commit, so it only caught two requests in the same instant; a second one
a moment later started a second run. Its key, ``host_id or group_id``,
also made host 5 and group 5 share a lock.
"""

import uuid

import pytest

from app.models.action_run import ActionRun
from tests.conftest import create_group, create_host

pytestmark = pytest.mark.integration

KEY = "_builtin.collect_state"


async def _run(superuser_client, **target):
    return await superuser_client.post("/api/actions/runs", json={"action_key": KEY, **target})


async def test_a_second_run_on_the_same_host_is_refused(superuser_client, db):
    host = await create_host(db, hostname=f"h-{uuid.uuid4().hex[:8]}")

    first = await _run(superuser_client, host_id=host.id)
    assert first.status_code == 201, first.text
    second = await _run(superuser_client, host_id=host.id)

    assert second.status_code == 409
    assert second.json()["detail"]["running_run_id"] == first.json()["id"]


async def test_a_finished_run_does_not_block_the_next(superuser_client, db):
    host = await create_host(db, hostname=f"h-{uuid.uuid4().hex[:8]}")
    first = await _run(superuser_client, host_id=host.id)
    run = await db.get(ActionRun, first.json()["id"], populate_existing=True)
    run.status = "succeeded"
    await db.commit()

    second = await _run(superuser_client, host_id=host.id)

    assert second.status_code == 201, second.text


async def test_a_host_and_a_group_with_the_same_id_do_not_clash(superuser_client, db):
    group = await create_group(db, name=f"g-{uuid.uuid4().hex[:8]}")
    host = await create_host(db, hostname=f"h-{uuid.uuid4().hex[:8]}")
    # Whatever ids they got, a host run must not count against the group.
    first = await _run(superuser_client, host_id=host.id)
    assert first.status_code == 201, first.text

    group_run = await _run(superuser_client, group_id=group.id)

    assert group_run.status_code == 201, group_run.text
