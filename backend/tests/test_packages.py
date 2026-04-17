import pytest
from pydantic import ValidationError

from app.packages.diff import compute_diff
from app.packages.generator import generate_package_playbook
from app.packages.schemas import PackageRuleCreate

# ---------------------------------------------------------------------------
# Schema validation tests (pure unit tests, no DB)
# ---------------------------------------------------------------------------


class TestPackageSchemas:
    def test_protected_package_rejected_exact(self):
        with pytest.raises(ValidationError, match="protected"):
            PackageRuleCreate(package_name="openssh-server", state="present")

    def test_protected_package_rejected_wildcard(self):
        with pytest.raises(ValidationError, match="protected"):
            PackageRuleCreate(package_name="linux-image-5.15.0-generic", state="present")

    def test_valid_package_accepted(self):
        rule = PackageRuleCreate(package_name="nginx", state="present")
        assert rule.package_name == "nginx"
        assert rule.state == "present"
        assert rule.package_manager == "auto"

    def test_invalid_package_name_rejected(self):
        with pytest.raises(ValidationError, match="Invalid package name"):
            PackageRuleCreate(package_name="nginx; rm -rf /", state="present")

    def test_protected_package_bash_rejected(self):
        with pytest.raises(ValidationError, match="protected"):
            PackageRuleCreate(package_name="bash", state="present")

    def test_empty_package_name_rejected(self):
        with pytest.raises(ValidationError, match="must not be empty"):
            PackageRuleCreate(package_name="   ", state="present")

    def test_defaults_applied(self):
        rule = PackageRuleCreate(package_name="curl", state="latest")
        assert rule.version is None
        assert rule.package_manager == "auto"
        assert rule.priority == 0
        assert rule.comment is None


# ---------------------------------------------------------------------------
# Diff engine tests (pure unit tests, no DB)
# ---------------------------------------------------------------------------


class TestPackageDiff:
    def test_to_install_detected(self):
        diff = compute_diff(
            desired=[{"package_name": "nginx", "state": "present", "version": None}],
            actual=[{"name": "nginx", "state": "absent", "version": None}],
        )
        assert len(diff.to_install) == 1
        assert diff.to_install[0].package_name == "nginx"
        assert diff.has_drift

    def test_to_remove_detected(self):
        diff = compute_diff(
            desired=[{"package_name": "nginx", "state": "absent", "version": None}],
            actual=[{"name": "nginx", "state": "present", "version": "1.24.0"}],
        )
        assert len(diff.to_remove) == 1
        assert diff.to_remove[0].package_name == "nginx"
        assert diff.has_drift

    def test_version_mismatch_upgrade(self):
        diff = compute_diff(
            desired=[{"package_name": "nginx", "state": "present", "version": "1.24.*"}],
            actual=[{"name": "nginx", "state": "present", "version": "1.22.0-1ubuntu1"}],
        )
        assert len(diff.to_upgrade) == 1
        assert diff.to_upgrade[0].package_name == "nginx"

    def test_glob_version_in_sync(self):
        diff = compute_diff(
            desired=[{"package_name": "nginx", "state": "present", "version": "1.24.*"}],
            actual=[{"name": "nginx", "state": "present", "version": "1.24.0-1ubuntu1"}],
        )
        assert len(diff.in_sync) == 1
        assert not diff.has_drift

    def test_latest_any_version_in_sync(self):
        diff = compute_diff(
            desired=[{"package_name": "curl", "state": "latest", "version": None}],
            actual=[{"name": "curl", "state": "present", "version": "7.81.0"}],
        )
        assert len(diff.in_sync) == 1
        assert not diff.has_drift

    def test_no_version_pinning_in_sync(self):
        diff = compute_diff(
            desired=[{"package_name": "wget", "state": "present", "version": None}],
            actual=[{"name": "wget", "state": "present", "version": "1.21.0"}],
        )
        assert len(diff.in_sync) == 1
        assert not diff.has_drift

    def test_absent_both_in_sync(self):
        diff = compute_diff(
            desired=[{"package_name": "telnet", "state": "absent", "version": None}],
            actual=[{"name": "telnet", "state": "absent", "version": None}],
        )
        assert len(diff.in_sync) == 1
        assert not diff.has_drift


# ---------------------------------------------------------------------------
# Playbook generator tests (pure unit tests, no DB)
# ---------------------------------------------------------------------------


class TestPackageGenerator:
    def test_repos_before_packages(self):
        result = generate_package_playbook(
            host_ip="1.2.3.4",
            packages=[
                {
                    "package_name": "nginx",
                    "state": "present",
                    "version": None,
                    "package_manager": "auto",
                }
            ],
            repos=[
                {
                    "name": "nginx-stable",
                    "url": "https://nginx.org/packages/ubuntu",
                    "repo_type": "apt",
                    "distribution": "jammy",
                    "components": "nginx",
                    "state": "present",
                    "key_url": None,
                }
            ],
            ssh_key_path="/tmp/test.key",
        )
        tasks = result["playbook"][0]["tasks"]
        repo_idx = next(i for i, t in enumerate(tasks) if "apt_repository" in str(t))
        pkg_idx = next(i for i, t in enumerate(tasks) if "ansible.builtin.package" in t)
        assert repo_idx < pkg_idx

    def test_gather_facts_true(self):
        result = generate_package_playbook(
            host_ip="1.2.3.4",
            packages=[
                {
                    "package_name": "nginx",
                    "state": "present",
                    "version": None,
                    "package_manager": "auto",
                }
            ],
            repos=[],
            ssh_key_path="/tmp/test.key",
        )
        assert result["playbook"][0]["gather_facts"] is True

    def test_become_true(self):
        result = generate_package_playbook(
            host_ip="1.2.3.4",
            packages=[
                {
                    "package_name": "nginx",
                    "state": "latest",
                    "version": None,
                    "package_manager": "auto",
                }
            ],
            repos=[],
            ssh_key_path="/tmp/test.key",
        )
        assert result["playbook"][0]["become"] is True

    def test_versioned_package_apt_format(self):
        result = generate_package_playbook(
            host_ip="1.2.3.4",
            packages=[
                {
                    "package_name": "nginx",
                    "state": "present",
                    "version": "1.24.0",
                    "package_manager": "apt",
                }
            ],
            repos=[],
            ssh_key_path="/tmp/test.key",
        )
        tasks = result["playbook"][0]["tasks"]
        pkg_task = next(t for t in tasks if "ansible.builtin.package" in t)
        assert pkg_task["ansible.builtin.package"]["name"] == "nginx=1.24.0"

    def test_versioned_package_yum_format(self):
        result = generate_package_playbook(
            host_ip="1.2.3.4",
            packages=[
                {
                    "package_name": "nginx",
                    "state": "present",
                    "version": "1.24.0",
                    "package_manager": "yum",
                }
            ],
            repos=[],
            ssh_key_path="/tmp/test.key",
        )
        tasks = result["playbook"][0]["tasks"]
        pkg_task = next(t for t in tasks if "ansible.builtin.package" in t)
        assert pkg_task["ansible.builtin.package"]["name"] == "nginx-1.24.0"

    def test_absent_package_state(self):
        result = generate_package_playbook(
            host_ip="1.2.3.4",
            packages=[
                {
                    "package_name": "telnet",
                    "state": "absent",
                    "version": None,
                    "package_manager": "auto",
                }
            ],
            repos=[],
            ssh_key_path="/tmp/test.key",
        )
        tasks = result["playbook"][0]["tasks"]
        pkg_task = next(t for t in tasks if "ansible.builtin.package" in t)
        assert pkg_task["ansible.builtin.package"]["state"] == "absent"


# ---------------------------------------------------------------------------
# Merge engine tests (async, require DB)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestPackageMerge:
    async def test_group_package_merge_priority(self, db):
        from app.packages.merge import get_effective_packages
        from app.packages.models import PackageManager, PackageRule, PackageState
        from tests.conftest import create_group, create_host, create_ssh_key

        group_low = await create_group(db, name="low-prio", priority=10)
        group_high = await create_group(db, name="high-prio", priority=100)
        ssh_key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh_key.id, group_ids=[group_low.id, group_high.id])

        db.add(
            PackageRule(
                group_id=group_low.id,
                package_name="nginx",
                state=PackageState.absent,
                package_manager=PackageManager.auto,
                priority=0,
            )
        )
        db.add(
            PackageRule(
                group_id=group_high.id,
                package_name="nginx",
                state=PackageState.present,
                package_manager=PackageManager.auto,
                priority=0,
            )
        )
        await db.flush()

        effective = await get_effective_packages(host.id, db)
        nginx = [p for p in effective if p.package_name == "nginx"]
        assert len(nginx) == 1
        assert nginx[0].state == "present"
        assert nginx[0].source == "group"
        assert nginx[0].source_id == group_high.id

    async def test_host_override_replaces_group(self, db):
        from app.packages.merge import get_effective_packages
        from app.packages.models import PackageManager, PackageRule, PackageState
        from tests.conftest import create_group, create_host, create_ssh_key

        group = await create_group(db, priority=50)
        ssh_key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh_key.id, group_ids=[group.id])

        db.add(
            PackageRule(
                group_id=group.id,
                package_name="nginx",
                state=PackageState.present,
                package_manager=PackageManager.auto,
                priority=0,
            )
        )
        db.add(
            PackageRule(
                host_id=host.id,
                package_name="nginx",
                state=PackageState.absent,
                package_manager=PackageManager.auto,
                priority=0,
            )
        )
        await db.flush()

        effective = await get_effective_packages(host.id, db)
        nginx = [p for p in effective if p.package_name == "nginx"]
        assert len(nginx) == 1
        assert nginx[0].state == "absent"
        assert nginx[0].source == "host"

    async def test_repo_dedup_by_url_type_distribution(self, db):
        from app.packages.merge import get_effective_repos
        from app.packages.models import PackageRepository, PackageState, RepoType
        from tests.conftest import create_group, create_host, create_ssh_key

        group_a = await create_group(db, priority=10)
        group_b = await create_group(db, priority=20)
        ssh_key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh_key.id, group_ids=[group_a.id, group_b.id])

        # Same URL but different distribution → should NOT be deduplicated
        db.add(
            PackageRepository(
                group_id=group_a.id,
                name="nginx-jammy",
                url="https://nginx.org/packages/ubuntu",
                repo_type=RepoType.apt,
                distribution="jammy",
                components="nginx",
                state=PackageState.present,
            )
        )
        db.add(
            PackageRepository(
                group_id=group_b.id,
                name="nginx-focal",
                url="https://nginx.org/packages/ubuntu",
                repo_type=RepoType.apt,
                distribution="focal",
                components="nginx",
                state=PackageState.present,
            )
        )
        await db.flush()

        repos = await get_effective_repos(host.id, db)
        assert len(repos) == 2

    async def test_repo_dedup_same_key(self, db):
        from app.packages.merge import get_effective_repos
        from app.packages.models import PackageRepository, PackageState, RepoType
        from tests.conftest import create_group, create_host, create_ssh_key

        group_a = await create_group(db, priority=10)
        group_b = await create_group(db, priority=20)
        ssh_key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh_key.id, group_ids=[group_a.id, group_b.id])

        # Same URL, same type, same distribution → should be deduplicated to 1
        db.add(
            PackageRepository(
                group_id=group_a.id,
                name="nginx-a",
                url="https://nginx.org/packages/ubuntu",
                repo_type=RepoType.apt,
                distribution="jammy",
                components="nginx",
                state=PackageState.present,
            )
        )
        db.add(
            PackageRepository(
                group_id=group_b.id,
                name="nginx-b",
                url="https://nginx.org/packages/ubuntu",
                repo_type=RepoType.apt,
                distribution="jammy",
                components="nginx",
                state=PackageState.present,
            )
        )
        await db.flush()

        repos = await get_effective_repos(host.id, db)
        assert len(repos) == 1


# ---------------------------------------------------------------------------
# API tests (require DB + superuser_client)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestPackageAPI:
    async def test_create_group_package(self, superuser_client, db):
        from tests.conftest import create_group

        group = await create_group(db)
        await db.commit()
        resp = await superuser_client.post(
            f"/api/groups/{group.id}/packages",
            json={"package_name": "nginx", "state": "present"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["package_name"] == "nginx"
        assert data["group_id"] == group.id
        assert data["state"] == "present"
        assert data["package_manager"] == "auto"

    async def test_protected_package_rejected(self, superuser_client, db):
        from tests.conftest import create_group

        group = await create_group(db)
        await db.commit()
        resp = await superuser_client.post(
            f"/api/groups/{group.id}/packages",
            json={"package_name": "openssh-server", "state": "present"},
        )
        assert resp.status_code == 422

    async def test_duplicate_package_rejected(self, superuser_client, db):
        from tests.conftest import create_group

        group = await create_group(db)
        await db.commit()
        await superuser_client.post(
            f"/api/groups/{group.id}/packages",
            json={"package_name": "nginx", "state": "present"},
        )
        resp = await superuser_client.post(
            f"/api/groups/{group.id}/packages",
            json={"package_name": "nginx", "state": "latest"},
        )
        assert resp.status_code == 409

    async def test_list_group_packages(self, superuser_client, db):
        from tests.conftest import create_group

        group = await create_group(db)
        await db.commit()
        await superuser_client.post(
            f"/api/groups/{group.id}/packages",
            json={"package_name": "nginx", "state": "present"},
        )
        await superuser_client.post(
            f"/api/groups/{group.id}/packages",
            json={"package_name": "curl", "state": "latest"},
        )
        resp = await superuser_client.get(f"/api/groups/{group.id}/packages")
        assert resp.status_code == 200
        names = [p["package_name"] for p in resp.json()]
        assert "nginx" in names
        assert "curl" in names

    async def test_get_effective_packages(self, superuser_client, db):
        from tests.conftest import create_group, create_host, create_ssh_key

        group = await create_group(db)
        ssh_key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh_key.id, group_ids=[group.id])
        await db.commit()

        await superuser_client.post(
            f"/api/groups/{group.id}/packages",
            json={"package_name": "nginx", "state": "present"},
        )
        await superuser_client.post(
            f"/api/hosts/{host.id}/packages",
            json={"package_name": "nginx", "state": "absent"},
        )

        resp = await superuser_client.get(f"/api/hosts/{host.id}/effective-packages")
        assert resp.status_code == 200
        data = resp.json()
        nginx = [p for p in data if p["package_name"] == "nginx"]
        assert len(nginx) == 1
        assert nginx[0]["source"] == "host"
        assert nginx[0]["state"] == "absent"

    async def test_delete_group_package(self, superuser_client, db):
        from tests.conftest import create_group

        group = await create_group(db)
        await db.commit()
        create_resp = await superuser_client.post(
            f"/api/groups/{group.id}/packages",
            json={"package_name": "nginx", "state": "present"},
        )
        rule_id = create_resp.json()["id"]

        del_resp = await superuser_client.delete(f"/api/groups/{group.id}/packages/{rule_id}")
        assert del_resp.status_code == 204

        list_resp = await superuser_client.get(f"/api/groups/{group.id}/packages")
        assert all(p["id"] != rule_id for p in list_resp.json())
