import pytest
from pydantic import ValidationError

from app.services.collector import ServiceCurrentState
from app.services.diff import compute_service_diff
from app.services.schemas import ServiceRuleCreate, ServiceRuleUpdate


class TestServiceSchemas:
    def test_protected_service_rejected(self):
        with pytest.raises(ValidationError):
            ServiceRuleCreate(service_name="sshd", state="running", enabled=True)

    def test_service_name_normalized(self):
        rule = ServiceRuleCreate(service_name="nginx.service", state="running", enabled=True)
        assert rule.service_name == "nginx"

    def test_valid_service_accepted(self):
        rule = ServiceRuleCreate(service_name="nginx", state="running", enabled=True)
        assert rule.service_name == "nginx"

    def test_invalid_state_rejected(self):
        with pytest.raises(ValidationError):
            ServiceRuleCreate(service_name="nginx", state="restarted", enabled=True)

    def test_update_protected_rejected(self):
        with pytest.raises(ValidationError):
            ServiceRuleUpdate(service_name="systemd-journald")


class TestServiceDiff:
    def _make_desired(self, name, state, enabled):
        class D:
            pass

        d = D()
        d.service_name = name
        d.state = state
        d.enabled = enabled
        return d

    def test_in_sync(self):
        current = [ServiceCurrentState(service_name="nginx", active_state="running", enabled=True)]
        desired = [self._make_desired("nginx", "running", True)]
        diff = compute_service_diff(current, desired)
        assert "nginx" in diff.services_in_sync
        assert not diff.has_changes

    def test_state_drift(self):
        current = [ServiceCurrentState(service_name="nginx", active_state="stopped", enabled=True)]
        desired = [self._make_desired("nginx", "running", True)]
        diff = compute_service_diff(current, desired)
        assert diff.has_changes
        assert diff.services_to_update[0].reason == "state_mismatch"

    def test_restarted_normalized(self):
        current = [ServiceCurrentState(service_name="nginx", active_state="running", enabled=True)]
        desired = [self._make_desired("nginx", "restarted", True)]
        diff = compute_service_diff(current, desired)
        assert not diff.has_changes

    def test_enabled_mismatch(self):
        current = [ServiceCurrentState(service_name="nginx", active_state="running", enabled=False)]
        desired = [self._make_desired("nginx", "running", True)]
        diff = compute_service_diff(current, desired)
        assert diff.services_to_update[0].reason == "enabled_mismatch"

    def test_error_service(self):
        current = [ServiceCurrentState(service_name="nginx", active_state="error", enabled=False)]
        desired = [self._make_desired("nginx", "running", True)]
        diff = compute_service_diff(current, desired)
        assert "nginx" in diff.services_with_errors

    def test_both_mismatch(self):
        current = [ServiceCurrentState(service_name="nginx", active_state="stopped", enabled=False)]
        desired = [self._make_desired("nginx", "running", True)]
        diff = compute_service_diff(current, desired)
        assert diff.services_to_update[0].reason == "both_mismatch"


class TestServiceAPI:
    @pytest.mark.asyncio
    async def test_create_group_service(self, superuser_client, db):
        from tests.conftest import create_group

        group = await create_group(db)
        await db.commit()
        resp = await superuser_client.post(
            f"/api/groups/{group.id}/services",
            json={"service_name": "nginx", "state": "running", "enabled": True},
        )
        assert resp.status_code == 201
        assert resp.json()["service_name"] == "nginx"

    @pytest.mark.asyncio
    async def test_protected_rejected_by_api(self, superuser_client, db):
        from tests.conftest import create_group

        group = await create_group(db)
        await db.commit()
        resp = await superuser_client.post(
            f"/api/groups/{group.id}/services",
            json={"service_name": "sshd", "state": "running", "enabled": True},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_effective_services(self, superuser_client, db):
        from tests.conftest import create_group, create_host, create_ssh_key

        group = await create_group(db)
        ssh_key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh_key.id, group_ids=[group.id])
        await db.commit()
        await superuser_client.post(
            f"/api/groups/{group.id}/services",
            json={"service_name": "nginx", "state": "running", "enabled": True},
        )
        resp = await superuser_client.get(f"/api/hosts/{host.id}/effective-services")
        assert resp.status_code == 200
        data = resp.json()
        nginx = [s for s in data if s["service_name"] == "nginx"]
        assert len(nginx) == 1
        assert nginx[0]["source"] == "group"


class TestServicePlaybook:
    def _base_args(self):
        return {
            "host_ip": "10.0.0.1",
            "ssh_port": 22,
            "ssh_key_path": "/tmp/key",
        }

    def test_override_mode_tasks_are_gated_on_unit_existence(self):
        import yaml

        from app.services.generator import generate_service_playbook

        playbook_yaml, _ = generate_service_playbook(
            services=[
                {
                    "service_name": "cron",
                    "state": "running",
                    "enabled": True,
                    "unit_content": "[Service]\nMemoryLimit=512M",
                    "deploy_mode": "override",
                },
            ],
            **self._base_args(),
        )
        play = yaml.safe_load(playbook_yaml)[0]
        tasks = play["tasks"]
        assert tasks[0]["ansible.builtin.service_facts"] == {}
        svc_tasks = [t for t in tasks if "cron" in t.get("name", "")]
        assert len(svc_tasks) >= 3
        for t in svc_tasks:
            assert t.get("when") == "'cron.service' in ansible_facts.services"

    def test_full_mode_tasks_unconditional(self):
        import yaml

        from app.services.generator import generate_service_playbook

        playbook_yaml, _ = generate_service_playbook(
            services=[
                {
                    "service_name": "myapp",
                    "state": "running",
                    "enabled": True,
                    "unit_content": (
                        "[Unit]\nDescription=My App\n\n[Service]\nExecStart=/usr/bin/myapp"
                    ),
                    "deploy_mode": "full",
                },
            ],
            **self._base_args(),
        )
        play = yaml.safe_load(playbook_yaml)[0]
        tasks = play["tasks"]
        assert tasks[0]["ansible.builtin.service_facts"] == {}
        svc_tasks = [t for t in tasks if "myapp" in t.get("name", "")]
        assert len(svc_tasks) >= 3
        for t in svc_tasks:
            assert "when" not in t

    def test_mixed_override_and_full(self):
        import yaml

        from app.services.generator import generate_service_playbook

        playbook_yaml, _ = generate_service_playbook(
            services=[
                {
                    "service_name": "cron",
                    "state": "running",
                    "enabled": True,
                    "unit_content": "[Service]\nMemoryLimit=512M",
                    "deploy_mode": "override",
                },
                {
                    "service_name": "myapp",
                    "state": "running",
                    "enabled": True,
                    "unit_content": "[Unit]\nDescription=My App",
                    "deploy_mode": "full",
                },
            ],
            **self._base_args(),
        )
        play = yaml.safe_load(playbook_yaml)[0]
        tasks = play["tasks"]
        cron_tasks = [t for t in tasks if "cron" in t.get("name", "")]
        myapp_tasks = [t for t in tasks if "myapp" in t.get("name", "")]
        assert all(t.get("when") == "'cron.service' in ansible_facts.services" for t in cron_tasks)
        assert all("when" not in t for t in myapp_tasks)
