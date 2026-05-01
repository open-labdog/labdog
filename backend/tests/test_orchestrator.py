"""Tests for the coalesced per-host sync orchestrator.

The orchestrator is dependency-injected (runner + decrypt_key_fn) so
these tests substitute synthetic stubs for ansible-runner and the
crypto layer. The DB is real (testcontainers) — host + ssh-key rows
come from the standard ``tests.conftest`` factories.
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest
import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from app.ansible_runtime.composer import CANONICAL_ORDER
from app.sync.orchestrator import orchestrate_host_sync
from tests.conftest import create_host, create_ssh_key

# These tests touch the real DB via testcontainers (factories require
# it) — mark as integration so they share the same session/engine
# bootstrap as the other DB-using tests.
pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeRunner:
    """Minimal stand-in for the ``ansible_runner.Runner`` object.

    ``orchestrate_host_sync`` only reads ``.events`` from the runner;
    everything else (status, stdout, rc) is the Celery wrapper's
    concern in the next commit.
    """

    def __init__(self, events: list[dict[str, Any]]):
        self.events = events


def _make_event(
    event_type: str,
    tags: list[str],
) -> dict[str, Any]:
    """Build a synthetic ansible-runner event dict.

    Matches the shape ``orchestrate_host_sync`` consumes:
    ``{"event": <type>, "event_data": {"task_tags": [...]}}``.
    """
    return {
        "event": event_type,
        "event_data": {"task_tags": list(tags)},
    }


def _make_decrypt_fn(plaintext: bytes):
    """Build a decrypt stub that returns a fixed plaintext value."""

    def decrypt(_encrypted: bytes, _master: bytes) -> bytes:
        return plaintext

    return decrypt


def _make_run_ansible_fn(runner: _FakeRunner, calls: list[dict[str, Any]] | None = None):
    """Build a runner stub that records its kwargs and returns ``runner``."""

    def run(**kwargs: Any) -> _FakeRunner:
        if calls is not None:
            calls.append(kwargs)
        return runner

    return run


def _all_modules_ok_events() -> list[dict[str, Any]]:
    """One ``runner_on_ok`` event per canonical module."""
    return [_make_event("runner_on_ok", [m]) for m in CANONICAL_ORDER]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_orchestrate_happy_path_all_modules(db: AsyncSession, tmp_path):
    """``module_filter=None`` runs all 7 canonical modules; all marked in_sync."""
    ssh_key = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh_key.id)
    # Default backend is "unknown" which the firewall generator rejects.
    # The orchestrator's contract is to pass the host's stored backend
    # through; tests pin it to a valid value rather than relying on a
    # default.
    from app.models.host import FirewallBackend

    host.firewall_backend = FirewallBackend.nftables
    await db.flush()
    host_id = host.id

    ssh_key_path = str(tmp_path / "id_ed25519")
    private_data_dir = str(tmp_path / "runner")
    os.makedirs(private_data_dir, exist_ok=True)

    fake_runner = _FakeRunner(events=_all_modules_ok_events())
    runner_calls: list[dict[str, Any]] = []

    outcomes, playbook_yaml, inventory_json = await orchestrate_host_sync(
        host_id,
        None,
        db,
        decrypt_key_fn=_make_decrypt_fn(b"FAKE PRIVATE KEY"),
        run_ansible_fn=_make_run_ansible_fn(fake_runner, runner_calls),
        ssh_key_path=ssh_key_path,
        private_data_dir=private_data_dir,
    )

    assert set(outcomes.keys()) == set(CANONICAL_ORDER), (
        f"Expected outcomes for all canonical modules, got {sorted(outcomes.keys())}"
    )
    assert all(v == "in_sync" for v in outcomes.values()), outcomes
    # Runner invoked exactly once.
    assert len(runner_calls) == 1
    # Playbook is non-empty YAML.
    parsed = yaml.safe_load(playbook_yaml)
    assert isinstance(parsed, list) and len(parsed) >= 1
    # Inventory parses as JSON.
    json.loads(inventory_json)


async def test_orchestrate_filtered_to_firewall_only(db: AsyncSession, tmp_path):
    """``module_filter=['firewall']`` produces a playbook with only firewall plays."""
    ssh_key = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh_key.id)
    # Default backend is "unknown" which the firewall generator rejects.
    # The orchestrator's contract is to pass the host's stored backend
    # through; tests pin it to a valid value rather than relying on a
    # default.
    from app.models.host import FirewallBackend

    host.firewall_backend = FirewallBackend.nftables
    await db.flush()
    host_id = host.id

    ssh_key_path = str(tmp_path / "id_ed25519")
    private_data_dir = str(tmp_path / "runner")
    os.makedirs(private_data_dir, exist_ok=True)

    fake_runner = _FakeRunner(events=[_make_event("runner_on_ok", ["firewall"])])
    runner_calls: list[dict[str, Any]] = []

    outcomes, playbook_yaml, _inventory = await orchestrate_host_sync(
        host_id,
        ["firewall"],
        db,
        decrypt_key_fn=_make_decrypt_fn(b"FAKE PRIVATE KEY"),
        run_ansible_fn=_make_run_ansible_fn(fake_runner, runner_calls),
        ssh_key_path=ssh_key_path,
        private_data_dir=private_data_dir,
    )

    assert outcomes == {"firewall": "in_sync"}
    assert len(runner_calls) == 1

    # Every task in the composed playbook must be tagged 'firewall' and only
    # 'firewall' — no service / package / cron tasks should leak in.
    plays = yaml.safe_load(playbook_yaml)
    assert isinstance(plays, list) and len(plays) >= 1
    seen_modules: set[str] = set()
    for play in plays:
        for section in ("pre_tasks", "tasks", "post_tasks"):
            for task in play.get(section) or []:
                tags = task.get("tags") or []
                if isinstance(tags, str):
                    tags = [tags]
                seen_modules.update(t for t in tags if t in CANONICAL_ORDER)
    assert seen_modules == {"firewall"}, (
        f"firewall-only filter leaked tasks tagged {seen_modules - {'firewall'}}"
    )


async def test_orchestrate_failed_module_marked_error(db: AsyncSession, tmp_path):
    """A ``runner_on_failed`` event for a module marks that module 'error'."""
    ssh_key = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh_key.id)
    # Default backend is "unknown" which the firewall generator rejects.
    # The orchestrator's contract is to pass the host's stored backend
    # through; tests pin it to a valid value rather than relying on a
    # default.
    from app.models.host import FirewallBackend

    host.firewall_backend = FirewallBackend.nftables
    await db.flush()
    host_id = host.id

    ssh_key_path = str(tmp_path / "id_ed25519")
    private_data_dir = str(tmp_path / "runner")
    os.makedirs(private_data_dir, exist_ok=True)

    # All modules report ok, except services which reports failed.
    events: list[dict[str, Any]] = []
    for module in CANONICAL_ORDER:
        if module == "services":
            events.append(_make_event("runner_on_failed", [module]))
        else:
            events.append(_make_event("runner_on_ok", [module]))

    fake_runner = _FakeRunner(events=events)
    outcomes, _playbook, _inventory = await orchestrate_host_sync(
        host_id,
        None,
        db,
        decrypt_key_fn=_make_decrypt_fn(b"FAKE PRIVATE KEY"),
        run_ansible_fn=_make_run_ansible_fn(fake_runner),
        ssh_key_path=ssh_key_path,
        private_data_dir=private_data_dir,
    )

    assert outcomes["services"] == "error"
    for module in CANONICAL_ORDER:
        if module == "services":
            continue
        assert outcomes[module] == "in_sync", f"{module}: {outcomes[module]}"


async def test_orchestrate_writes_ssh_key_with_correct_perms(db: AsyncSession, tmp_path):
    """SSH key is written at ``ssh_key_path`` with mode 0o600 and decrypted contents."""
    ssh_key = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh_key.id)
    # Default backend is "unknown" which the firewall generator rejects.
    # The orchestrator's contract is to pass the host's stored backend
    # through; tests pin it to a valid value rather than relying on a
    # default.
    from app.models.host import FirewallBackend

    host.firewall_backend = FirewallBackend.nftables
    await db.flush()
    host_id = host.id

    ssh_key_path = str(tmp_path / "id_ed25519")
    private_data_dir = str(tmp_path / "runner")
    os.makedirs(private_data_dir, exist_ok=True)

    fake_runner = _FakeRunner(events=[])
    fixed_plaintext = b"FAKE PRIVATE KEY"

    await orchestrate_host_sync(
        host_id,
        ["firewall"],
        db,
        decrypt_key_fn=_make_decrypt_fn(fixed_plaintext),
        run_ansible_fn=_make_run_ansible_fn(fake_runner),
        ssh_key_path=ssh_key_path,
        private_data_dir=private_data_dir,
    )

    assert os.path.exists(ssh_key_path), "SSH key file must exist after orchestrate"

    # Mode bits — orchestrator forces 0o600.
    mode = os.stat(ssh_key_path).st_mode & 0o777
    assert mode == 0o600, f"Expected mode 0o600, got {oct(mode)}"

    # Contents should start with the injected plaintext (orchestrator
    # may append a trailing newline if not already present).
    with open(ssh_key_path, "rb") as f:
        content = f.read()
    assert content.startswith(fixed_plaintext)
    assert content.endswith(b"\n")


async def test_orchestrate_host_not_found_raises(db: AsyncSession, tmp_path):
    """Calling with a non-existent host_id raises LookupError."""
    ssh_key_path = str(tmp_path / "id_ed25519")
    private_data_dir = str(tmp_path / "runner")
    os.makedirs(private_data_dir, exist_ok=True)

    with pytest.raises(LookupError, match="Host 99999 not found"):
        await orchestrate_host_sync(
            99999,
            None,
            db,
            decrypt_key_fn=_make_decrypt_fn(b"FAKE PRIVATE KEY"),
            run_ansible_fn=_make_run_ansible_fn(_FakeRunner(events=[])),
            ssh_key_path=ssh_key_path,
            private_data_dir=private_data_dir,
        )


async def test_orchestrate_passes_correct_inventory_fields(db: AsyncSession, tmp_path):
    """Returned inventory_json contains host IP, port, ssh_user, key path, hostname."""
    ssh_key = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh_key.id, ip="10.42.0.99")
    from app.models.host import FirewallBackend

    host.firewall_backend = FirewallBackend.nftables
    await db.flush()
    host_id = host.id
    hostname = host.hostname
    ssh_user = ssh_key.ssh_user

    ssh_key_path = str(tmp_path / "id_ed25519")
    private_data_dir = str(tmp_path / "runner")
    os.makedirs(private_data_dir, exist_ok=True)

    fake_runner = _FakeRunner(events=[])
    _outcomes, _playbook, inventory_json = await orchestrate_host_sync(
        host_id,
        ["firewall"],
        db,
        decrypt_key_fn=_make_decrypt_fn(b"FAKE PRIVATE KEY"),
        run_ansible_fn=_make_run_ansible_fn(fake_runner),
        ssh_key_path=ssh_key_path,
        private_data_dir=private_data_dir,
    )

    inv = json.loads(inventory_json)
    hosts = inv["all"]["hosts"]
    # The hostname (sanitised) is the inventory key; with a normal DNS
    # name from create_host it should round-trip unchanged or close to.
    assert len(hosts) == 1
    inv_key = next(iter(hosts.keys()))
    # generate_inventory replaces unsafe chars with underscore but our
    # factory hostnames use only safe chars [A-Za-z0-9._-].
    assert inv_key in hostname or hostname.replace(".", "_").startswith(inv_key[:5])
    entry = hosts[inv_key]
    assert entry["ansible_host"] == "10.42.0.99"
    assert entry["ansible_port"] == host.ssh_port
    assert entry["ansible_user"] == ssh_user
    assert entry["ansible_ssh_private_key_file"] == ssh_key_path


async def test_orchestrate_unreachable_event_marks_module_error(db: AsyncSession, tmp_path):
    """A ``runner_on_unreachable`` event marks the tagged module 'error'."""
    ssh_key = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh_key.id)
    # Default backend is "unknown" which the firewall generator rejects.
    # The orchestrator's contract is to pass the host's stored backend
    # through; tests pin it to a valid value rather than relying on a
    # default.
    from app.models.host import FirewallBackend

    host.firewall_backend = FirewallBackend.nftables
    await db.flush()
    host_id = host.id

    ssh_key_path = str(tmp_path / "id_ed25519")
    private_data_dir = str(tmp_path / "runner")
    os.makedirs(private_data_dir, exist_ok=True)

    fake_runner = _FakeRunner(
        events=[_make_event("runner_on_unreachable", ["firewall"])],
    )

    outcomes, _playbook, _inventory = await orchestrate_host_sync(
        host_id,
        ["firewall"],
        db,
        decrypt_key_fn=_make_decrypt_fn(b"FAKE PRIVATE KEY"),
        run_ansible_fn=_make_run_ansible_fn(fake_runner),
        ssh_key_path=ssh_key_path,
        private_data_dir=private_data_dir,
    )

    assert outcomes == {"firewall": "error"}


async def test_orchestrate_resolver_only_no_config_short_circuits(db: AsyncSession, tmp_path):
    """BUG-40: resolver-only filter + no resolver config → no runner call.

    The resolver block is the only one with a "skip if no config" path.
    When it skips and is the sole requested module, ``fragments`` is
    empty. Calling ``compose_playbook([])`` would yield an empty play
    list that ansible-runner rejects with a runtime error. The fix
    short-circuits: outcomes report ``no_tasks`` for every requested
    module, the runner is never invoked, and the playbook/inventory
    strings come back empty.
    """
    ssh_key = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=ssh_key.id)
    from app.models.host import FirewallBackend

    host.firewall_backend = FirewallBackend.nftables
    await db.flush()
    host_id = host.id

    ssh_key_path = str(tmp_path / "id_ed25519")
    private_data_dir = str(tmp_path / "runner")
    os.makedirs(private_data_dir, exist_ok=True)

    runner_calls: list[dict[str, Any]] = []

    def _runner_must_not_be_called(**kwargs: Any) -> _FakeRunner:
        runner_calls.append(kwargs)
        raise AssertionError("BUG-40: runner invoked despite empty fragment list")

    outcomes, playbook_yaml, inventory_json = await orchestrate_host_sync(
        host_id,
        ["resolver"],
        db,
        decrypt_key_fn=_make_decrypt_fn(b"FAKE PRIVATE KEY"),
        run_ansible_fn=_runner_must_not_be_called,
        ssh_key_path=ssh_key_path,
        private_data_dir=private_data_dir,
    )

    assert outcomes == {"resolver": "no_tasks"}, outcomes
    assert playbook_yaml == ""
    assert inventory_json == ""
    assert runner_calls == []
