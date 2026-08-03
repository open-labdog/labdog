"""Integration tests for the dashboard charts read API.

Tests /api/dashboard/sync-success-rate and /api/dashboard/drift-trend.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.drift_sample import DriftSample
from app.models.sync_job import JobStatus, SyncJob

from .conftest import create_host

pytestmark = pytest.mark.integration


class TestSyncSuccessRateEndpoint:
    @pytest.mark.asyncio
    async def test_empty_db_returns_empty_points(self, superuser_client):
        resp = await superuser_client.get("/api/dashboard/sync-success-rate")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["points"] == []
        assert data["granularity"] == "day"
        assert "since" in data

    @pytest.mark.asyncio
    async def test_returns_bucketed_points(self, superuser_client, db: AsyncSession):
        host = await create_host(db)
        db.add(
            SyncJob(
                host_id=host.id,
                status=JobStatus.success,
                module_type="firewall",
                created_at=datetime.now(UTC),
            )
        )
        db.add(
            SyncJob(
                host_id=host.id,
                status=JobStatus.failed,
                module_type="firewall",
                created_at=datetime.now(UTC),
            )
        )
        await db.flush()

        resp = await superuser_client.get("/api/dashboard/sync-success-rate")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert len(data["points"]) == 1
        point = data["points"][0]
        assert point["total"] == 2
        assert point["success"] == 1
        assert point["failed"] == 1
        assert point["success_rate"] == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_days_out_of_range_is_422(self, superuser_client):
        resp = await superuser_client.get("/api/dashboard/sync-success-rate?days=0")
        assert resp.status_code == 422, resp.text

        resp = await superuser_client.get("/api/dashboard/sync-success-rate?days=91")
        assert resp.status_code == 422, resp.text

    @pytest.mark.asyncio
    async def test_requires_auth(self, client):
        resp = await client.get("/api/dashboard/sync-success-rate")
        assert resp.status_code == 401


class TestDriftTrendEndpoint:
    @pytest.mark.asyncio
    async def test_empty_db_returns_empty_points(self, superuser_client):
        resp = await superuser_client.get("/api/dashboard/drift-trend")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["points"] == []

    @pytest.mark.asyncio
    async def test_returns_bucketed_points(self, superuser_client, db: AsyncSession):
        host = await create_host(db)
        db.add(
            DriftSample(
                host_id=host.id,
                module_type="firewall",
                status="out_of_sync",
                add_count=2,
                remove_count=1,
                policy_change_count=0,
                checked_at=datetime.now(UTC),
            )
        )
        await db.flush()

        resp = await superuser_client.get("/api/dashboard/drift-trend")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert len(data["points"]) == 1
        point = data["points"][0]
        assert point["checks"] == 1
        assert point["drifted_checks"] == 1
        assert point["total_drift"] == 3

    @pytest.mark.asyncio
    async def test_days_out_of_range_is_422(self, superuser_client):
        resp = await superuser_client.get("/api/dashboard/drift-trend?days=0")
        assert resp.status_code == 422, resp.text

        resp = await superuser_client.get("/api/dashboard/drift-trend?days=91")
        assert resp.status_code == 422, resp.text

    @pytest.mark.asyncio
    async def test_granularity_and_module_query_params(self, superuser_client, db: AsyncSession):
        host = await create_host(db)
        db.add(
            DriftSample(
                host_id=host.id,
                module_type="package",
                status="in_sync",
                checked_at=datetime.now(UTC),
            )
        )
        await db.flush()

        resp = await superuser_client.get(
            "/api/dashboard/drift-trend?granularity=hour&module=package"
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["granularity"] == "hour"
        assert len(data["points"]) == 1

        resp = await superuser_client.get(
            "/api/dashboard/drift-trend?granularity=hour&module=firewall"
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["points"] == []
