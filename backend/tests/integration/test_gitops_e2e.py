"""End-to-end GitOps tests against a locally-hosted git repo.

Covers what the older `test_gitops_workflow.py` doesn't:

* **Multi-module YAML** — every section in `LabDogGroupYAML` (firewall,
  services, packages, hosts entries, cron jobs, users, linux groups,
  resolver, workflow) on a single push, verified end-to-end against
  the live import dispatcher.
* **`_global.yaml`** — drift interval + scan-config rows imported
  through the global dispatcher, with name-based ssh_key /
  default_groups resolution exercised.
* **Webhook receiver** — full HMAC path: build a GitHub-shaped
  payload, sign it with the right secret, POST to
  `/webhooks/github`, intercept the celery dispatch and invoke
  the task body inline so we exercise webhook → task → import →
  per-host sync trigger in one pass.

Mechanism: a bare git repo on a tmpfs path (`file://` URL) acts as
the remote. A working clone lets us push commits between test
phases. No external infrastructure required — `pytest -m integration`
on a stock dev env gives the same coverage you'd get from a real
github.com round-trip, minus TLS.
"""

import hashlib
import hmac
import json
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.cron.models import CronJob
from app.gitops.git_service import clone_repo_local, read_file_at_sha
from app.gitops.importer import import_global_from_yaml, import_group_from_yaml
from app.hosts_mgmt.models import HostsEntry
from app.models.app_setting import AppSetting
from app.models.firewall_rule import FirewallRule
from app.models.git_repository import GitAuthType, GitOpsStatus, GitRepository
from app.models.scan_config import ScanConfig
from app.packages.models import PackageRule
from app.resolver.models import ResolverConfig
from app.services.models import ServiceRule
from app.settings_service import invalidate_cache
from app.user_mgmt.models import LinuxGroup, LinuxUser
from tests.conftest import create_group, create_ssh_key

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Multi-module YAML covering every section in LabDogGroupYAML
# ---------------------------------------------------------------------------

MULTI_MODULE_YAML = """\
group: e2e-multi
priority: 200

firewall:
  rules:
    - action: allow
      protocol: tcp
      direction: input
      port: 22
      source: 10.0.0.0/8
      comment: SSH from internal

services:
  - service_name: nginx
    state: running
    enabled: true
    priority: 10

packages:
  - package_name: htop
    state: present
  - package_name: curl
    state: latest

hosts_entries:
  - ip_address: 10.20.30.40
    hostname: e2e-internal.local
    aliases: [e2e]

cron_jobs:
  - name: e2e-cleanup
    user: root
    schedule: "0 4 * * *"
    command: /usr/local/bin/cleanup.sh

linux_groups:
  - groupname: e2e-devs
    state: present

users:
  - username: e2e-bot
    shell: /bin/bash
    state: present
    supplementary_groups: [e2e-devs]

resolver:
  nameservers:
    - 1.1.1.1
    - 9.9.9.9
  search_domains:
    - e2e.local
  resolver_type: resolv_conf

workflow:
  enabled: false
  schedule_cron: "0 3 * * 0"
  batch_size: 1
  pre_update_snapshot: true
  auto_rollback: true
  auto_reboot: true
  action_key: linux-upgrade
  action_parameters: {}
"""


# Second push: trim some, add some, modify one.
MULTI_MODULE_YAML_V2 = """\
group: e2e-multi
priority: 200

firewall:
  rules:
    - action: allow
      protocol: tcp
      direction: input
      port: 22
      source: 10.0.0.0/8
      comment: SSH from internal
    - action: allow
      protocol: tcp
      direction: input
      port: 443
      comment: HTTPS

services:
  - service_name: nginx
    state: stopped
    enabled: true
    priority: 10
  - service_name: cron
    state: running
    enabled: true
    priority: 20

packages:
  - package_name: htop
    state: present

hosts_entries:
  - ip_address: 10.20.30.40
    hostname: e2e-internal.local
    aliases: [e2e]
  - ip_address: 10.20.30.41
    hostname: e2e-second.local

resolver:
  nameservers:
    - 1.1.1.1
  search_domains: []
  resolver_type: resolv_conf

workflow:
  enabled: true
  schedule_cron: "0 4 * * 0"
  batch_size: 2
  pre_update_snapshot: true
  auto_rollback: true
  auto_reboot: true
  action_key: linux-upgrade
  action_parameters: {}
"""


# Global YAML with drift + 1 scan config that references a group + ssh key
# that we'll create up-front in the test.
GLOBAL_YAML_V1 = """\
drift:
  check_interval_minutes: 12

discovery:
  - name: e2e-scan
    cidrs:
      - 10.30.0.0/24
    ssh_key: e2e-key
    interval_minutes: 60
    default_groups: [e2e-multi]
"""


# ---------------------------------------------------------------------------
# Bare-repo helpers
# ---------------------------------------------------------------------------


def _git_run(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "user.email=test@test.com", "-c", "user.name=Test"] + args,
        cwd=cwd,
        capture_output=True,
        check=True,
    )


def _setup_bare_repo() -> tuple[Path, Path]:
    """Make `<bare>.git` and a working clone, both in tmpdirs.

    Returns ``(bare_dir, clone_dir)``. Caller deletes both in finally.
    """
    bare_dir = Path(tempfile.mkdtemp(prefix="labdog-e2e-bare-"))
    clone_dir = Path(tempfile.mkdtemp(prefix="labdog-e2e-clone-"))

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

    # Empty initial commit so refs/heads/main exists with a real SHA before
    # the first content push.
    _git_run(["commit", "--allow-empty", "-m", "init"], cwd=str(clone_dir))
    _git_run(["push", "-u", "origin", "main"], cwd=str(clone_dir))

    return bare_dir, clone_dir


def _push_files(clone_dir: Path, files: dict[str, str], message: str) -> str:
    """Write each file under the working clone, commit, push, return new HEAD SHA."""
    for relpath, content in files.items():
        full = clone_dir / relpath
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
    _git_run(["add", "-A"], cwd=str(clone_dir))
    _git_run(["commit", "-m", message], cwd=str(clone_dir))
    _git_run(["push"], cwd=str(clone_dir))
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(clone_dir),
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
    return sha


# ---------------------------------------------------------------------------
# Multi-module group YAML
# ---------------------------------------------------------------------------


class TestMultiModuleGroupYAML:
    """One push = every module updates. Twiddle the YAML, push again,
    confirm the diff lands per-module."""

    async def test_full_module_sweep(self, db):
        bare_dir, clone_dir = _setup_bare_repo()
        import_dir = Path(tempfile.mkdtemp(prefix="labdog-e2e-import-"))

        try:
            # The group YAML references group "e2e-multi" (in YAML's own
            # `group:` key). The DB row name doesn't have to match — the
            # importer is keyed by group_id, the YAML's `group:` is
            # informational. Use a unique DB name so concurrent tests
            # don't collide on the unique constraint.
            group = await create_group(
                db,
                name=f"e2e-multi-{uuid.uuid4().hex[:6]}",
                priority=200 + int(uuid.uuid4().int % 700) + 1,
            )
            group.gitops_enabled = True
            group.gitops_file_path = "groups/e2e-multi.yaml"
            group.gitops_status = GitOpsStatus.disconnected
            await db.flush()

            # ---- Push 1: everything new ---------------------------------
            sha1 = _push_files(
                clone_dir,
                {"groups/e2e-multi.yaml": MULTI_MODULE_YAML},
                "Add e2e-multi",
            )
            _, import_dir = clone_repo_local(str(bare_dir), import_dir)
            content = read_file_at_sha(import_dir, "groups/e2e-multi.yaml", sha1)

            result = await import_group_from_yaml(
                group_id=group.id,
                yaml_content=content,
                commit_sha=sha1,
                db=db,
            )
            assert result.success is True, result.error_message

            # Every module dispatcher should have run.
            modules_seen = {m.module for m in result.modules}
            assert {
                "firewall",
                "services",
                "packages",
                "hosts_entries",
                "cron_jobs",
                "resolver",
                "users",
                "workflow",
            } <= modules_seen

            # Per-table assertions.
            fw = (
                (
                    await db.execute(
                        select(FirewallRule).where(
                            FirewallRule.group_id == group.id,
                            FirewallRule.is_system == False,  # noqa: E712
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert {r.port_start for r in fw} == {22}

            svc = (
                (await db.execute(select(ServiceRule).where(ServiceRule.group_id == group.id)))
                .scalars()
                .all()
            )
            assert {s.service_name for s in svc} == {"nginx"}
            assert all(s.state == "running" for s in svc)

            pkgs = (
                (await db.execute(select(PackageRule).where(PackageRule.group_id == group.id)))
                .scalars()
                .all()
            )
            assert {p.package_name for p in pkgs} == {"htop", "curl"}

            entries = (
                (await db.execute(select(HostsEntry).where(HostsEntry.group_id == group.id)))
                .scalars()
                .all()
            )
            assert {e.hostname for e in entries} == {"e2e-internal.local"}

            crons = (
                (await db.execute(select(CronJob).where(CronJob.group_id == group.id)))
                .scalars()
                .all()
            )
            assert {c.name for c in crons} == {"e2e-cleanup"}

            users = (
                (await db.execute(select(LinuxUser).where(LinuxUser.group_id == group.id)))
                .scalars()
                .all()
            )
            assert {u.username for u in users} == {"e2e-bot"}

            grps = (
                (await db.execute(select(LinuxGroup).where(LinuxGroup.group_id == group.id)))
                .scalars()
                .all()
            )
            assert {g.groupname for g in grps} == {"e2e-devs"}

            resolver = await db.scalar(
                select(ResolverConfig).where(ResolverConfig.group_id == group.id)
            )
            assert resolver is not None
            assert list(resolver.nameservers) == ["1.1.1.1", "9.9.9.9"]

            # Workflow imports are now exercised through scheduled_actions
            # in test_gitops_scheduled_actions.py — the legacy `workflow:`
            # block is dropped from this fixture.

            await db.refresh(group)
            assert group.gitops_status == GitOpsStatus.synced

            # ---- Push 2: incremental diff per module --------------------
            sha2 = _push_files(
                clone_dir,
                {"groups/e2e-multi.yaml": MULTI_MODULE_YAML_V2},
                "Update e2e-multi",
            )
            subprocess.run(["git", "pull"], cwd=str(import_dir), capture_output=True, check=True)
            content_v2 = read_file_at_sha(import_dir, "groups/e2e-multi.yaml", sha2)

            result2 = await import_group_from_yaml(
                group_id=group.id,
                yaml_content=content_v2,
                commit_sha=sha2,
                db=db,
            )
            assert result2.success is True

            # Firewall: SSH unchanged + HTTPS added.
            fw2 = (
                (
                    await db.execute(
                        select(FirewallRule).where(
                            FirewallRule.group_id == group.id,
                            FirewallRule.is_system == False,  # noqa: E712
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert {r.port_start for r in fw2} == {22, 443}

            # Services: nginx flipped to stopped, cron added.
            svc2 = (
                (await db.execute(select(ServiceRule).where(ServiceRule.group_id == group.id)))
                .scalars()
                .all()
            )
            assert {s.service_name for s in svc2} == {"nginx", "cron"}
            nginx2 = next(s for s in svc2 if s.service_name == "nginx")
            assert nginx2.state == "stopped"

            # Packages: curl gone, only htop.
            pkgs2 = (
                (await db.execute(select(PackageRule).where(PackageRule.group_id == group.id)))
                .scalars()
                .all()
            )
            assert {p.package_name for p in pkgs2} == {"htop"}

            # Hosts entries: a second one added.
            entries2 = (
                (await db.execute(select(HostsEntry).where(HostsEntry.group_id == group.id)))
                .scalars()
                .all()
            )
            assert {e.hostname for e in entries2} == {"e2e-internal.local", "e2e-second.local"}

            # Cron + linux_groups + users wiped (sections absent in v2 ⇒ wipe).
            crons2 = (
                (await db.execute(select(CronJob).where(CronJob.group_id == group.id)))
                .scalars()
                .all()
            )
            assert crons2 == []

            grps2 = (
                (await db.execute(select(LinuxGroup).where(LinuxGroup.group_id == group.id)))
                .scalars()
                .all()
            )
            assert grps2 == []

            users2 = (
                (await db.execute(select(LinuxUser).where(LinuxUser.group_id == group.id)))
                .scalars()
                .all()
            )
            assert users2 == []

            # Resolver: nameservers shrunk to 1.
            resolver2 = await db.scalar(
                select(ResolverConfig).where(ResolverConfig.group_id == group.id)
            )
            assert list(resolver2.nameservers) == ["1.1.1.1"]

        finally:
            for d in [bare_dir, clone_dir, import_dir]:
                if d.exists():
                    shutil.rmtree(str(d))


# ---------------------------------------------------------------------------
# `_global.yaml` round-trip
# ---------------------------------------------------------------------------


class TestGlobalYAML:
    """`_global.yaml` at the repo root imports drift + discovery.

    Verifies cross-reference resolution by name (ssh_key, default_groups)
    against pre-existing DB rows.
    """

    async def test_drift_and_discovery_round_trip(self, db):
        invalidate_cache()
        bare_dir, clone_dir = _setup_bare_repo()
        import_dir = Path(tempfile.mkdtemp(prefix="labdog-e2e-import-"))

        try:
            # Group + SSH key the YAML will reference.
            group = await create_group(
                db,
                name="e2e-multi",  # matches GLOBAL_YAML_V1.discovery[0].default_groups
                priority=int(uuid.uuid4().int % 1000) + 1,
            )
            await create_ssh_key(db, name="e2e-key")
            await db.flush()

            sha = _push_files(
                clone_dir,
                {"_global.yaml": GLOBAL_YAML_V1},
                "Add _global.yaml",
            )
            _, import_dir = clone_repo_local(str(bare_dir), import_dir)
            content = read_file_at_sha(import_dir, "_global.yaml", sha)

            result = await import_global_from_yaml(
                repo_id=42,  # advisory-lock scope only; doesn't have to be a real repo.
                yaml_content=content,
                commit_sha=sha,
                db=db,
            )
            assert result.success is True, result.error_message

            drift_setting = await db.scalar(
                select(AppSetting).where(AppSetting.key == "drift.check_interval_minutes")
            )
            assert drift_setting is not None
            assert drift_setting.value == "12"

            scans = (
                (await db.execute(select(ScanConfig).where(ScanConfig.name == "e2e-scan")))
                .scalars()
                .all()
            )
            assert len(scans) == 1
            scan = scans[0]
            assert scan.cidrs == ["10.30.0.0/24"]
            assert scan.interval_minutes == 60
            assert scan.default_group_ids == [group.id]

        finally:
            for d in [bare_dir, clone_dir, import_dir]:
                if d.exists():
                    shutil.rmtree(str(d))


# ---------------------------------------------------------------------------
# Webhook receiver — full HMAC-signed POST → task body
# ---------------------------------------------------------------------------


def _sign_github(secret: str, body: bytes) -> str:
    """Compute the GitHub-style X-Hub-Signature-256 header value."""
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _make_file_url_repo(db, name: str, bare_dir: Path, secret: str) -> GitRepository:
    """Persist a GitRepository row that points at a local bare repo.

    `GitAuthType` doesn't have a ``none`` member yet — its enum only
    declares ``ssh_key`` and ``https_token``. ``app/gitops/git_service.py``
    has an ``else: # No auth — local or public repo`` branch that's
    currently unreachable from any DB-backed row. For the test we use
    ``ssh_key`` as a placeholder and patch :func:`clone_repo` (in the
    caller) so the auth path is bypassed; the row's ``auth_type`` is
    never actually consulted because we substitute ``clone_repo_local``.
    Adding a real ``GitAuthType.none`` member is a schema change worth
    doing on its own, not bundled with the E2E test.
    """
    repo = GitRepository(
        name=name,
        url=f"file://{bare_dir}",
        branch="main",
        auth_type=GitAuthType.ssh_key,
        webhook_secret=secret,
    )
    db.add(repo)
    return repo


def _patch_clone_for_file_url():
    """Replace `app.gitops.git_service.clone_repo` with a `file://`-aware shim.

    The shim ignores `repo.auth_type` and delegates to
    ``clone_repo_local`` when the URL starts with ``file://``.
    Returns a context manager — patch must target the *source* module
    (not the consumer), because :func:`_process_webhook_async`
    lazy-imports inside the function body.
    """
    from app.gitops.git_service import clone_repo_local as _local

    def _shim(repo, encrypted_ssh_key=None, target_dir=None):
        if target_dir is None:
            target_dir = Path(tempfile.mkdtemp(prefix="labdog-e2e-task-clone-"))
        url = repo.url.removeprefix("file://")
        return _local(url, target_dir, branch=repo.branch)

    return patch("app.gitops.git_service.clone_repo", new=_shim)


def _patch_task_session_to_use(test_db):
    """Make `_process_webhook_async`'s `task_session()` yield the test session.

    The task normally opens a fresh asyncpg engine via :func:`task_session`,
    which is correct for production (forked Celery workers) but invisible
    to tests because the per-test DB fixture writes through a savepoint
    that's never really committed. Substituting the test's session
    routes the task's reads/writes back through the savepoint so the
    test can both see what the task wrote and roll the whole tree back
    cleanly at teardown.
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _session_yielder():
        yield test_db

    # `_process_webhook_async` does `from app.db import task_session` lazily
    # inside the function body — patching `app.db.task_session` (the source
    # attribute) means the lazy import inside the task picks up our shim.
    return patch("app.db.task_session", new=_session_yielder)


def _github_push_payload(repo_url: str, sha: str, branch: str = "main") -> dict:
    """Minimal GitHub `push` event payload — only the keys the receiver reads."""
    return {
        "ref": f"refs/heads/{branch}",
        "after": sha,
        "deleted": False,
        "repository": {
            "clone_url": repo_url,
            "ssh_url": repo_url,
        },
    }


class TestWebhookReceiver:
    """End-to-end: HMAC-signed GitHub webhook → receiver → import → DB.

    The receiver normally calls `celery_app.send_task("gitops.process_webhook")`.
    We patch that to capture the kwargs and then invoke the task body
    `_process_webhook_async` inline against the test's DB session — the
    only way to make the celery hop synchronous within a single pytest
    session.
    """

    async def test_signed_push_imports_yaml(self, db, app):
        import httpx
        from httpx import ASGITransport

        bare_dir, clone_dir = _setup_bare_repo()

        try:
            # Push the YAML the webhook should trigger import of.
            sha = _push_files(
                clone_dir,
                {
                    "groups/e2e-webhook.yaml": MULTI_MODULE_YAML.replace(
                        "group: e2e-multi", "group: e2e-webhook"
                    )
                },
                "Add e2e-webhook YAML",
            )

            secret = "e2e-test-webhook-secret"  # noqa: S105 — test fixture
            repo = _make_file_url_repo(db, f"e2e-webhook-{uuid.uuid4().hex[:6]}", bare_dir, secret)
            await db.flush()

            group = await create_group(
                db,
                name=f"e2e-webhook-{uuid.uuid4().hex[:6]}",
                priority=300 + int(uuid.uuid4().int % 600) + 1,
            )
            group.gitops_enabled = True
            group.git_repository_id = repo.id
            group.gitops_file_path = "groups/e2e-webhook.yaml"
            group.gitops_status = GitOpsStatus.disconnected
            await db.flush()
            await db.commit()  # webhook receiver opens a fresh session via task_session

            # Build + sign the payload.
            payload = _github_push_payload(repo.url, sha)
            body = json.dumps(payload).encode()
            sig = _sign_github(secret, body)

            # Hook celery.send_task to capture kwargs instead of dispatching.
            captured: dict = {}

            def fake_send_task(name, kwargs=None, **_):
                captured["name"] = name
                captured["kwargs"] = kwargs

            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
                with patch("app.api.webhooks.celery_app.send_task", new=fake_send_task):
                    resp = await ac.post(
                        "/webhooks/github",
                        content=body,
                        headers={
                            "Content-Type": "application/json",
                            "X-GitHub-Event": "push",
                            "X-Hub-Signature-256": sig,
                        },
                    )

            assert resp.status_code == 200, resp.text
            assert resp.json()["status"] == "accepted"
            assert captured["name"] == "gitops.process_webhook"
            assert captured["kwargs"]["repo_id"] == repo.id
            assert captured["kwargs"]["commit_sha"] == sha

            # Now invoke the task body inline. It opens its own session via
            # task_session() and commits, so the test session sees the
            # imported rows when it re-queries.
            from app.tasks.gitops import _process_webhook_async

            class _FakeTask:
                def retry(self, exc):
                    raise exc

            # Patch clone_repo to handle our `file://` URL (the auth_type
            # placeholder on the row would otherwise fail at clone time),
            # the celery delay so we don't spawn ansible, and task_session
            # so the task sees rows the test session wrote under savepoints.
            with (
                _patch_clone_for_file_url(),
                _patch_task_session_to_use(db),
                patch("app.tasks.sync.run_sync_playbook.delay"),
            ):
                await _process_webhook_async(_FakeTask(), repo.id, sha)

            # Refresh the group from DB and verify import landed.
            await db.refresh(group)
            assert group.gitops_status == GitOpsStatus.synced
            assert group.gitops_last_import_at is not None

            fw = (
                (
                    await db.execute(
                        select(FirewallRule).where(
                            FirewallRule.group_id == group.id,
                            FirewallRule.is_system == False,  # noqa: E712
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert {r.port_start for r in fw} == {22}

            svc = (
                (await db.execute(select(ServiceRule).where(ServiceRule.group_id == group.id)))
                .scalars()
                .all()
            )
            assert {s.service_name for s in svc} == {"nginx"}

        finally:
            for d in [bare_dir, clone_dir]:
                if d.exists():
                    shutil.rmtree(str(d))

    async def test_invalid_signature_is_rejected(self, db, app):
        """A wrong HMAC signature must produce 401, no import side effects."""
        import httpx
        from httpx import ASGITransport

        bare_dir, clone_dir = _setup_bare_repo()
        try:
            sha = _push_files(
                clone_dir,
                {"groups/x.yaml": "group: x\nfirewall:\n  rules: []\n"},
                "init",
            )

            _make_file_url_repo(
                db,
                f"e2e-bad-sig-{uuid.uuid4().hex[:6]}",
                bare_dir,
                secret="real-secret",  # noqa: S106
            )
            await db.flush()
            await db.commit()

            payload = _github_push_payload(f"file://{bare_dir}", sha)
            body = json.dumps(payload).encode()
            wrong_sig = _sign_github("wrong-secret", body)

            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
                resp = await ac.post(
                    "/webhooks/github",
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-GitHub-Event": "push",
                        "X-Hub-Signature-256": wrong_sig,
                    },
                )

            assert resp.status_code == 401
            assert "Invalid signature" in resp.json()["detail"]

        finally:
            for d in [bare_dir, clone_dir]:
                if d.exists():
                    shutil.rmtree(str(d))

    async def test_branch_mismatch_is_ignored(self, db, app):
        """Push on a non-tracked branch returns 200 ignored — no import."""
        import httpx
        from httpx import ASGITransport

        bare_dir, clone_dir = _setup_bare_repo()
        try:
            sha = _push_files(
                clone_dir,
                {"groups/x.yaml": "group: x\nfirewall:\n  rules: []\n"},
                "init",
            )

            secret = "branch-test"  # noqa: S105
            _make_file_url_repo(db, f"e2e-branch-{uuid.uuid4().hex[:6]}", bare_dir, secret)
            await db.flush()
            await db.commit()

            # Push pretending to be on a different branch.
            payload = _github_push_payload(f"file://{bare_dir}", sha, branch="feature/other")
            body = json.dumps(payload).encode()
            sig = _sign_github(secret, body)

            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
                resp = await ac.post(
                    "/webhooks/github",
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-GitHub-Event": "push",
                        "X-Hub-Signature-256": sig,
                    },
                )

            assert resp.status_code == 200
            assert resp.json()["status"] == "ignored"

        finally:
            for d in [bare_dir, clone_dir]:
                if d.exists():
                    shutil.rmtree(str(d))
