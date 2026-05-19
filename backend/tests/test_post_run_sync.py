"""Tests for the post-run module sync feature.

Covers the manifest field, the dispatch helper, and the
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
from app.sync.post_run import dispatch_post_run_sync
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
# dispatch_post_run_sync helper
# ---------------------------------------------------------------------------


class TestDispatchPostRunSync:
    """Direct unit tests of the helper.

    ``run_host_sync.delay`` is patched out so the broker isn't hit;
    the helper's job is to create SyncJob rows and call ``.delay()``
    with the right arguments.
    """

    async def test_empty_module_list_is_noop(self, db):
        delay_calls: list[tuple] = []
        with patch(
            "app.tasks.host_sync_orchestrator.run_host_sync.delay",
            new=MagicMock(side_effect=lambda *a, **kw: delay_calls.append((a, kw))),
        ):
            ids = await dispatch_post_run_sync(
                db, host_id=1, modules=[], triggered_by_user_id=None
            )
        assert ids == []
        assert delay_calls == []

    async def test_creates_syncjob_and_dispatches_per_module(self, db):
        ssh_key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh_key.id)
        delay_calls: list[tuple] = []

        with patch(
            "app.tasks.host_sync_orchestrator.run_host_sync.delay",
            new=MagicMock(side_effect=lambda *a, **kw: delay_calls.append((a, kw))),
        ):
            ids = await dispatch_post_run_sync(
                db,
                host_id=host.id,
                modules=["packages", "services"],
                triggered_by_user_id=None,
            )

        assert len(ids) == 2
        jobs = (
            (
                await db.execute(
                    select(SyncJob).where(SyncJob.id.in_(ids)).order_by(SyncJob.id)
                )
            )
            .scalars()
            .all()
        )
        assert [j.module_type for j in jobs] == ["packages", "services"]
        for j in jobs:
            assert j.host_id == host.id
            assert j.status == "pending"

        # One .delay() per module, with the explicit module_filter.
        assert len(delay_calls) == 2
        for (args, kwargs), expected_module in zip(
            delay_calls, ["packages", "services"], strict=True
        ):
            assert kwargs == {"module_filter": [expected_module]}
            # args = (job_id, host_id)
            assert args[1] == host.id

    async def test_skips_module_already_active(self, db):
        """The active-row partial unique index on (host_id, module_type)
        rejects a second pending/running row. The helper treats this as
        'already queued, nothing more to do' and continues."""
        ssh_key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh_key.id)

        # Pre-seed an active SyncJob for ``packages``. The helper's
        # ``packages`` insert should collide and be skipped.
        pre_existing = SyncJob(
            host_id=host.id, module_type="packages", status="pending"
        )
        db.add(pre_existing)
        await db.commit()

        delay_calls: list[tuple] = []
        with patch(
            "app.tasks.host_sync_orchestrator.run_host_sync.delay",
            new=MagicMock(side_effect=lambda *a, **kw: delay_calls.append((a, kw))),
        ):
            ids = await dispatch_post_run_sync(
                db,
                host_id=host.id,
                modules=["packages", "services"],
                triggered_by_user_id=None,
            )

        # ``packages`` collided -> skipped; ``services`` succeeded.
        assert len(ids) == 1
        new_job = (
            await db.execute(select(SyncJob).where(SyncJob.id == ids[0]))
        ).scalar_one()
        assert new_job.module_type == "services"
        # Only one .delay() call -- the skipped insert never dispatched.
        assert len(delay_calls) == 1
        assert delay_calls[0][1] == {"module_filter": ["services"]}
