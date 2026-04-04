"""End-to-end GitOps pipeline integration test.

Tests the full workflow:
1. Create local Git repo with YAML rules
2. Create HostGroup in DB with gitops_enabled
3. Import YAML -> verify rules in DB
4. Push invalid YAML -> verify error status, rules preserved
5. Push valid update -> verify rules updated
6. Test rule lockdown (403 when GitOps on)
7. Disable GitOps -> rule writes work again

Run with:
    cd backend && .venv/bin/pytest tests/integration/test_gitops_workflow.py -v -m integration
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import select

from app.gitops.git_service import clone_repo_local, get_current_sha, read_file_at_sha
from app.gitops.importer import import_group_from_yaml
from app.models.firewall_rule import FirewallRule
from app.models.git_repository import GitOpsStatus
from tests.conftest import create_group

pytestmark = pytest.mark.integration

VALID_YAML = """\
group: web-servers
priority: 100
firewall:
  rules:
    - action: allow
      protocol: tcp
      direction: input
      port: 443
      source: 10.0.0.0/8
      comment: HTTPS from internal
    - action: allow
      protocol: tcp
      direction: input
      port: 80
      comment: HTTP
"""

INVALID_YAML = """\
group: web-servers
firewall:
  rules:
    - action: explode
      protocol: tcp
      direction: input
"""

UPDATED_YAML = """\
group: web-servers
priority: 100
firewall:
  rules:
    - action: allow
      protocol: tcp
      direction: input
      port: 443
      source: 10.0.0.0/8
      comment: HTTPS from internal
    - action: allow
      protocol: tcp
      direction: input
      port: 8080
      comment: App server
    - action: deny
      protocol: tcp
      direction: input
      port: 22
      comment: Block SSH
"""


def _git_run(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "user.email=test@test.com", "-c", "user.name=Test"] + args,
        cwd=cwd,
        capture_output=True,
        check=True,
    )


def _setup_bare_repo() -> tuple[Path, Path]:
    bare_dir = Path(tempfile.mkdtemp(prefix="test-gitops-bare-"))
    clone_dir = Path(tempfile.mkdtemp(prefix="test-gitops-clone-"))

    subprocess.run(
        ["git", "-c", "init.defaultBranch=main", "init", "--bare", str(bare_dir)],
        capture_output=True,
        check=True,
    )

    subprocess.run(
        ["git", "clone", str(bare_dir), str(clone_dir)],
        capture_output=True,
        check=True,
    )
    rules_dir = clone_dir / "groups"
    rules_dir.mkdir()
    (rules_dir / "web-servers.yaml").write_text(VALID_YAML)

    _git_run(["add", "."], cwd=str(clone_dir))
    _git_run(["commit", "-m", "Add web-servers rules"], cwd=str(clone_dir))
    _git_run(["push", "-u", "origin", "main"], cwd=str(clone_dir))

    return bare_dir, clone_dir


def _push_yaml(clone_dir: Path, content: str, message: str) -> None:
    (clone_dir / "groups" / "web-servers.yaml").write_text(content)
    _git_run(["add", "."], cwd=str(clone_dir))
    _git_run(["commit", "-m", message], cwd=str(clone_dir))
    _git_run(["push"], cwd=str(clone_dir))


class TestGitOpsWorkflow:

    async def test_full_gitops_lifecycle(self, superuser_client, db):

        bare_dir, clone_dir = _setup_bare_repo()
        import_dir = Path(tempfile.mkdtemp(prefix="test-gitops-import-"))

        try:
            # -- STEP 1: Create group with GitOps enabled --
            group = await create_group(db, name="web-servers-gitops", priority=999)
            group.gitops_enabled = True
            group.gitops_file_path = "groups/web-servers.yaml"
            group.gitops_status = GitOpsStatus.disconnected
            await db.flush()

            # -- STEP 2: Clone bare repo and read YAML --
            _, import_dir = clone_repo_local(str(bare_dir), import_dir)
            sha = get_current_sha(import_dir)
            content = read_file_at_sha(import_dir, "groups/web-servers.yaml", sha)
            assert "HTTPS from internal" in content

            # -- STEP 3: Import YAML -> verify rules in DB --
            result = await import_group_from_yaml(
                group_id=group.id,
                yaml_content=content,
                commit_sha=sha,
                db=db,
            )
            assert result.success is True
            assert result.rules_added == 2

            rules_result = await db.execute(
                select(FirewallRule).where(
                    FirewallRule.group_id == group.id,
                    FirewallRule.is_system == False,  # noqa: E712
                )
            )
            rules = rules_result.scalars().all()
            assert len(rules) == 2
            ports = {r.port_start for r in rules}
            assert ports == {443, 80}

            await db.refresh(group)
            assert group.gitops_status == GitOpsStatus.synced
            assert group.gitops_error_message is None

            # -- STEP 4: Push invalid YAML -> error status, rules preserved --
            _push_yaml(clone_dir, INVALID_YAML, "Break rules")

            subprocess.run(
                ["git", "pull"], cwd=str(import_dir), capture_output=True, check=True,
            )
            sha2 = get_current_sha(import_dir)
            content2 = read_file_at_sha(import_dir, "groups/web-servers.yaml", sha2)

            result2 = await import_group_from_yaml(
                group_id=group.id,
                yaml_content=content2,
                commit_sha=sha2,
                db=db,
            )
            assert result2.success is False
            assert result2.error_message is not None

            await db.refresh(group)
            assert group.gitops_status == GitOpsStatus.error

            rules_after_error = await db.execute(
                select(FirewallRule).where(
                    FirewallRule.group_id == group.id,
                    FirewallRule.is_system == False,  # noqa: E712
                )
            )
            assert len(rules_after_error.scalars().all()) == 2

            # -- STEP 5: Push valid update -> rules updated --
            _push_yaml(clone_dir, UPDATED_YAML, "Fix rules")

            subprocess.run(
                ["git", "pull"], cwd=str(import_dir), capture_output=True, check=True,
            )
            sha3 = get_current_sha(import_dir)
            content3 = read_file_at_sha(import_dir, "groups/web-servers.yaml", sha3)

            result3 = await import_group_from_yaml(
                group_id=group.id,
                yaml_content=content3,
                commit_sha=sha3,
                db=db,
            )
            assert result3.success is True
            assert result3.rules_added == 2
            assert result3.rules_removed == 1
            assert result3.rules_unchanged == 1

            await db.refresh(group)
            assert group.gitops_status == GitOpsStatus.synced

            rules_updated = await db.execute(
                select(FirewallRule).where(
                    FirewallRule.group_id == group.id,
                    FirewallRule.is_system == False,  # noqa: E712
                )
            )
            updated_ports = {r.port_start for r in rules_updated.scalars().all()}
            assert updated_ports == {443, 8080, 22}

            # -- STEP 6: Rule lockdown (403 when GitOps on) --
            resp = await superuser_client.post(
                f"/api/groups/{group.id}/rules",
                json={
                    "action": "allow",
                    "protocol": "tcp",
                    "direction": "input",
                    "port_start": 9999,
                },
            )
            assert resp.status_code == 403
            assert "GitOps" in resp.json()["detail"]

            resp_get = await superuser_client.get(f"/api/groups/{group.id}/rules")
            assert resp_get.status_code == 200

            # -- STEP 7: Disable GitOps -> writes work again --
            group.gitops_enabled = False
            await db.flush()

            resp2 = await superuser_client.post(
                f"/api/groups/{group.id}/rules",
                json={
                    "action": "allow",
                    "protocol": "tcp",
                    "direction": "input",
                    "port_start": 9999,
                },
            )
            assert resp2.status_code == 201

        finally:
            for d in [bare_dir, clone_dir, import_dir]:
                if d.exists():
                    shutil.rmtree(str(d))
