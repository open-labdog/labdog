"""Checking an automatic fix afterwards, and rolling back one that made
the host worse.

The rules pinned here pull against each other. A host the fix broke must
be put back without anyone asking, because nobody is watching. A host the
fix merely failed to mend must not be, because a rollback restarts the
machine and throws away everything written since the snapshot. And the
one machine LabDog runs on must never be rolled back at all, by anyone,
because the rollback would take LabDog down with nothing left to bring
the machine back.

Nothing here touches a real host or hypervisor: SSH and Proxmox are
stand-ins that record what they were asked to do.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import select

from app.ai import labdog_host, remediation
from app.ai.alert_autonomy import MIN_WEBHOOK_TOKEN_CHARS, resolve
from app.ai.loop import AgentLoop, LoopCaps
from app.ai.models import AIRollback, AISession, AIToolCall, AlertEvent
from app.config import settings
from app.models.app_setting import AppSetting
from app.models.audit_log import AuditLog
from app.notifications.models import Notification, NotificationSubscription, SMTPSettings
from app.settings_service import invalidate_cache
from app.ssh_utils import HostKeyMismatchError
from app.tasks import celery_app
from tests.ai.fake_provider import FakeProvider, ScriptedTurn, call
from tests.conftest import create_host, create_ssh_key

ALERT = "NginxDown"
TOKEN = "t" * MIN_WEBHOOK_TOKEN_CHARS
REMOTE_LABDOG = "10.9.9.9"


async def _set(db, key: str, value: str) -> None:
    existing = (
        await db.execute(select(AppSetting).where(AppSetting.key == key))
    ).scalar_one_or_none()
    if existing:
        existing.value = value
    else:
        db.add(AppSetting(key=key, value=value))
    await db.flush()
    invalidate_cache(key)


@pytest.fixture(autouse=True)
async def _clear_settings_cache():
    invalidate_cache()
    yield
    invalidate_cache()


@pytest.fixture(autouse=True)
def _forget_where_labdog_runs():
    labdog_host.forget()
    yield
    labdog_host.forget()


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """The probe's pauses and the busy wait, without the waiting."""
    monkeypatch.setattr(remediation, "PROBE_PAUSE_SECONDS", 0)
    monkeypatch.setattr(remediation, "BUSY_POLL_SECONDS", 0)


# ---------------------------------------------------------------------------
# Stand-ins
# ---------------------------------------------------------------------------


class FakeSSH:
    """Stands in for ``ssh_connect_host``: answers, refuses, or has a new key.

    ``client_ip`` and ``interfaces`` are what the host reports for the
    LabDog-host probe: where the connection came from, and ``ip -o addr``.
    """

    def __init__(self) -> None:
        self.reachable = True
        self.key_changed = False
        self.client_ip = REMOTE_LABDOG
        self.interfaces = ["2: eth0    inet 10.0.0.21/24 brd 10.0.0.255 scope global eth0"]
        self.connects = 0
        self.commands: list[str] = []

    def __call__(self, host, db, client_keys=None, connect_timeout=None):  # noqa: ARG002
        fake = self

        class _Conn:
            async def run(self, command, check=False):  # noqa: ARG002
                fake.commands.append(command)
                stdout = ""
                if command == labdog_host.PROBE_COMMAND:
                    stdout = "\n".join([f"{fake.client_ip} 51515 22", *fake.interfaces]) + "\n"
                return SimpleNamespace(stdout=stdout, stderr="", exit_status=0)

        @asynccontextmanager
        async def _connect():
            fake.connects += 1
            if fake.key_changed:
                raise HostKeyMismatchError("host key for 10.0.0.21 does not match")
            if not fake.reachable:
                raise OSError("Connect call failed: connection refused")
            yield _Conn()

        return _connect()


@pytest.fixture
def ssh():
    fake = FakeSSH()
    with (
        patch("app.ssh_utils.ssh_connect_host", fake),
        patch("app.ai.tools.ssh.ssh_connect_host", fake),
    ):
        yield fake


class FakeProxmox:
    """The hypervisor. ``on_vm`` is the VM's snapshots, oldest first, and
    ``storage_type`` the type of the storage its disk is on."""

    def __init__(self) -> None:
        self.created: list[str] = []
        self.deleted: list[str] = []
        self.on_vm: list[str] = ["labdog-ai-snap-1"]
        self.storage_type = "zfspool"
        self.api_down = False

    async def create_snapshot(self, pve_node, vmid, name, description="", *, vm_type="qemu"):  # noqa: ARG002
        self.created.append(name)
        self.on_vm.append(name)
        return f"UPID:{name}"

    async def delete_snapshot(self, pve_node, vmid, name, *, vm_type="qemu"):  # noqa: ARG002
        self.deleted.append(name)
        if name in self.on_vm:
            self.on_vm.remove(name)
        return f"UPID:rm:{name}"

    async def list_snapshots(self, pve_node, vmid, *, vm_type="qemu"):  # noqa: ARG002
        taken = [{"name": n, "snaptime": 1_790_000_000 + i} for i, n in enumerate(self.on_vm)]
        return [*taken, {"name": "current", "parent": self.on_vm[-1] if self.on_vm else None}]

    async def get_vm_config(self, pve_node, vmid, *, vm_type="qemu"):  # noqa: ARG002
        return {
            "scsi0": "tank:vm-210-disk-0,size=32G",
            "ide2": "local:iso/debian.iso,media=cdrom",
            "net0": "virtio=BC:24:11:00:00:01,bridge=vmbr0",
        }

    async def list_node_storage(self, pve_node):  # noqa: ARG002
        return [
            {"storage": "tank", "type": self.storage_type},
            {"storage": "local", "type": "dir"},
        ]

    async def get_vm_status(self, pve_node, vmid, *, vm_type="qemu"):  # noqa: ARG002
        if self.api_down:
            raise httpx.ConnectError("All connection attempts failed")
        return {"status": "running"}

    async def test_connection(self):
        if self.api_down:
            raise httpx.ConnectError("All connection attempts failed")
        return {"version": "8.4"}

    async def wait_for_task(self, *args, **kwargs):  # noqa: ARG002
        return None


@pytest.fixture
def proxmox():
    """The hypervisor, and the rollback step that restarts a VM and waits
    for SSH. ``restored`` lists what was rolled back to; set ``succeeds``
    to make the rollback report failure."""
    client = FakeProxmox()
    client.restored = []
    client.succeeds = True

    async def _rollback(proxmox_client, pve_node, vmid, snapshot_name, host, key, db, *, vm_type):  # noqa: ARG001
        client.restored.append(snapshot_name)
        if client.succeeds:
            return {"success": True}
        return {"success": False, "error": "SSH did not recover within 300s"}

    with (
        patch("app.proxmox.client.ProxmoxClient", return_value=client),
        patch("app.workflows.steps.rollback.rollback_to_snapshot", side_effect=_rollback),
    ):
        yield client


def _session_cm(db):
    @asynccontextmanager
    async def _cm():
        yield db

    return _cm


@pytest.fixture
def tasks_use_test_db(db):
    with patch("app.db.task_session", new=_session_cm(db)):
        yield


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------


async def _mapped_host(db, *, ip: str = "10.0.0.21"):
    """A host LabDog can reach, snapshot and roll back."""
    from app.crypto.encryption import encrypt_ssh_key
    from app.crypto.key_management import get_master_key
    from app.proxmox.models import ProxmoxNode
    from app.proxmox.vm_mapping import VMMapping

    key = await create_ssh_key(db)
    host = await create_host(db, ip=ip, ssh_key_id=key.id)
    node = ProxmoxNode(
        name=f"pve-{uuid.uuid4().hex[:6]}",
        api_url="https://pve.test:8006",
        token_id="labdog@pve!ci",
        encrypted_token_secret=encrypt_ssh_key("secret", get_master_key()),
        verify_ssl=False,
    )
    db.add(node)
    await db.flush()
    db.add(
        VMMapping(
            host_id=host.id,
            proxmox_node_id=node.id,
            pve_node_name="pve1",
            vmid=210,
            vm_name="vm-210",
            vm_type="qemu",
        )
    )
    await db.flush()
    return host


async def _alert(
    db,
    host_id: int,
    *,
    alertname: str = ALERT,
    status: str = "firing",
    severity: str = "critical",
    starts_at: datetime | None = None,
    fingerprint: str | None = None,
) -> AlertEvent:
    event = AlertEvent(
        source="grafana_webhook",
        fingerprint=fingerprint or uuid.uuid4().hex,
        alertname=alertname,
        severity=severity,
        status=status,
        labels={"alertname": alertname},
        annotations={},
        starts_at=starts_at or datetime.now(UTC) - timedelta(minutes=30),
        host_id=host_id,
    )
    db.add(event)
    await db.flush()
    return event


async def _fix(
    db,
    ai_provider,
    host,
    *,
    finished_ago: timedelta = timedelta(minutes=11),
    status: str = "succeeded",
    autonomy: str = "full_auto",
    snapshots: tuple[str | None, ...] = ("labdog-ai-snap-1",),
    call_status: str = "executed",
    classification: str = "mutating",
    alert_status: str = "firing",
) -> tuple[AlertEvent, AISession, list[AIToolCall]]:
    """An alert, its session, and the commands that session ran."""
    now = datetime.now(UTC)
    event = await _alert(db, host.id, status=alert_status)
    session = AISession(
        provider_id=ai_provider.id,
        mode="alert_investigation",
        mission="fix it",
        autonomy_level=autonomy,
        status=status,
        target_host_ids=[host.id],
        alert_event_id=event.id,
        started_at=now - finished_ago - timedelta(minutes=3),
        finished_at=None if status in ("queued", "running") else now - finished_ago,
    )
    db.add(session)
    await db.flush()
    event.investigation_session_id = session.id
    event.investigation_outcome = "started"
    event.investigation_autonomy = autonomy
    calls = []
    for i, name in enumerate(snapshots):
        call_row = AIToolCall(
            session_id=session.id,
            tool_name="run_ssh_command",
            arguments={"host_id": host.id, "command": f"systemctl restart nginx  # {i}"},
            classification=classification,
            target_host_id=host.id,
            status=call_status,
            snapshot_name=name,
            started_at=now - finished_ago - timedelta(minutes=2) + timedelta(seconds=i),
        )
        db.add(call_row)
        calls.append(call_row)
    await db.flush()
    return event, session, calls


# ---------------------------------------------------------------------------
# Finding what is due
# ---------------------------------------------------------------------------


class TestWhatIsDue:
    async def test_a_fix_is_checked_once_the_settle_time_has_passed(self, db, ai_provider) -> None:
        host = await create_host(db)
        event, _, _ = await _fix(db, ai_provider, host)
        due, overdue = await remediation.find_due(db, datetime.now(UTC))
        assert event.id in due
        assert event.id not in overdue

    async def test_not_before(self, db, ai_provider) -> None:
        host = await create_host(db)
        event, _, _ = await _fix(db, ai_provider, host, finished_ago=timedelta(minutes=4))
        due, overdue = await remediation.find_due(db, datetime.now(UTC))
        assert event.id not in due + overdue

    async def test_the_settle_time_is_a_setting(self, db, ai_provider) -> None:
        await _set(db, "ai.alert_remediation_check_minutes", "3")
        host = await create_host(db)
        event, _, _ = await _fix(db, ai_provider, host, finished_ago=timedelta(minutes=4))
        due, _ = await remediation.find_due(db, datetime.now(UTC))
        assert event.id in due

    @pytest.mark.parametrize(
        "over",
        [
            {"autonomy": "approval"},
            {"autonomy": "read_only"},
            {"classification": "read_only"},
            {"call_status": "blocked"},
            {"snapshots": ()},
            {"status": "running"},
            {"status": "cancelled"},
        ],
        ids=[
            "approval",
            "read-only",
            "only-reads",
            "blocked-write",
            "no-commands",
            "running",
            "cancelled",
        ],
    )
    async def test_only_a_finished_full_auto_fix_that_changed_the_host(
        self, db, ai_provider, over
    ) -> None:
        """A person decided every change at approval, and nothing changed
        in the others — there is no unattended fix to judge. A cancelled
        session was stopped by someone, most likely to take the host over:
        what a check found would be their work, not the model's."""
        host = await create_host(db)
        event, _, _ = await _fix(db, ai_provider, host, **over)
        due, overdue = await remediation.find_due(db, datetime.now(UTC))
        assert event.id not in due + overdue

    async def test_a_failed_command_still_counts(self, db, ai_provider) -> None:
        """A restart that exited non-zero still reached the host."""
        host = await create_host(db)
        event, _, _ = await _fix(db, ai_provider, host, call_status="error")
        due, _ = await remediation.find_due(db, datetime.now(UTC))
        assert event.id in due

    async def test_a_check_too_late_to_make_is_reported_rather_than_made(
        self, db, ai_provider
    ) -> None:
        host = await create_host(db)
        event, _, _ = await _fix(db, ai_provider, host, finished_ago=timedelta(minutes=45))
        due, overdue = await remediation.find_due(db, datetime.now(UTC))
        assert event.id in overdue
        assert event.id not in due

    async def test_fixes_from_before_the_check_existed_are_left_alone(
        self, db, ai_provider
    ) -> None:
        host = await create_host(db)
        event, _, _ = await _fix(db, ai_provider, host, finished_ago=timedelta(days=2))
        due, overdue = await remediation.find_due(db, datetime.now(UTC))
        assert event.id not in due + overdue

    async def test_one_check_per_fix(self, db, ai_provider) -> None:
        host = await create_host(db)
        event, _, _ = await _fix(db, ai_provider, host)
        now = datetime.now(UTC)
        assert await remediation.claim(db, event.id, now)
        assert not await remediation.claim(db, event.id, now)
        due, _ = await remediation.find_due(db, now)
        assert event.id not in due

    async def test_what_a_killed_worker_left_running_is_closed(self, db, ai_provider) -> None:
        host = await create_host(db)
        event, session, _ = await _fix(db, ai_provider, host)
        long_ago = datetime.now(UTC) - timedelta(hours=2)
        event.remediation_outcome = "checking"
        event.remediation_checked_at = long_ago
        db.add(
            AIRollback(
                session_id=session.id,
                host_id=host.id,
                hostname=host.hostname,
                trigger="automatic",
                status="running",
                started_at=long_ago,
            )
        )
        await db.flush()

        abandoned, freed = await remediation.release_abandoned(db, datetime.now(UTC))

        assert abandoned == [event.id]
        assert freed == [host.id]
        await db.refresh(event)
        assert event.remediation_outcome == "unchecked"
        rollback = (
            await db.execute(select(AIRollback).where(AIRollback.session_id == session.id))
        ).scalar_one()
        await db.refresh(rollback)
        assert rollback.status == "failed"
        assert "Proxmox" in rollback.detail


# ---------------------------------------------------------------------------
# Judging the fix
# ---------------------------------------------------------------------------


class TestJudgingTheFix:
    async def test_resolved_and_reachable_is_fixed(self, db, ai_provider, ssh) -> None:
        host = await _mapped_host(db)
        event, session, _ = await _fix(db, ai_provider, host, alert_status="resolved")
        verdict = await remediation.assess(db, event, session)
        assert verdict.outcome == "fixed"
        assert host.hostname in verdict.detail

    async def test_still_firing_is_not_effective(self, db, ai_provider, ssh) -> None:
        host = await _mapped_host(db)
        event, session, _ = await _fix(db, ai_provider, host)
        verdict = await remediation.assess(db, event, session)
        assert verdict.outcome == "not_effective"
        assert "not rolled back" in verdict.detail

    async def test_the_same_alert_firing_again_is_not_effective(self, db, ai_provider, ssh) -> None:
        """Resolved for a moment and back: the fix did not hold. That is a
        fix that failed, not one that broke something else."""
        host = await _mapped_host(db)
        event, session, _ = await _fix(db, ai_provider, host, alert_status="resolved")
        await _alert(
            db,
            host.id,
            fingerprint=event.fingerprint,
            starts_at=datetime.now(UTC) - timedelta(minutes=2),
        )
        verdict = await remediation.assess(db, event, session)
        assert verdict.outcome == "not_effective"

    async def test_an_unreachable_host_is_worse(self, db, ai_provider, ssh, proxmox) -> None:
        host = await _mapped_host(db)
        event, session, _ = await _fix(db, ai_provider, host, alert_status="resolved")
        ssh.reachable = False
        verdict = await remediation.assess(db, event, session)
        assert verdict.outcome == "made_worse"
        assert "can no longer reach" in verdict.detail
        assert ssh.connects == remediation.PROBE_ATTEMPTS

    async def test_a_timeout_says_so(self, db, ai_provider, ssh, proxmox) -> None:
        """Seen live: an unanswered SYN surfaces as a bare ``TimeoutError``."""
        host = await _mapped_host(db)
        event, session, _ = await _fix(db, ai_provider, host, alert_status="resolved")

        def silent(host_, db_, client_keys=None, connect_timeout=None):  # noqa: ARG001
            @asynccontextmanager
            async def _connect():
                raise TimeoutError
                yield  # pragma: no cover

            return _connect()

        with patch("app.ssh_utils.ssh_connect_host", silent):
            verdict = await remediation.assess(db, event, session)
        assert verdict.outcome == "made_worse"
        assert verdict.detail.endswith("the last with: timed out.")

    async def test_an_outage_on_labdogs_side_is_not_held_against_the_host(
        self, db, ai_provider, ssh, proxmox
    ) -> None:
        """LabDog's own route or DNS gone: neither the host nor Proxmox
        answers, and that says nothing about the fix."""
        host = await _mapped_host(db)
        event, session, _ = await _fix(db, ai_provider, host, alert_status="resolved")
        ssh.reachable = False
        proxmox.api_down = True
        verdict = await remediation.assess(db, event, session)
        assert verdict.outcome == "unchecked"
        assert "LabDog's side" in verdict.detail
        assert "All connection attempts failed" in verdict.detail

    async def test_a_host_without_a_vm_is_compared_against_any_proxmox_node(
        self, db, ai_provider, ssh, proxmox
    ) -> None:
        await _mapped_host(db, ip="10.0.0.40")
        key = await create_ssh_key(db)
        host = await create_host(db, ip="10.0.0.41", ssh_key_id=key.id)
        event, session, _ = await _fix(db, ai_provider, host, alert_status="resolved")
        ssh.reachable = False
        proxmox.api_down = True
        assert (await remediation.assess(db, event, session)).outcome == "unchecked"
        proxmox.api_down = False
        assert (await remediation.assess(db, event, session)).outcome == "made_worse"

    async def test_an_error_from_proxmox_is_still_an_answer(
        self, db, ai_provider, ssh, proxmox, monkeypatch
    ) -> None:
        """A refused token means the network to Proxmox works."""
        from app.proxmox.client import ProxmoxError

        async def refused(*args, **kwargs):  # noqa: ARG001
            raise ProxmoxError("permission check failed", 403)

        monkeypatch.setattr(proxmox, "get_vm_status", refused)
        host = await _mapped_host(db)
        event, session, _ = await _fix(db, ai_provider, host, alert_status="resolved")
        ssh.reachable = False
        assert (await remediation.assess(db, event, session)).outcome == "made_worse"

    async def test_a_new_alert_still_counts_when_labdog_is_cut_off(
        self, db, ai_provider, ssh, proxmox
    ) -> None:
        host = await _mapped_host(db)
        event, session, _ = await _fix(db, ai_provider, host, alert_status="resolved")
        await _alert(db, host.id, alertname="PostgresDown", starts_at=datetime.now(UTC))
        ssh.reachable = False
        proxmox.api_down = True
        verdict = await remediation.assess(db, event, session)
        assert verdict.outcome == "made_worse"
        assert "PostgresDown" in verdict.detail
        assert "LabDog's side" in verdict.detail

    async def test_one_answer_in_three_is_enough(self, db, ai_provider, ssh) -> None:
        """A host mid-restart fails one probe and answers the next."""
        host = await _mapped_host(db)
        event, session, _ = await _fix(db, ai_provider, host, alert_status="resolved")
        attempts = []

        def flaky(host_, db_, client_keys=None, connect_timeout=None):
            attempts.append(1)
            ssh.reachable = len(attempts) > 1
            return FakeSSH.__call__(ssh, host_, db_, client_keys, connect_timeout)

        with patch("app.ssh_utils.ssh_connect_host", flaky):
            verdict = await remediation.assess(db, event, session)
        assert verdict.outcome == "fixed"
        assert len(attempts) == 2

    async def test_a_changed_host_key_is_worse_and_not_retried(
        self, db, ai_provider, ssh, proxmox
    ) -> None:
        host = await _mapped_host(db)
        event, session, _ = await _fix(db, ai_provider, host, alert_status="resolved")
        ssh.key_changed = True
        verdict = await remediation.assess(db, event, session)
        assert verdict.outcome == "made_worse"
        assert "host key has changed" in verdict.detail
        assert ssh.connects == 1

    async def test_a_new_critical_alert_after_the_fix_is_worse(self, db, ai_provider, ssh) -> None:
        host = await _mapped_host(db)
        event, session, _ = await _fix(db, ai_provider, host, alert_status="resolved")
        await _alert(db, host.id, alertname="PostgresDown", starts_at=datetime.now(UTC))
        verdict = await remediation.assess(db, event, session)
        assert verdict.outcome == "made_worse"
        assert "PostgresDown" in verdict.detail

    @pytest.mark.parametrize(
        "over",
        [
            {"severity": "warning"},
            {"status": "resolved"},
            {"starts_at": datetime.now(UTC) - timedelta(hours=2)},
        ],
        ids=["only-a-warning", "already-resolved", "fired-before-the-fix"],
    )
    async def test_other_alerts_that_do_not_count(self, db, ai_provider, ssh, over) -> None:
        host = await _mapped_host(db)
        event, session, _ = await _fix(db, ai_provider, host, alert_status="resolved")
        await _alert(db, host.id, alertname="PostgresDown", **over)
        verdict = await remediation.assess(db, event, session)
        assert verdict.outcome == "fixed"

    async def test_an_alert_on_another_host_does_not_count(self, db, ai_provider, ssh) -> None:
        host = await _mapped_host(db)
        other = await create_host(db, ip="10.0.0.99")
        event, session, _ = await _fix(db, ai_provider, host, alert_status="resolved")
        await _alert(db, other.id, alertname="PostgresDown", starts_at=datetime.now(UTC))
        verdict = await remediation.assess(db, event, session)
        assert verdict.outcome == "fixed"

    async def test_with_no_key_it_is_judged_on_alerts_alone(self, db, ai_provider, ssh) -> None:
        """No key, no probe — and no way the session changed it either."""
        host = await create_host(db)
        event, session, _ = await _fix(db, ai_provider, host, alert_status="resolved")
        verdict = await remediation.assess(db, event, session)
        assert verdict.outcome == "fixed"
        assert ssh.connects == 0

    async def test_the_outcome_is_recorded_and_audited(self, db, ai_provider) -> None:
        host = await create_host(db)
        event, session, _ = await _fix(db, ai_provider, host)
        assert await remediation.claim(db, event.id, datetime.now(UTC))
        verdict = remediation.Assessment("not_effective", "still firing")
        assert await remediation.record_outcome(db, event, verdict, datetime.now(UTC))
        assert event.remediation_outcome == "not_effective"
        audit = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.action == "ai_remediation_checked", AuditLog.entity_id == event.id
                )
            )
        ).scalar_one()
        assert audit.after_state["session_id"] == session.id

    async def test_a_check_closed_while_it_ran_is_left_closed(self, db, ai_provider) -> None:
        """The sweep marked it abandoned and said so by email; a verdict
        now would contradict that."""
        host = await create_host(db)
        event, _, _ = await _fix(db, ai_provider, host)
        event.remediation_outcome = "unchecked"
        await db.flush()
        verdict = remediation.Assessment("made_worse", "unreachable")
        assert not await remediation.record_outcome(db, event, verdict, datetime.now(UTC))
        await db.refresh(event)
        assert event.remediation_outcome == "unchecked"


# ---------------------------------------------------------------------------
# Which host LabDog runs on
# ---------------------------------------------------------------------------

DOCKER_HOST = [
    "1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever preferred_lft forever",
    "2: eth0    inet 10.10.101.5/24 brd 10.10.101.255 scope global eth0\\       valid_lft forever",
    "3: docker0    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0",
    "5: br-3f2a1b    inet 172.18.0.1/16 brd 172.18.255.255 scope global br-3f2a1b",
    "2: eth0    inet6 fe80::1/64 scope link \\       valid_lft forever",
]


class TestWhereLabDogRuns:
    def test_the_probe_output_is_read(self) -> None:
        client, addresses = labdog_host.parse_probe(
            "\n".join(["172.18.0.4 40022 22", *DOCKER_HOST])
        )
        assert client == "172.18.0.4"
        names = [name for name, _ in addresses]
        assert names == ["lo", "eth0", "docker0", "br-3f2a1b", "eth0"]

    @pytest.mark.parametrize(
        ("client", "expected"),
        [
            ("172.18.0.4", True),  # a container on a compose network on this host
            ("172.17.0.2", True),  # one on the default bridge
            ("10.10.101.5", True),  # host networking, or installed natively
            ("10.10.101.7", False),  # another machine on the same LAN
            ("10.20.0.4", False),  # another machine somewhere else
            ("fe80::1", True),
            ("not-an-ip", False),
        ],
    )
    def test_which_connections_come_from_this_machine(self, client, expected) -> None:
        _, addresses = labdog_host.parse_probe("\n".join(["x 1 22", *DOCKER_HOST]))
        assert labdog_host.runs_here(client, addresses) is expected

    def test_a_lan_bridge_is_not_a_container_bridge(self) -> None:
        """A hypervisor's ``vmbr0`` carries its LAN address; a LabDog
        elsewhere on that LAN is inside its subnet and is still elsewhere."""
        _, addresses = labdog_host.parse_probe(
            "x 1 22\n2: vmbr0    inet 10.10.10.2/24 brd 10.10.10.255 scope global vmbr0"
        )
        assert not labdog_host.runs_here("10.10.10.9", addresses)

    async def test_other_hosts_seeing_labdog_from_its_address(self, db) -> None:
        """lin-manager: every other host records LabDog as 10.10.101.5."""
        here = await create_host(db, ip="10.10.101.5")
        await create_host(db, ip="10.10.10.51")
        other = await create_host(db, ip="10.10.10.52")
        other.labdog_source_ip = "10.10.101.5"
        await db.flush()
        assert await labdog_host.labdog_runs_on(db, here, ask=False)
        assert not await labdog_host.labdog_runs_on(db, other, ask=False)

    async def test_the_configured_server_address(self, db, monkeypatch) -> None:
        """``[security] labdog_server_ip`` is the operator saying so."""
        monkeypatch.setattr(settings.security, "labdog_server_ip", "10.10.101.5")
        here = await create_host(db, ip="10.10.101.5")
        elsewhere = await create_host(db, ip="10.10.101.6")
        assert await labdog_host.labdog_runs_on(db, here, ask=False)
        assert not await labdog_host.labdog_runs_on(db, elsewhere, ask=False)

    async def test_a_host_seeing_labdog_from_its_own_address(self, db) -> None:
        here = await create_host(db, ip="10.10.101.5")
        here.labdog_source_ip = "10.10.101.5"
        await db.flush()
        assert await labdog_host.labdog_runs_on(db, here, ask=False)

    async def test_the_host_is_asked_when_the_database_cannot_tell(self, db, ssh) -> None:
        """The only host LabDog manages, in a container on it: nothing in
        the database gives it away, the host's own interfaces do."""
        here = await _mapped_host(db, ip="10.10.101.5")
        ssh.client_ip = "172.18.0.4"
        ssh.interfaces = DOCKER_HOST
        assert await labdog_host.labdog_runs_on(db, here)
        assert labdog_host.PROBE_COMMAND in ssh.commands

    async def test_an_unreachable_host_is_not_assumed_to_be_it(self, db, ssh) -> None:
        here = await _mapped_host(db)
        ssh.reachable = False
        assert not await labdog_host.labdog_runs_on(db, here)

    @pytest.mark.parametrize("reachable", [True, False], ids=["answered", "could-not-ask"])
    async def test_the_hosts_answer_is_kept(self, db, ssh, reachable) -> None:
        """Every full-auto decision asks, and an alerting host is often
        down: one connect timeout per host, not one per alert."""
        here = await _mapped_host(db)
        ssh.reachable = reachable
        for _ in range(3):
            await labdog_host.labdog_runs_on(db, here)
        assert ssh.connects == 1

    async def test_could_not_ask_is_kept_for_less(self, db, ssh, monkeypatch) -> None:
        here = await _mapped_host(db)
        ssh.reachable = False
        monkeypatch.setattr(labdog_host, "NO_ANSWER_TTL_SECONDS", -1)
        await labdog_host.labdog_runs_on(db, here)
        await labdog_host.labdog_runs_on(db, here)
        assert ssh.connects == 2


# ---------------------------------------------------------------------------
# Rolling back
# ---------------------------------------------------------------------------


class TestRollingBack:
    async def test_it_restores_the_snapshot_from_before_the_first_change(
        self, db, ai_provider, ssh, proxmox
    ) -> None:
        """Later snapshots go first, newest first: ZFS refuses to roll back
        past a newer snapshot."""
        host = await _mapped_host(db)
        _, session, calls = await _fix(db, ai_provider, host, snapshots=("s1", "s2", "s3"))
        proxmox.on_vm = ["s1", "s2", "s3"]

        rollback, plan = await remediation.prepare_rollback(
            db, session=session, host_id=host.id, trigger="manual"
        )
        await remediation.perform_rollback(db, rollback, plan)

        assert proxmox.deleted == ["s3", "s2"]
        assert proxmox.restored == ["s1"]
        assert rollback.status == "succeeded"
        assert rollback.snapshot_name == "s1"
        assert calls[0].snapshot_pruned_at is None
        assert calls[1].snapshot_pruned_at is not None
        assert calls[2].snapshot_pruned_at is not None
        audit = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.action == "ai_rollback", AuditLog.entity_id == session.id
                )
            )
        ).scalar_one()
        assert audit.after_state["status"] == "succeeded"

    @pytest.mark.parametrize("storage", ["lvmthin", "dir", "rbd"])
    async def test_storage_that_restores_any_snapshot_keeps_the_later_ones(
        self, db, ai_provider, ssh, proxmox, storage
    ) -> None:
        host = await _mapped_host(db)
        _, session, calls = await _fix(db, ai_provider, host, snapshots=("s1", "s2", "s3"))
        proxmox.on_vm = ["s1", "s2", "s3"]
        proxmox.storage_type = storage
        rollback, plan = await remediation.prepare_rollback(
            db, session=session, host_id=host.id, trigger="manual"
        )
        await remediation.perform_rollback(db, rollback, plan)
        assert rollback.status == "succeeded"
        assert proxmox.deleted == []
        assert proxmox.restored == ["s1"]
        assert calls[1].snapshot_pruned_at is None

    async def test_unknown_storage_is_treated_as_zfs(
        self, db, ai_provider, ssh, proxmox, monkeypatch
    ) -> None:
        """The token may not see the storage. Deleting the session's own
        later snapshots costs less than a restore ZFS refuses."""

        async def hidden(pve_node):  # noqa: ARG001
            return []

        monkeypatch.setattr(proxmox, "list_node_storage", hidden)
        host = await _mapped_host(db)
        _, session, _ = await _fix(db, ai_provider, host, snapshots=("s1", "s2"))
        proxmox.on_vm = ["s1", "s2"]
        rollback, plan = await remediation.prepare_rollback(
            db, session=session, host_id=host.id, trigger="manual"
        )
        await remediation.perform_rollback(db, rollback, plan)
        assert proxmox.deleted == ["s2"]
        assert proxmox.restored == ["s1"]

    async def test_a_newer_snapshot_that_is_not_the_sessions_stops_it_before_anything_goes(
        self, db, ai_provider, ssh, proxmox
    ) -> None:
        """ZFS would refuse the restore anyway, and by then s2 — a valid
        restore point — would have been deleted for nothing."""
        host = await _mapped_host(db)
        _, session, calls = await _fix(db, ai_provider, host, snapshots=("s1", "s2"))
        proxmox.on_vm = ["s1", "s2", "pre-upgrade-from-an-action-run"]
        rollback, plan = await remediation.prepare_rollback(
            db, session=session, host_id=host.id, trigger="manual"
        )
        await remediation.perform_rollback(db, rollback, plan)
        assert rollback.status == "refused"
        assert "pre-upgrade-from-an-action-run" in rollback.detail
        assert "Nothing was deleted" in rollback.detail
        assert proxmox.deleted == []
        assert proxmox.restored == []
        assert calls[1].snapshot_pruned_at is None

    async def test_a_snapshot_gone_from_proxmox(self, db, ai_provider, ssh, proxmox) -> None:
        host = await _mapped_host(db)
        _, session, _ = await _fix(db, ai_provider, host, snapshots=("s1", "s2"))
        proxmox.on_vm = ["s2"]
        rollback, plan = await remediation.prepare_rollback(
            db, session=session, host_id=host.id, trigger="manual"
        )
        await remediation.perform_rollback(db, rollback, plan)
        assert rollback.status == "refused"
        assert "no longer on" in rollback.detail
        assert proxmox.deleted == []

    async def test_a_snapshot_another_rollback_went_back_past(
        self, db, ai_provider, ssh, proxmox
    ) -> None:
        """LVM-thin: S1 took s1, S2 later took s2, and S1 was rolled back to
        s1. s2 holds S1's change; restoring it would bring that back."""
        proxmox.storage_type = "lvmthin"
        host = await _mapped_host(db)
        _, earlier, _ = await _fix(
            db, ai_provider, host, snapshots=("s1",), finished_ago=timedelta(minutes=30)
        )
        _, later, _ = await _fix(db, ai_provider, host, snapshots=("s2",))
        proxmox.on_vm = ["s1", "s2"]
        rollback, plan = await remediation.prepare_rollback(
            db, session=earlier, host_id=host.id, trigger="automatic"
        )
        await remediation.perform_rollback(db, rollback, plan)
        assert rollback.status == "succeeded"

        with pytest.raises(remediation.RollbackRefused, match="bring back what that rollback"):
            await remediation.prepare_rollback(db, session=later, host_id=host.id, trigger="manual")

    async def test_going_back_further_than_another_rollback_is_allowed(
        self, db, ai_provider, ssh, proxmox
    ) -> None:
        """S2's rollback restored s2; S1's s1 is older still, so restoring it
        undoes more, never brings anything back."""
        proxmox.storage_type = "lvmthin"
        host = await _mapped_host(db)
        _, earlier, _ = await _fix(
            db, ai_provider, host, snapshots=("s1",), finished_ago=timedelta(minutes=30)
        )
        _, later, _ = await _fix(db, ai_provider, host, snapshots=("s2",))
        proxmox.on_vm = ["s1", "s2"]
        rollback, plan = await remediation.prepare_rollback(
            db, session=later, host_id=host.id, trigger="manual"
        )
        await remediation.perform_rollback(db, rollback, plan)
        rollback, plan = await remediation.prepare_rollback(
            db, session=earlier, host_id=host.id, trigger="manual"
        )
        await remediation.perform_rollback(db, rollback, plan)
        assert proxmox.restored == ["s2", "s1"]

    async def test_a_failed_rollback_says_so(self, db, ai_provider, ssh, proxmox) -> None:
        host = await _mapped_host(db)
        _, session, _ = await _fix(db, ai_provider, host)
        proxmox.succeeds = False
        rollback, plan = await remediation.prepare_rollback(
            db, session=session, host_id=host.id, trigger="manual"
        )
        await remediation.perform_rollback(db, rollback, plan)
        assert rollback.status == "failed"
        assert "SSH did not recover" in rollback.detail

    async def test_never_the_host_labdog_runs_on(self, db, ai_provider, ssh, proxmox) -> None:
        host = await _mapped_host(db, ip="10.10.101.5")
        neighbour = await create_host(db, ip="10.10.10.51")
        neighbour.labdog_source_ip = "10.10.101.5"
        _, session, _ = await _fix(db, ai_provider, host)
        with pytest.raises(remediation.RollbackRefused, match="LabDog runs on"):
            await remediation.prepare_rollback(
                db, session=session, host_id=host.id, trigger="manual"
            )
        assert proxmox.restored == []

    @pytest.mark.parametrize(
        ("snapshots", "match"),
        [((None, "s2"), "No snapshot was taken before"), ((), "did not change")],
        ids=["first-change-unprotected", "nothing-changed"],
    )
    async def test_nothing_to_restore(
        self, db, ai_provider, ssh, proxmox, snapshots, match
    ) -> None:
        """Restoring a snapshot from after the first change would leave the
        first change in place and call it undone."""
        host = await _mapped_host(db)
        _, session, _ = await _fix(db, ai_provider, host, snapshots=snapshots)
        with pytest.raises(remediation.RollbackRefused, match=match):
            await remediation.prepare_rollback(
                db, session=session, host_id=host.id, trigger="manual"
            )

    async def test_a_snapshot_retention_removed(self, db, ai_provider, ssh, proxmox) -> None:
        host = await _mapped_host(db)
        _, session, calls = await _fix(db, ai_provider, host)
        calls[0].snapshot_pruned_at = datetime.now(UTC)
        with pytest.raises(remediation.RollbackRefused, match="removed by retention"):
            await remediation.prepare_rollback(
                db, session=session, host_id=host.id, trigger="manual"
            )

    async def test_once_per_session_and_host(self, db, ai_provider, ssh, proxmox) -> None:
        """A second rollback would undo whatever happened since the first."""
        host = await _mapped_host(db)
        _, session, _ = await _fix(db, ai_provider, host)
        rollback, plan = await remediation.prepare_rollback(
            db, session=session, host_id=host.id, trigger="manual"
        )
        await remediation.perform_rollback(db, rollback, plan)
        with pytest.raises(remediation.RollbackRefused, match="already rolled back"):
            await remediation.prepare_rollback(
                db, session=session, host_id=host.id, trigger="automatic"
            )

    async def test_the_index_holds_even_without_the_lookup(self, db, ai_provider) -> None:
        """Two workers that both passed the lookup still cannot both start."""
        from sqlalchemy.exc import IntegrityError

        host = await create_host(db)
        _, session, _ = await _fix(db, ai_provider, host)
        for _ in range(2):
            db.add(
                AIRollback(
                    session_id=session.id,
                    host_id=host.id,
                    hostname=host.hostname,
                    trigger="manual",
                    status="running",
                )
            )
        with pytest.raises(IntegrityError):
            async with db.begin_nested():
                await db.flush()

    async def test_not_while_the_session_runs(self, db, ai_provider, ssh, proxmox) -> None:
        host = await _mapped_host(db)
        _, session, _ = await _fix(db, ai_provider, host, status="running")
        with pytest.raises(remediation.RollbackRefused, match="still running"):
            await remediation.prepare_rollback(
                db, session=session, host_id=host.id, trigger="manual"
            )


class TestTheHostQueue:
    """A rollback stops and restores the machine: nothing else may run on
    it meanwhile, and what was queued behind it runs after."""

    async def test_not_while_labdog_is_working_on_the_host(
        self, db, ai_provider, ssh, proxmox
    ) -> None:
        host = await _mapped_host(db)
        job_id = await _running_sync(db, host.id)
        _, session, _ = await _fix(db, ai_provider, host)
        with pytest.raises(remediation.HostBusy, match=f"sync {job_id}"):
            await remediation.prepare_rollback(
                db, session=session, host_id=host.id, trigger="manual"
            )

    async def test_a_claimed_rollback_holds_the_host(self, db, ai_provider, ssh, proxmox) -> None:
        from app.tasks.host_lock import check_host_busy, check_hosts_busy

        host = await _mapped_host(db)
        _, session, _ = await _fix(db, ai_provider, host)
        rollback, _ = await remediation.prepare_rollback(
            db, session=session, host_id=host.id, trigger="manual"
        )
        blocker = await check_host_busy(db, host.id)
        assert (blocker.kind, blocker.id) == ("ai_rollback", rollback.id)
        assert (await check_hosts_busy(db, [host.id])).kind == "ai_rollback"
        assert await remediation.host_busy(db, host.id) == (
            f"LabDog's rollback {rollback.id} is working on this host"
        )

    async def test_one_rollback_per_host_across_sessions(
        self, db, ai_provider, ssh, proxmox
    ) -> None:
        """The second goes back further than the first, so no rule but the
        queue stops it."""
        proxmox.storage_type = "lvmthin"
        host = await _mapped_host(db)
        _, second, _ = await _fix(
            db, ai_provider, host, snapshots=("s1",), finished_ago=timedelta(minutes=30)
        )
        _, first, _ = await _fix(db, ai_provider, host, snapshots=("s2",))
        proxmox.on_vm = ["s1", "s2"]
        await remediation.prepare_rollback(db, session=first, host_id=host.id, trigger="manual")
        with pytest.raises(remediation.HostBusy, match="rollback"):
            await remediation.prepare_rollback(
                db, session=second, host_id=host.id, trigger="manual"
            )

    @pytest.mark.parametrize("outcome", ["succeeded", "refused"])
    async def test_the_worker_hands_the_host_on(
        self, db, ai_provider, ssh, proxmox, tasks_use_test_db, outcome
    ) -> None:
        from app.tasks.ai_remediation import _run_rollback

        host = await _mapped_host(db)
        _, session, calls = await _fix(db, ai_provider, host)
        rollback, _ = await remediation.prepare_rollback(
            db, session=session, host_id=host.id, trigger="manual"
        )
        if outcome == "refused":
            calls[0].snapshot_pruned_at = datetime.now(UTC)
            await db.flush()
        with patch("app.tasks.host_lock.release_host_queue") as release:
            result = await _run_rollback(rollback.id)
        assert result["status"] == outcome
        release.assert_awaited_once()
        assert release.await_args.args == (host.id,)


async def _running_sync(db, host_id: int) -> int:
    from app.models.sync_job import SyncJob

    job = SyncJob(
        host_id=host_id, status="running", module_type="firewall", started_at=datetime.now(UTC)
    )
    db.add(job)
    await db.flush()
    return job.id


class TestRollingBackAutomatically:
    async def test_it_can_be_switched_off(self, db, ai_provider, ssh, proxmox) -> None:
        await _set(db, "ai.alert_auto_rollback", "0")
        host = await _mapped_host(db)
        event, session, _ = await _fix(db, ai_provider, host)
        rollback = await remediation.roll_back_automatically(db, event, session)
        assert rollback.status == "refused"
        assert "ai.alert_auto_rollback" in rollback.detail
        assert proxmox.restored == []

    async def test_it_waits_for_labdogs_own_work_then_gives_up(
        self, db, ai_provider, ssh, proxmox, monkeypatch
    ) -> None:
        monkeypatch.setattr(remediation, "BUSY_WAIT_SECONDS", 0)
        host = await _mapped_host(db)
        job_id = await _running_sync(db, host.id)
        event, session, _ = await _fix(db, ai_provider, host)
        ssh.reachable = False

        verdict, busy = await remediation.judge(db, event, session)
        assert verdict.outcome == "made_worse"
        assert f"sync {job_id}" in busy

        rollback = await remediation.roll_back_automatically(db, event, session, busy=busy)
        assert rollback.status == "refused"
        assert f"sync {job_id}" in rollback.detail
        assert proxmox.restored == []

    async def test_after_waiting_it_looks_again(
        self, db, ai_provider, ssh, proxmox, monkeypatch
    ) -> None:
        """The probe failed while a sync restarted networking. By the time
        the sync is done the host answers, and is not rolled back."""
        host = await _mapped_host(db)
        event, session, _ = await _fix(db, ai_provider, host, alert_status="resolved")
        ssh.reachable = False
        looks = []

        async def busy_once(db_, host_id):  # noqa: ARG001
            looks.append(host_id)
            if len(looks) == 1:
                return "LabDog's sync 7 is working on this host"
            ssh.reachable = True
            return None

        monkeypatch.setattr(remediation, "host_busy", busy_once)
        verdict, busy = await remediation.judge(db, event, session)
        assert verdict.outcome == "fixed"
        assert busy is None
        assert len(looks) == 2

    async def test_no_wait_when_the_host_is_free(self, db, ai_provider, ssh, proxmox) -> None:
        host = await _mapped_host(db)
        event, session, _ = await _fix(db, ai_provider, host)
        ssh.reachable = False
        verdict, busy = await remediation.judge(db, event, session)
        assert verdict.outcome == "made_worse"
        assert busy is None
        assert ssh.connects == remediation.PROBE_ATTEMPTS

    async def test_a_refusal_is_kept_with_its_reason(self, db, ai_provider, ssh, proxmox) -> None:
        host = await _mapped_host(db)
        event, session, calls = await _fix(db, ai_provider, host)
        calls[0].snapshot_pruned_at = datetime.now(UTC)
        rollback = await remediation.roll_back_automatically(db, event, session)
        assert rollback.status == "refused"
        assert rollback.alert_event_id == event.id
        assert "removed by retention" in rollback.detail


# ---------------------------------------------------------------------------
# The tasks
# ---------------------------------------------------------------------------


@pytest.fixture
def sent():
    with patch.object(celery_app, "send_task") as send:
        yield send


class TestTheTasks:
    async def test_the_sweep_hands_on_what_is_due_and_reports_what_is_late(
        self, db, ai_provider, tasks_use_test_db, sent
    ) -> None:
        from app.tasks.ai_remediation import _sweep

        host = await create_host(db)
        due, _, _ = await _fix(db, ai_provider, host)
        late, _, _ = await _fix(db, ai_provider, host, finished_ago=timedelta(minutes=50))

        result = await _sweep()

        assert result["dispatched"] == 1
        sent.assert_called_once_with(
            "app.tasks.ai_remediation.check_remediation", kwargs={"alert_event_id": due.id}
        )
        await db.refresh(due)
        await db.refresh(late)
        assert due.remediation_outcome == "checking"
        assert late.remediation_outcome == "unchecked"
        assert "too late" in late.remediation_detail

    async def test_a_fix_that_made_the_host_worse_is_rolled_back(
        self, db, ai_provider, ssh, proxmox, tasks_use_test_db, sent
    ) -> None:
        from app.tasks.ai_remediation import _check

        host = await _mapped_host(db)
        event, session, _ = await _fix(db, ai_provider, host, snapshots=("s1", "s2"))
        proxmox.on_vm = ["s1", "s2"]
        assert await remediation.claim(db, event.id, datetime.now(UTC))
        ssh.reachable = False

        with patch("app.tasks.host_lock.release_host_queue") as release:
            result = await _check(event.id)
        release.assert_awaited_once()

        assert result == {
            "alert_event_id": event.id,
            "outcome": "made_worse",
            "rollback": "succeeded",
        }
        assert proxmox.restored == ["s1"]
        rollback = (
            await db.execute(select(AIRollback).where(AIRollback.alert_event_id == event.id))
        ).scalar_one()
        assert rollback.trigger == "automatic"
        assert rollback.session_id == session.id

    @pytest.mark.parametrize(
        ("alert_status", "outcome"),
        [("resolved", "fixed"), ("firing", "not_effective")],
    )
    async def test_a_fix_that_did_not_make_it_worse_is_left_alone(
        self, db, ai_provider, ssh, proxmox, tasks_use_test_db, sent, alert_status, outcome
    ) -> None:
        from app.tasks.ai_remediation import _check

        host = await _mapped_host(db)
        event, _, _ = await _fix(db, ai_provider, host, alert_status=alert_status)
        assert await remediation.claim(db, event.id, datetime.now(UTC))

        result = await _check(event.id)

        assert result["outcome"] == outcome
        assert result["rollback"] is None
        assert proxmox.restored == []

    async def test_a_check_already_made_is_not_made_twice(
        self, db, ai_provider, tasks_use_test_db
    ) -> None:
        from app.tasks.ai_remediation import _check

        host = await create_host(db)
        event, _, _ = await _fix(db, ai_provider, host)
        event.remediation_outcome = "fixed"
        await db.flush()
        assert (await _check(event.id))["outcome"] == "skipped"

    async def test_a_check_the_sweep_closed_meanwhile_does_nothing_more(
        self, db, ai_provider, ssh, proxmox, tasks_use_test_db, sent, monkeypatch
    ) -> None:
        """Claimed, then slow enough that the sweep gave up on it and said
        so: no second, contradicting verdict, and no rollback."""
        from app.tasks.ai_remediation import _check

        host = await _mapped_host(db)
        event, _, _ = await _fix(db, ai_provider, host)
        assert await remediation.claim(db, event.id, datetime.now(UTC))
        ssh.reachable = False
        real_assess = remediation.assess

        async def slow(db_, event_, session_):
            verdict = await real_assess(db_, event_, session_)
            await remediation.mark_unchecked(
                db_, event_.id, datetime.now(UTC), "abandoned", expect="checking"
            )
            return verdict

        monkeypatch.setattr(remediation, "assess", slow)
        assert (await _check(event.id))["outcome"] == "skipped"
        await db.refresh(event)
        assert event.remediation_outcome == "unchecked"
        assert proxmox.restored == []

    async def test_a_requested_rollback_is_carried_out(
        self, db, ai_provider, ssh, proxmox, tasks_use_test_db
    ) -> None:
        from app.tasks.ai_remediation import _run_rollback

        host = await _mapped_host(db)
        _, session, _ = await _fix(db, ai_provider, host)
        rollback, _ = await remediation.prepare_rollback(
            db, session=session, host_id=host.id, trigger="manual"
        )
        result = await _run_rollback(rollback.id)
        assert result["status"] == "succeeded"
        assert proxmox.restored == ["labdog-ai-snap-1"]

    def test_the_sweep_is_scheduled_and_the_slow_tasks_are_routed(self) -> None:
        from tests.test_task_routing import _queue_for

        celery_app.loader.import_default_modules()
        assert "app.tasks.ai_remediation.check_due_remediations" in celery_app.tasks
        assert _queue_for("app.tasks.ai_remediation.check_remediation") == "long_running"
        assert _queue_for("app.tasks.ai_remediation.run_rollback") == "long_running"

        from app.tasks import ai_remediation

        with patch("app.tasks.beat_registry.ensure_entry") as ensure:
            ai_remediation._register_beat_schedules()
        assert ensure.call_args.kwargs["task"] == "app.tasks.ai_remediation.check_due_remediations"


# ---------------------------------------------------------------------------
# Full auto's new safeguards
# ---------------------------------------------------------------------------


@pytest.fixture
async def full_auto_ready(db, monkeypatch):
    monkeypatch.setattr(settings.alerts, "webhook_token", TOKEN)
    await _set(db, "ai.alert_full_auto_alertnames", ALERT)
    await _set(db, "ai.alert_full_auto_requires_snapshot", "0")


class TestNewSafeguards:
    async def test_not_on_the_host_labdog_runs_on(self, db, full_auto_ready) -> None:
        here = await create_host(db, ip="10.10.101.5")
        neighbour = await create_host(db, ip="10.10.10.51")
        neighbour.labdog_source_ip = "10.10.101.5"
        await db.flush()
        decided = await resolve(db, await _alert(db, here.id))
        assert decided.level == "read_only"
        assert "LabDog runs on this host" in decided.note

    async def test_a_remote_labdog_on_the_same_lan_is_not_mistaken_for_local(
        self, db, full_auto_ready, ssh
    ) -> None:
        host = await _mapped_host(db)
        decided = await resolve(db, await _alert(db, host.id))
        assert decided.level == "full_auto"
        assert labdog_host.PROBE_COMMAND in ssh.commands

    async def test_a_fix_that_made_the_host_worse_keeps_full_auto_off_it(
        self, db, ai_provider, full_auto_ready
    ) -> None:
        host = await create_host(db)
        earlier, _, _ = await _fix(db, ai_provider, host, finished_ago=timedelta(hours=3))
        earlier.remediation_outcome = "made_worse"
        earlier.remediation_checked_at = datetime.now(UTC) - timedelta(hours=2)
        await db.flush()

        decided = await resolve(db, await _alert(db, host.id, alertname=ALERT))
        assert decided.level == "read_only"
        assert "made this host worse" in decided.note

    @pytest.mark.parametrize("pending", [None, "checking"], ids=["not-yet-due", "being-checked"])
    async def test_not_while_the_last_fix_awaits_its_check(
        self, db, ai_provider, full_auto_ready, pending
    ) -> None:
        """Two unattended fixes on one host before the first is judged, and
        the check could not tell which one the host's state is down to."""
        await _set(db, "ai.alert_full_auto_alertnames", f"{ALERT}\nOtherAlert")
        host = await create_host(db)
        earlier, _, _ = await _fix(db, ai_provider, host, finished_ago=timedelta(minutes=2))
        earlier.remediation_outcome = pending
        await db.flush()
        decided = await resolve(db, await _alert(db, host.id, alertname="OtherAlert"))
        assert decided.level == "read_only"
        assert "not been checked yet" in decided.note

    async def test_a_judged_fix_does_not_hold_the_next_one(
        self, db, ai_provider, full_auto_ready
    ) -> None:
        await _set(db, "ai.alert_full_auto_alertnames", f"{ALERT}\nOtherAlert")
        host = await create_host(db)
        earlier, _, _ = await _fix(db, ai_provider, host, finished_ago=timedelta(minutes=12))
        earlier.remediation_outcome = "fixed"
        earlier.remediation_checked_at = datetime.now(UTC)
        await db.flush()
        decided = await resolve(db, await _alert(db, host.id, alertname="OtherAlert"))
        assert decided.level == "full_auto"

    async def test_for_a_day(self, db, ai_provider, full_auto_ready) -> None:
        host = await create_host(db)
        earlier, _, _ = await _fix(db, ai_provider, host, finished_ago=timedelta(days=2))
        earlier.remediation_outcome = "made_worse"
        earlier.remediation_checked_at = datetime.now(UTC) - timedelta(hours=25)
        await db.flush()

        decided = await resolve(db, await _alert(db, host.id))
        assert decided.level == "full_auto"


# ---------------------------------------------------------------------------
# The API
# ---------------------------------------------------------------------------


class TestTheApi:
    async def test_a_rollback_is_accepted_and_handed_to_a_worker(
        self, db, ai_provider, ssh, proxmox, superuser_client, sent
    ) -> None:
        host = await _mapped_host(db)
        _, session, _ = await _fix(db, ai_provider, host)

        response = await superuser_client.post(f"/api/ai/sessions/{session.id}/rollback", json={})

        assert response.status_code == 202, response.text
        body = response.json()
        assert body["status"] == "running"
        assert body["trigger"] == "manual"
        assert body["snapshot_name"] == "labdog-ai-snap-1"
        assert body["requested_by_user_id"] is not None
        sent.assert_called_once_with(
            "app.tasks.ai_remediation.run_rollback", kwargs={"rollback_id": body["id"]}
        )

    async def test_a_refusal_comes_back_at_once(
        self, db, ai_provider, ssh, proxmox, superuser_client, sent
    ) -> None:
        host = await _mapped_host(db, ip="10.10.101.5")
        neighbour = await create_host(db, ip="10.10.10.51")
        neighbour.labdog_source_ip = "10.10.101.5"
        _, session, _ = await _fix(db, ai_provider, host)

        response = await superuser_client.post(f"/api/ai/sessions/{session.id}/rollback", json={})

        assert response.status_code == 409
        assert "LabDog runs on" in response.json()["detail"]
        sent.assert_not_called()

    async def test_not_while_labdog_is_working_on_the_host(
        self, db, ai_provider, ssh, proxmox, superuser_client, sent
    ) -> None:
        host = await _mapped_host(db)
        await _running_sync(db, host.id)
        _, session, _ = await _fix(db, ai_provider, host)
        response = await superuser_client.post(f"/api/ai/sessions/{session.id}/rollback", json={})
        assert response.status_code == 409
        assert "sync" in response.json()["detail"]
        assert "Try again" in response.json()["detail"]
        sent.assert_not_called()

    async def test_the_session_says_what_a_rollback_would_restore(
        self, db, ai_provider, ssh, proxmox, superuser_client
    ) -> None:
        host = await _mapped_host(db)
        _, session, _ = await _fix(db, ai_provider, host, snapshots=("s1", "s2"))
        proxmox.on_vm = ["s1", "s2"]

        before = (await superuser_client.get(f"/api/ai/sessions/{session.id}")).json()
        [target] = before["rollback_targets"]
        assert target["host_id"] == host.id
        assert target["snapshot_name"] == "s1"
        assert target["unavailable_reason"] is None
        assert before["rollbacks"] == []

        rollback, plan = await remediation.prepare_rollback(
            db, session=session, host_id=host.id, trigger="manual"
        )
        await remediation.perform_rollback(db, rollback, plan)

        after = (await superuser_client.get(f"/api/ai/sessions/{session.id}")).json()
        assert "already rolled back" in after["rollback_targets"][0]["unavailable_reason"]
        assert [r["status"] for r in after["rollbacks"]] == ["succeeded"]

    async def test_the_button_follows_the_endpoints_rules(
        self, db, ai_provider, ssh, proxmox, superuser_client
    ) -> None:
        """A session whose snapshot another rollback went back past: the
        endpoint refuses it, so the button is off, with the same reason."""
        proxmox.storage_type = "lvmthin"
        host = await _mapped_host(db)
        _, earlier, _ = await _fix(
            db, ai_provider, host, snapshots=("s1",), finished_ago=timedelta(minutes=30)
        )
        _, later, _ = await _fix(db, ai_provider, host, snapshots=("s2",))
        proxmox.on_vm = ["s1", "s2"]
        rollback, plan = await remediation.prepare_rollback(
            db, session=earlier, host_id=host.id, trigger="manual"
        )
        await remediation.perform_rollback(db, rollback, plan)

        detail = (await superuser_client.get(f"/api/ai/sessions/{later.id}")).json()
        reason = detail["rollback_targets"][0]["unavailable_reason"]
        assert "bring back what that rollback undid" in reason
        response = await superuser_client.post(f"/api/ai/sessions/{later.id}/rollback", json={})
        assert response.status_code == 409
        assert response.json()["detail"] == reason

    async def test_the_alert_list_shows_the_outcome_and_the_rollback(
        self, db, ai_provider, superuser_client
    ) -> None:
        host = await create_host(db)
        event, session, _ = await _fix(db, ai_provider, host)
        event.remediation_outcome = "made_worse"
        event.remediation_detail = "LabDog can no longer reach it."
        await remediation.record_refused(
            db,
            session=session,
            host_id=host.id,
            trigger="automatic",
            reason="Automatic rollback is off (ai.alert_auto_rollback).",
            alert_event_id=event.id,
        )

        rows = (await superuser_client.get("/api/ai/alerts")).json()
        [row] = [r for r in rows if r["id"] == event.id]
        assert row["remediation_outcome"] == "made_worse"
        assert row["remediation_detail"] == "LabDog can no longer reach it."
        assert row["rollback_status"] == "refused"
        assert "ai.alert_auto_rollback" in row["rollback_detail"]


# ---------------------------------------------------------------------------
# The email
# ---------------------------------------------------------------------------


@pytest.fixture
async def subscribed(db):
    from app.models.user import User

    db.add(
        SMTPSettings(
            id=1,
            enabled=True,
            host="mail.home.arpa",
            port=587,
            tls_mode="starttls",
            from_address="labdog@home.arpa",
        )
    )
    await _set(db, "notifications.public_url", "https://labdog.example.com")
    user = User(
        email=f"u{uuid.uuid4().hex[:8]}@home.arpa",
        hashed_password="x",
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    db.add(user)
    await db.flush()
    db.add(NotificationSubscription(user_id=user.id, event_type="alert_remediation"))
    await db.flush()


async def _subjects(db) -> list[str]:
    rows = (
        await db.execute(
            select(Notification.subject)
            .where(Notification.event_type == "alert_remediation")
            .order_by(Notification.id)
        )
    ).scalars()
    return list(rows)


class TestTheEmail:
    @pytest.mark.parametrize(
        ("outcome", "rollback_status", "words"),
        [
            ("fixed", None, "worked"),
            ("not_effective", None, "did not clear"),
            ("made_worse", "succeeded", "Rolled back"),
            ("made_worse", "refused", "not rolled back"),
            ("unchecked", None, "not checked"),
        ],
    )
    async def test_each_outcome_reads_as_what_happened(
        self, db, ai_provider, subscribed, outcome, rollback_status, words
    ) -> None:
        from app.notifications.service import notify_remediation_checked

        host = await create_host(db)
        event, session, _ = await _fix(db, ai_provider, host)
        event.remediation_outcome = outcome
        event.remediation_detail = "what the check found"
        rollback = (
            SimpleNamespace(status=rollback_status, snapshot_name="s1", detail="restored")
            if rollback_status
            else None
        )
        assert await notify_remediation_checked(db, event, session, rollback) == 1
        [subject] = await _subjects(db)
        assert words in subject
        assert host.hostname in subject


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


class TestFromAlertToRollback:
    """The whole path, with only the network replaced.

    A webhook delivers a firing alert for a listed alert name on a mapped
    host. The investigation is decided at full auto; the model restarts a
    service, behind a snapshot; the session ends; the check falls due and
    finds the host unreachable; LabDog restores the snapshot and says so;
    and the next alert for that host is held back to the base level.
    """

    async def _alert_arrives(self, db, external_client, host, *, status: str) -> AlertEvent:
        payload = {
            "status": status,
            "alerts": [
                {
                    "status": status,
                    "labels": {
                        "alertname": ALERT,
                        "severity": "critical",
                        "instance": f"{host.ip_address}:9100",
                    },
                    "annotations": {"summary": "nginx is not answering"},
                    "startsAt": "2026-10-04T09:00:00Z",
                    "endsAt": "2026-10-04T09:20:00Z"
                    if status == "resolved"
                    else "0001-01-01T00:00:00Z",
                    "fingerprint": "e2e-nginx",
                }
            ],
        }
        with patch("app.api.webhooks.celery_app.send_task"):
            response = await external_client.post(
                "/api/webhooks/grafana-alerts",
                json=payload,
                headers={"Authorization": f"Bearer {TOKEN}"},
            )
        assert response.status_code == 200, response.text
        return (
            await db.execute(select(AlertEvent).where(AlertEvent.fingerprint == "e2e-nginx"))
        ).scalar_one()

    @pytest.mark.parametrize("host_after_fix", ["unreachable", "fine"])
    async def test_end_to_end(
        self,
        db,
        ai_provider,
        ssh,
        proxmox,
        subscribed,
        external_client,
        tasks_use_test_db,
        sent,
        monkeypatch,
        host_after_fix,
    ) -> None:
        from app.tasks.ai_alerts import _decide_and_run
        from app.tasks.ai_remediation import _check, _sweep

        monkeypatch.setattr(settings.alerts, "webhook_token", TOKEN)
        for key, value in {
            "ai.enabled": "1",
            "ai.alert_intake_enabled": "1",
            "ai.auto_investigate_enabled": "1",
            "ai.alert_full_auto_alertnames": ALERT,
        }.items():
            await _set(db, key, value)
        host = await _mapped_host(db)

        # 1. The alert arrives and is investigated at full auto.
        event = await self._alert_arrives(db, external_client, host, status="firing")
        assert event.host_id == host.id
        decided = await _decide_and_run(event.id)
        assert decided["outcome"] == "started"
        await db.refresh(event)
        assert event.investigation_autonomy == "full_auto", event.investigation_autonomy_note
        session = await db.get(AISession, decided["session_id"])

        # 2. The model restarts nginx; LabDog snapshots first.
        fake = FakeProvider(
            [
                ScriptedTurn(
                    tool_calls=[
                        call(
                            "run_ssh_command",
                            host_id=host.id,
                            command="systemctl restart nginx",
                            purpose="nginx has stopped",
                        )
                    ]
                ),
                ScriptedTurn(text="Restarted nginx. Changed: systemctl restart nginx."),
            ]
        )
        outcome = await AgentLoop(db, session, ai_provider, LoopCaps(), provider=fake).run()
        assert outcome.status == "succeeded"
        [snapshot] = proxmox.created
        change = (
            await db.execute(select(AIToolCall).where(AIToolCall.session_id == session.id))
        ).scalar_one()
        assert change.status == "executed"
        assert change.snapshot_name == snapshot
        assert "systemctl restart nginx" in ssh.commands

        # 3. Time passes. Either the host has gone, or the alert resolves.
        session.finished_at = datetime.now(UTC) - timedelta(minutes=11)
        await db.flush()
        if host_after_fix == "unreachable":
            ssh.reachable = False
        else:
            await self._alert_arrives(db, external_client, host, status="resolved")

        # 4. The sweep finds the check due, and the check runs.
        assert (await _sweep())["dispatched"] == 1
        result = await _check(event.id)
        await db.refresh(event)

        if host_after_fix == "fine":
            assert result == {"alert_event_id": event.id, "outcome": "fixed", "rollback": None}
            assert event.remediation_outcome == "fixed"
            assert proxmox.restored == []
            assert any("worked" in s for s in await _subjects(db))
            return

        assert result == {
            "alert_event_id": event.id,
            "outcome": "made_worse",
            "rollback": "succeeded",
        }
        assert event.remediation_outcome == "made_worse"
        assert proxmox.restored == [snapshot]
        assert any(s.startswith("[LabDog] Rolled back") for s in await _subjects(db))

        # 5. The next alert on that host is not trusted to full auto.
        ssh.reachable = True
        again = await _alert(db, host.id)
        decided = await resolve(db, again)
        assert decided.level == "read_only"
        assert "made this host worse" in decided.note
