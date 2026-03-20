"""
Integration test: full Barricade workflow from registration to audit log verification.

Runs against the real FastAPI app via httpx.AsyncClient + ASGITransport.
Uses testcontainers for an isolated PostgreSQL instance.

Run with:
    pytest tests/integration/test_full_workflow.py -v -m integration
"""

import pytest
import httpx
from httpx import ASGITransport

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

pytestmark = pytest.mark.integration


def _generate_ed25519_private_key_pem() -> str:
    key = Ed25519PrivateKey.generate()
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


async def _promote_to_superuser(db_url: str, email: str) -> None:
    """Directly update the DB to make a user a superuser."""
    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE users SET is_superuser = TRUE, is_verified = TRUE WHERE email = :email"),
            {"email": email},
        )
    await engine.dispose()


class TestFullWorkflow:
    """End-to-end workflow: register → login → create resources → sync → drift → audit."""

    @pytest.fixture(autouse=True)
    async def setup(self):
        from testcontainers.postgres import PostgresContainer

        with PostgresContainer("postgres:16-alpine") as pg:
            # Build asyncpg-compatible URL
            sync_url = pg.get_connection_url()
            async_url = sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://").replace(
                "postgresql://", "postgresql+asyncpg://"
            )

            # Run Alembic migrations against the test DB
            import subprocess
            import os

            env = os.environ.copy()
            env["BARRICADE_DATABASE__URL"] = async_url

            result = subprocess.run(
                ["alembic", "upgrade", "head"],
                capture_output=True,
                text=True,
                env=env,
                cwd=str(__import__("pathlib").Path(__file__).parent.parent.parent),
            )
            assert result.returncode == 0, f"Alembic migration failed:\n{result.stderr}"

            # Patch app settings to use test DB
            from app.config import settings

            original_db_url = settings.database.url
            settings.database.url = async_url

            # Rebuild the DB engine with the test URL
            import app.db as db_module

            original_engine = db_module.engine
            original_session_local = db_module.AsyncSessionLocal

            test_engine = create_async_engine(async_url, echo=False)
            test_session_local = async_sessionmaker(test_engine, expire_on_commit=False)
            db_module.engine = test_engine
            db_module.AsyncSessionLocal = test_session_local

            from app.main import app as fastapi_app

            self.fastapi_app = fastapi_app
            transport = ASGITransport(app=fastapi_app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
                follow_redirects=True,
            ) as client:
                self.client = client
                self.async_url = async_url
                yield

            # Restore
            settings.database.url = original_db_url
            db_module.engine = original_engine
            db_module.AsyncSessionLocal = original_session_local
            await test_engine.dispose()

    async def test_full_workflow(self):
        client = self.client

        # ── Step 1: Register superuser ────────────────────────────────────────
        r = await client.post(
            "/auth/register",
            json={"email": "integ@barricade.test", "password": "IntegPass1!"},
        )
        assert r.status_code == 201, r.text
        user_data = r.json()
        assert "id" in user_data
        assert user_data["email"] == "integ@barricade.test"
        admin_user_id = user_data["id"]

        # Promote to superuser directly in DB
        await _promote_to_superuser(self.async_url, "integ@barricade.test")

        # ── Step 2: Login ─────────────────────────────────────────────────────
        r = await client.post(
            "/auth/jwt/login",
            data={"username": "integ@barricade.test", "password": "IntegPass1!"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert r.status_code == 200, r.text
        assert "barricade_auth" in r.cookies

        # ── Step 3: Create SSH key ────────────────────────────────────────────
        private_key_pem = _generate_ed25519_private_key_pem()
        r = await client.post(
            "/api/ssh-keys",
            json={"name": "integ-key", "private_key": private_key_pem, "is_default": True},
        )
        assert r.status_code == 201, r.text
        ssh_key_data = r.json()
        assert "id" in ssh_key_data
        assert ssh_key_data["name"] == "integ-key"
        assert "private_key" not in ssh_key_data
        assert "encrypted_private_key" not in ssh_key_data
        ssh_key_id = ssh_key_data["id"]

        # ── Step 4: Create host group ─────────────────────────────────────────
        r = await client.post(
            "/api/groups",
            json={
                "name": "integ-web-servers",
                "description": "Integration test group",
                "priority": 500,
            },
        )
        assert r.status_code == 201, r.text
        group_data = r.json()
        assert "id" in group_data
        group_id = group_data["id"]

        # ── Step 5: Create host ───────────────────────────────────────────────
        r = await client.post(
            "/api/hosts",
            json={
                "hostname": "integ-web01",
                "ip_address": "10.99.0.1",
                "ssh_port": 22,
                "ssh_key_id": ssh_key_id,
                "group_ids": [group_id],
            },
        )
        assert r.status_code == 201, r.text
        host_data = r.json()
        assert "id" in host_data
        assert host_data["hostname"] == "integ-web01"
        host_id = host_data["id"]

        # ── Step 6: Create firewall rules ─────────────────────────────────────
        rules_url = f"/api/groups/{group_id}/rules"

        r = await client.post(
            rules_url,
            json={
                "action": "allow",
                "protocol": "tcp",
                "direction": "input",
                "port_start": 22,
                "port_end": 22,
                "comment": "Allow SSH",
            },
        )
        assert r.status_code == 201, r.text
        rule_ssh_id = r.json()["id"]

        r = await client.post(
            rules_url,
            json={
                "action": "allow",
                "protocol": "tcp",
                "direction": "input",
                "port_start": 80,
                "port_end": 80,
                "source_cidr": "0.0.0.0/0",
                "comment": "Allow HTTP",
            },
        )
        assert r.status_code == 201, r.text

        r = await client.post(
            rules_url,
            json={
                "action": "deny",
                "protocol": "any",
                "direction": "input",
                "comment": "Deny all other input",
            },
        )
        assert r.status_code == 201, r.text

        # ── Step 7: Get effective rules for host ──────────────────────────────
        r = await client.get(f"/api/hosts/{host_id}/effective-rules")
        assert r.status_code == 200, r.text
        effective_rules = r.json()
        assert isinstance(effective_rules, list)
        assert len(effective_rules) >= 3

        # ── Step 8: Preview sync (plan) ───────────────────────────────────────
        r = await client.post(f"/api/sync/hosts/{host_id}/plan")
        assert r.status_code == 200, r.text
        plan_data = r.json()
        assert "has_changes" in plan_data
        assert "rules_to_add" in plan_data
        assert isinstance(plan_data["rules_to_add"], list)

        # ── Step 9: Trigger sync ──────────────────────────────────────────────
        r = await client.post(f"/api/sync/hosts/{host_id}/sync")
        assert r.status_code == 201, r.text
        job_data = r.json()
        assert "id" in job_data
        assert "status" in job_data
        assert job_data["status"] in ("pending", "running")
        job_id = job_data["id"]

        # ── Step 10: Poll sync job status ─────────────────────────────────────
        r = await client.get(f"/api/sync/jobs/{job_id}")
        assert r.status_code == 200, r.text
        job_status_data = r.json()
        assert job_status_data["status"] in ("pending", "running", "success", "failed")

        # ── Step 11: Check drift ──────────────────────────────────────────────
        r = await client.post(f"/api/drift/hosts/{host_id}/check")
        assert r.status_code == 200, r.text
        drift_data = r.json()
        assert "host_id" in drift_data
        assert "status" in drift_data
        assert drift_data["host_id"] == host_id

        # ── Step 12: Query audit log ──────────────────────────────────────────
        r = await client.get("/api/audit-log")
        assert r.status_code == 200, r.text
        audit_entries = r.json()
        assert isinstance(audit_entries, list)

        # ── Step 13: RBAC — viewer user ───────────────────────────────────────
        # Register viewer
        r = await client.post(
            "/auth/register",
            json={"email": "viewer@barricade.test", "password": "ViewerPass1!"},
        )
        assert r.status_code == 201, r.text
        viewer_id = r.json()["id"]

        # Grant viewer permission on the group (as superuser)
        r = await client.post(
            f"/api/groups/{group_id}/permissions",
            json={"user_id": viewer_id, "role": "viewer"},
        )
        assert r.status_code == 201, r.text

        # Login as viewer (new client to isolate cookies, same app instance)
        async with httpx.AsyncClient(
            transport=ASGITransport(app=self.fastapi_app),
            base_url="http://testserver",
            follow_redirects=True,
        ) as viewer_client:
            r = await viewer_client.post(
                "/auth/jwt/login",
                data={"username": "viewer@barricade.test", "password": "ViewerPass1!"},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            assert r.status_code == 200, r.text

            # Viewer can list groups
            r = await viewer_client.get("/api/groups")
            assert r.status_code == 200, r.text

            # Viewer cannot create groups (requires superuser)
            r = await viewer_client.post(
                "/api/groups",
                json={"name": "viewer-attempt-group", "priority": 501},
            )
            assert r.status_code == 403, r.text

        # ── Step 14: Cleanup verification ────────────────────────────────────
        r = await client.get(f"/api/hosts/{host_id}")
        assert r.status_code == 200, r.text
        assert r.json()["id"] == host_id

        r = await client.get(f"/api/groups/{group_id}")
        assert r.status_code == 200, r.text
        assert r.json()["id"] == group_id

        r = await client.get(f"/api/groups/{group_id}/rules")
        assert r.status_code == 200, r.text
        rules_list = r.json()
        assert len(rules_list) == 3
