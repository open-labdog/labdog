"""Tests for the drift-sample metrics recorder instrumentation.

Exercises the primary drift-check path (``app.tasks.drift._check_drift_for_one_host``,
the periodic-sweep / built-in ``drift_check`` action body) against a seeded
host and asserts exactly one ``drift_samples`` row is written, with counts
that match the drift diff, atomically with the ``HostModuleStatus`` write
(same session, same transaction -- no extra ``db.commit()``/session needed).
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.drift.detector import DriftResult
from app.models.drift_sample import DriftSample
from app.models.host import FirewallBackend, SyncStatus
from app.models.host_module_status import HostModuleStatus
from app.rules.model import ChainPolicies
from app.sync.diff import RulesetDiff

from .conftest import create_host

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_records_one_drift_sample_atomically_with_host_module_status(
    db: AsyncSession,
):
    from app.tasks.drift import _check_drift_for_one_host

    host = await create_host(db)
    host.firewall_backend = FirewallBackend.nftables
    await db.flush()

    diff = RulesetDiff(
        rules_to_add=["rule-a", "rule-b"],
        rules_to_remove=["rule-c"],
        policy_changes={"input": ("accept", "drop")},
    )
    drift_result = DriftResult(host_id=host.id, status=SyncStatus.out_of_sync, diff=diff)

    with (
        patch(
            "app.api.drift._get_desired_state_for_host",
            new=AsyncMock(return_value=([], ChainPolicies())),
        ),
        patch(
            "app.sync.diff.fetch_current_firewall_state",
            new=AsyncMock(return_value=AsyncMock(rules=[], policies=ChainPolicies())),
        ),
        patch(
            "app.drift.detector.check_drift",
            new=AsyncMock(return_value=drift_result),
        ),
    ):
        ran = await _check_drift_for_one_host(host, db)

    assert ran is True

    samples = (
        (await db.execute(select(DriftSample).where(DriftSample.host_id == host.id)))
        .scalars()
        .all()
    )
    assert len(samples) == 1
    sample = samples[0]
    assert sample.module_type == "firewall"
    assert sample.status == "out_of_sync"
    assert sample.add_count == 2
    assert sample.remove_count == 1
    assert sample.policy_change_count == 1

    hms = (
        await db.execute(
            select(HostModuleStatus).where(
                HostModuleStatus.host_id == host.id,
                HostModuleStatus.module_type == "firewall",
            )
        )
    ).scalar_one()
    assert hms.sync_status == "out_of_sync"


@pytest.mark.asyncio
async def test_no_sample_recorded_when_backend_unknown(db: AsyncSession):
    """Firewall-backend-unknown hosts are skipped entirely (no diff computed)."""
    from app.tasks.drift import _check_drift_for_one_host

    host = await create_host(db)
    assert host.firewall_backend == FirewallBackend.unknown
    await db.flush()

    ran = await _check_drift_for_one_host(host, db)
    assert ran is False

    samples = (
        (await db.execute(select(DriftSample).where(DriftSample.host_id == host.id)))
        .scalars()
        .all()
    )
    assert samples == []
