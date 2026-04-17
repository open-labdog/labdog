"""Tests for service live control: schemas, SSH functions, and API endpoints."""

import inspect

import pytest
from pydantic import ValidationError

from app.services.collector import execute_service_command, list_all_services
from app.services.live_schemas import (
    ServiceCommandRequest,
    ServiceCommandResponse,
    ServiceInventoryItem,
)


class TestCommandSchemas:
    def test_valid_start_command(self):
        r = ServiceCommandRequest(service_name="nginx", action="start")
        assert r.service_name == "nginx"
        assert r.action == "start"

    def test_valid_stop_command(self):
        r = ServiceCommandRequest(service_name="nginx", action="stop")
        assert r.action == "stop"

    def test_valid_restart_command(self):
        r = ServiceCommandRequest(service_name="nginx", action="restart")
        assert r.action == "restart"

    def test_service_suffix_stripped(self):
        r = ServiceCommandRequest(service_name="nginx.service", action="start")
        assert r.service_name == "nginx"

    def test_injection_semicolon_rejected(self):
        with pytest.raises(ValidationError):
            ServiceCommandRequest(service_name="nginx; rm -rf /", action="start")

    def test_injection_space_rejected(self):
        with pytest.raises(ValidationError):
            ServiceCommandRequest(service_name="nginx rm", action="start")

    def test_injection_backtick_rejected(self):
        with pytest.raises(ValidationError):
            ServiceCommandRequest(service_name="`whoami`", action="start")

    def test_invalid_action_rejected(self):
        with pytest.raises(ValidationError):
            ServiceCommandRequest(service_name="nginx", action="enable")

    def test_protected_service_not_blocked(self):
        """Ad-hoc commands allow protected services — unlike ServiceRuleCreate which blocks them."""
        r = ServiceCommandRequest(service_name="sshd", action="stop")
        assert r.service_name == "sshd"

    def test_max_length_exceeded(self):
        with pytest.raises(ValidationError):
            ServiceCommandRequest(service_name="a" * 101, action="start")

    def test_max_length_accepted(self):
        r = ServiceCommandRequest(service_name="a" * 100, action="start")
        assert len(r.service_name) == 100

    def test_special_chars_accepted(self):
        """Service names with @, :, ., - are valid systemd names."""
        r = ServiceCommandRequest(service_name="user@1000", action="start")
        assert r.service_name == "user@1000"

    def test_response_model(self):
        resp = ServiceCommandResponse(
            success=True,
            exit_code=0,
            stdout="",
            stderr="",
            service_name="nginx",
            action="restart",
            is_protected=False,
        )
        assert resp.success is True
        assert resp.is_protected is False

    def test_inventory_item_model(self):
        item = ServiceInventoryItem(
            unit="nginx",
            load_state="loaded",
            active_state="active",
            sub_state="running",
            description="A high performance web server",
            is_managed=True,
            is_protected=False,
            is_system=False,
        )
        assert item.unit == "nginx"
        assert item.is_managed is True
        assert item.is_system is False


class TestSSHFunctions:
    """Verify SSH function signatures and safety measures."""

    def test_list_all_services_signature(self):
        sig = inspect.signature(list_all_services)
        assert "host_ip" in sig.parameters
        assert "ssh_port" in sig.parameters
        assert "private_key_pem" in sig.parameters

    def test_execute_command_signature(self):
        sig = inspect.signature(execute_service_command)
        params = list(sig.parameters.keys())
        assert "host_ip" in params
        assert "service_name" in params
        assert "action" in params

    def test_execute_command_uses_shlex(self):
        source = inspect.getsource(execute_service_command)
        assert "shlex.quote" in source

    def test_execute_command_has_timeout(self):
        source = inspect.getsource(execute_service_command)
        assert "wait_for" in source
        assert "30.0" in source

    def test_list_all_services_has_timeout(self):
        source = inspect.getsource(list_all_services)
        assert "wait_for" in source
        assert "30.0" in source

    def test_execute_command_rejects_invalid_action(self):
        """execute_service_command raises ValueError for invalid actions."""
        import asyncio

        with pytest.raises(ValueError, match="Invalid action"):
            asyncio.get_event_loop().run_until_complete(
                execute_service_command("1.2.3.4", 22, "fake-key", "nginx", "enable")
            )


class TestCommandAPI:
    @pytest.mark.asyncio
    async def test_inventory_returns_400_without_ssh_key(self, superuser_client, db):
        from tests.conftest import create_host

        host = await create_host(db)
        host_id = host.id
        await db.commit()
        resp = await superuser_client.get(f"/api/services/hosts/{host_id}/inventory")
        assert resp.status_code == 400
        assert "SSH key" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_command_returns_400_without_ssh_key(self, superuser_client, db):
        from tests.conftest import create_host

        host = await create_host(db)
        host_id = host.id
        await db.commit()
        resp = await superuser_client.post(
            f"/api/services/hosts/{host_id}/command",
            json={"service_name": "nginx", "action": "restart"},
        )
        assert resp.status_code == 400
        assert "SSH key" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_command_rejects_invalid_service_name(self, superuser_client, db):
        from tests.conftest import create_host, create_ssh_key

        ssh_key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh_key.id)
        host_id = host.id
        await db.commit()
        resp = await superuser_client.post(
            f"/api/services/hosts/{host_id}/command",
            json={"service_name": "nginx; rm -rf /", "action": "restart"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_command_rejects_invalid_action(self, superuser_client, db):
        from tests.conftest import create_host, create_ssh_key

        ssh_key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh_key.id)
        host_id = host.id
        await db.commit()
        resp = await superuser_client.post(
            f"/api/services/hosts/{host_id}/command",
            json={"service_name": "nginx", "action": "enable"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_inventory_host_not_found(self, superuser_client, db):
        resp = await superuser_client.get("/api/services/hosts/99999/inventory")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_command_host_not_found(self, superuser_client, db):
        resp = await superuser_client.post(
            "/api/services/hosts/99999/command",
            json={"service_name": "nginx", "action": "start"},
        )
        assert resp.status_code == 404
