"""Tests for the post-run module sync feature.

Covers the manifest field, the queueing helper, and the
``ActionDefinitionOut`` API surface. The end-to-end "action runs ->
sync fires" wiring is intentionally not covered here: it would
require the full action_host / action_group pipeline. Lint + the
focused helper tests catch the wiring contract.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.actions.manifest import ActionManifest
from app.models.sync_job import SyncJob
from app.sync.post_run import queue_post_run_sync
from app.tasks.host_sync_orchestrator import module_filter_for
from tests.conftest import create_host, create_ssh_key

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Manifest validation
# ---------------------------------------------------------------------------


class TestManifestPostRunSync:
    """``post_run_sync`` accepts canonical module names, dedupes, rejects
    unknown names."""

    BASE_KWARGS = {
        "key": "demo",
        "name": "Demo",
        "description": "demo",
        "icon": "Box",
        "playbook": "playbook.yml",
        "version": "1.0",
        "estimated_duration": "1 min",
    }

    def test_default_is_empty(self):
        m = ActionManifest(**self.BASE_KWARGS)
        assert m.post_run_sync == []

    def test_accepts_canonical_modules(self):
        m = ActionManifest(**self.BASE_KWARGS, post_run_sync=["packages", "services"])
        assert m.post_run_sync == ["packages", "services"]

    def test_rejects_unknown_module(self):
        with pytest.raises(ValidationError):
            ActionManifest(**self.BASE_KWARGS, post_run_sync=["packages", "not-a-module"])

    def test_dedupes_preserving_order(self):
        m = ActionManifest(
            **self.BASE_KWARGS,
            post_run_sync=["services", "packages", "services", "packages"],
        )
        assert m.post_run_sync == ["services", "packages"]


# ---------------------------------------------------------------------------
# queue_post_run_sync helper
# ---------------------------------------------------------------------------


def _no_sync_dispatch():
    """Fail the test if the helper dispatches anything itself: the rows
    are for the host queue to start once the action lets go of the host
    (BUG-117)."""
    return patch(
        "app.tasks.host_sync_orchestrator.run_host_sync.delay",
        new=MagicMock(side_effect=AssertionError("post-run syncs are queued, not dispatched")),
    )


class TestQueuePostRunSync:
    async def test_empty_module_list_is_noop(self, db):
        with _no_sync_dispatch():
            ids = await queue_post_run_sync(db, host_id=1, modules=[], triggered_by_user_id=None)
        assert ids == []

    async def test_queues_one_row_per_module_as_the_queue_reads_it(self, db):
        """BUG-116: the short name in ``module_type`` and the module in
        ``module_filter`` — what ``module_filter_for`` reads back when the
        queue re-dispatches the row. ``packages`` there read back as every
        module."""
        ssh_key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh_key.id)

        with _no_sync_dispatch():
            ids = await queue_post_run_sync(
                db,
                host_id=host.id,
                modules=["packages", "services", "hosts-file"],
                triggered_by_user_id=None,
            )

        jobs = (
            (await db.execute(select(SyncJob).where(SyncJob.id.in_(ids)).order_by(SyncJob.id)))
            .scalars()
            .all()
        )
        assert [(j.module_type, j.module_filter) for j in jobs] == [
            ("package", ["packages"]),
            ("service", ["services"]),
            ("hosts_file", ["hosts-file"]),
        ]
        for j in jobs:
            assert j.host_id == host.id
            assert j.status == "pending"
            assert module_filter_for(j) == j.module_filter

    async def test_skips_module_already_active(self, db):
        """The active-row partial unique index on (host_id, module_type)
        rejects a second pending/running row — now including an operator's
        own sync of the module, which uses the same short name. The helper
        treats this as 'already queued, nothing more to do' and continues."""
        ssh_key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh_key.id)

        # An operator's packages sync, already waiting on the host.
        db.add(SyncJob(host_id=host.id, module_type="package", status="pending"))
        await db.commit()

        with _no_sync_dispatch():
            ids = await queue_post_run_sync(
                db,
                host_id=host.id,
                modules=["packages", "services"],
                triggered_by_user_id=None,
            )

        assert len(ids) == 1
        new_job = (await db.execute(select(SyncJob).where(SyncJob.id == ids[0]))).scalar_one()
        assert new_job.module_type == "service"

    async def test_skips_a_name_that_is_not_a_module(self, db):
        ssh_key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh_key.id)

        with _no_sync_dispatch():
            ids = await queue_post_run_sync(
                db, host_id=host.id, modules=["ca_cert"], triggered_by_user_id=None
            )

        assert ids == []
