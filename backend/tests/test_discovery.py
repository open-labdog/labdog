"""
Integration tests for the host discovery feature.

Tests cover:
- CIDR validation (pure unit tests)
- Network scanner with mocked asyncio (no real network calls)
- API endpoints (scan, bulk-add) with real DB via testcontainers
- RBAC: non-superuser rejected from all discovery endpoints
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.discovery.scanner import scan_network, validate_cidr
from tests.conftest import create_ssh_key

pytestmark = pytest.mark.integration


class TestCIDRValidation:
    """Pure unit tests for CIDR validation — no DB, no async."""

    def test_valid_private_cidr(self):
        net = validate_cidr("10.0.0.0/24")
        assert str(net) == "10.0.0.0/24"

    def test_valid_192_168_cidr(self):
        net = validate_cidr("192.168.1.0/24")
        assert str(net) == "192.168.1.0/24"

    def test_cidr_too_large_slash8(self):
        with pytest.raises(ValueError, match="too large"):
            validate_cidr("10.0.0.0/8")

    def test_cidr_too_large_slash16(self):
        with pytest.raises(ValueError, match="too large"):
            validate_cidr("10.0.0.0/16")

    def test_blocked_loopback(self):
        with pytest.raises(ValueError, match="blocked"):
            validate_cidr("127.0.0.1/32")

    def test_blocked_link_local(self):
        with pytest.raises(ValueError, match="blocked"):
            validate_cidr("169.254.0.0/24")

    def test_private_ranges_allowed(self):
        # RFC1918 must NOT be blocked
        for cidr in ["10.0.0.0/24", "172.16.0.0/24", "192.168.1.0/24"]:
            validate_cidr(cidr)  # must not raise


class TestScanner:
    """Scanner tests with mocked asyncio connections — no real network calls."""

    async def test_scan_returns_open_ports(self):
        """Mock open_connection to succeed for 2 IPs."""
        mock_writer = MagicMock()
        mock_writer.close = MagicMock()
        mock_writer.wait_closed = AsyncMock(return_value=None)

        with patch(
            "app.discovery.scanner.asyncio.open_connection",
            new=AsyncMock(return_value=(MagicMock(), mock_writer)),
        ):
            results = await scan_network(
                "10.0.0.0/30", port=22, timeout=0.1, max_concurrent=10
            )
        # /30 has 2 usable hosts: 10.0.0.1, 10.0.0.2
        assert len(results) == 2

    async def test_scan_handles_timeout(self):
        """Timed-out hosts return None (not in results)."""
        with patch(
            "app.discovery.scanner.asyncio.open_connection",
            new=AsyncMock(side_effect=asyncio.TimeoutError),
        ):
            results = await scan_network(
                "10.0.0.0/30", port=22, timeout=0.01, max_concurrent=10
            )
        assert results == []

    async def test_connection_refused_excluded(self):
        """ConnectionRefusedError → host NOT in results."""
        with patch(
            "app.discovery.scanner.asyncio.open_connection",
            new=AsyncMock(side_effect=ConnectionRefusedError),
        ):
            results = await scan_network(
                "10.0.0.0/30", port=22, timeout=0.1, max_concurrent=10
            )
        assert results == []

    async def test_connection_reset_included(self):
        """ConnectionResetError → host IS in results (port open, service reset)."""
        with patch(
            "app.discovery.scanner.asyncio.open_connection",
            new=AsyncMock(side_effect=ConnectionResetError),
        ):
            results = await scan_network(
                "10.0.0.0/30", port=22, timeout=0.1, max_concurrent=10
            )
        assert len(results) == 2


class TestDiscoveryAPI:
    """Integration tests for discovery API endpoints with real DB."""

    async def test_scan_endpoint_accepts_valid_cidr(self, superuser_client):
        with patch("app.api.discovery.celery_app.send_task") as mock_task:
            mock_task.return_value.id = "test-job-id-123"
            resp = await superuser_client.post(
                "/api/discovery/scan", json={"cidr": "10.0.0.0/28"}
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "job_id" in data
        assert data["status"] == "pending"

    async def test_scan_endpoint_rejects_too_large(self, superuser_client):
        resp = await superuser_client.post(
            "/api/discovery/scan", json={"cidr": "10.0.0.0/8"}
        )
        assert resp.status_code == 422
        assert "too large" in resp.json()["detail"].lower()

    async def test_scan_endpoint_rejects_blocked_range(self, superuser_client):
        resp = await superuser_client.post(
            "/api/discovery/scan", json={"cidr": "127.0.0.0/24"}
        )
        assert resp.status_code == 422
        assert "blocked" in resp.json()["detail"].lower()

    async def test_bulk_add_creates_hosts(self, superuser_client, db):
        key = await create_ssh_key(db)
        resp = await superuser_client.post(
            "/api/discovery/add-hosts",
            json={"ips": ["10.77.77.1", "10.77.77.2"], "ssh_key_id": key.id},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["added"] == 2
        assert data["skipped"] == 0
        assert len(data["hosts"]) == 2

    async def test_bulk_add_skips_existing_ip(self, superuser_client, db):
        key = await create_ssh_key(db)
        # First add
        resp1 = await superuser_client.post(
            "/api/discovery/add-hosts",
            json={"ips": ["10.77.78.1"], "ssh_key_id": key.id},
        )
        assert resp1.status_code == 201
        # Add again — should skip
        resp2 = await superuser_client.post(
            "/api/discovery/add-hosts",
            json={"ips": ["10.77.78.1"], "ssh_key_id": key.id},
        )
        assert resp2.status_code == 201
        assert resp2.json()["added"] == 0
        assert resp2.json()["skipped"] == 1

    async def test_bulk_add_enforces_limit(self, superuser_client, db):
        key = await create_ssh_key(db)
        ips = [f"10.77.{i // 256}.{i % 256}" for i in range(51)]
        resp = await superuser_client.post(
            "/api/discovery/add-hosts",
            json={"ips": ips, "ssh_key_id": key.id},
        )
        assert resp.status_code == 422

    async def test_bulk_add_invalid_ssh_key(self, superuser_client):
        resp = await superuser_client.post(
            "/api/discovery/add-hosts",
            json={"ips": ["10.77.79.1"], "ssh_key_id": 99999},
        )
        assert resp.status_code == 404

    async def test_non_superuser_scan_rejected(self, regular_user_client):
        resp = await regular_user_client.post(
            "/api/discovery/scan", json={"cidr": "10.0.0.0/28"}
        )
        assert resp.status_code == 403

    async def test_non_superuser_add_rejected(self, regular_user_client):
        resp = await regular_user_client.post(
            "/api/discovery/add-hosts",
            json={"ips": ["10.0.0.1"], "ssh_key_id": 1},
        )
        assert resp.status_code == 403
