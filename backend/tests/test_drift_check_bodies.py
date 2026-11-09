"""Characterisation tests for the five per-host drift check bodies.

``check_<module>_drift_for_one_host`` for cron, packages, services, Linux
users and /etc/hosts each load the host's SSH key, collect and diff, write
the verdict to the ``HostModuleStatus`` row, record a drift sample, probe
LabDog's source IP, and record an unreachable host or an unexpected error.
The sweep tests stub them out, so they were 14 to 19% covered. These drive
each one with the collector, diff and merge replaced (the bodies import
them at call time, so patching the source module reaches them) and pin
what lands on the row, the sample, the host, and the log, so moving their
shared steps into helpers has something to answer to. The resolver body's
adapter records failures the same way and is pinned at the end.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import asyncssh
import pytest
from sqlalchemy import select

from app.models.drift_sample import DriftSample
from app.models.host_module_status import HostModuleStatus
from tests.conftest import create_host, create_ssh_key

pytestmark = pytest.mark.integration


class _Dumpable(SimpleNamespace):
    def model_dump(self):
        return dict(vars(self))


@dataclass
class Spec:
    """One module: what to patch, what its diff looks like, what it writes."""

    module: str
    check: str
    module_type: str
    #: dotted path -> return value; the collect/merge/diff functions.
    returns: dict
    #: The diff object for (drifted, in sync).
    diff: tuple
    #: The collector whose failure exercises the error paths.
    collector: str
    collected_state: object
    sample_counts: dict
    refreshes: bool
    #: The /etc/hosts body connects on every run, not only to learn the IP.
    always_connects: bool = False
    #: A second diff (Linux users diff groups too), shaped like ``diff``.
    extra_diff: tuple | None = None
    calls: list = field(default_factory=list)


def _cron():
    desired = [
        SimpleNamespace(name="backup", user="root", schedule="0 1 * * *", command="/bin/a"),
        SimpleNamespace(name="report", user="ops", schedule="0 2 * * *", command="/bin/b"),
    ]
    actual = [{"name": "backup", "user": "root"}]
    return Spec(
        module="app.tasks.cron_drift",
        check="check_cron_drift_for_one_host",
        module_type="cron",
        returns={
            "app.cron.merge.get_effective_cron_jobs": desired,
            "app.cron.collector.collect_cron_jobs": actual,
        },
        diff=(
            "app.cron.diff.diff_cron_jobs",
            SimpleNamespace(jobs_to_add=[1, 2], jobs_to_remove=[3], jobs_to_update=[4, 5, 6]),
            SimpleNamespace(jobs_to_add=[], jobs_to_remove=[], jobs_to_update=[]),
        ),
        collector="app.cron.collector.collect_cron_jobs",
        collected_state=actual,
        sample_counts={"add_count": 2, "remove_count": 1, "policy_change_count": 3},
        refreshes=True,
    )


def _packages():
    desired = [_Dumpable(package_name="curl", state="present")]
    actual = [{"package_name": "curl", "installed": True}]
    return Spec(
        module="app.tasks.package_drift",
        check="check_package_drift_for_one_host",
        module_type="package",
        returns={
            "app.packages.merge.get_effective_packages": desired,
            "app.packages.collector.collect_package_states": actual,
        },
        diff=(
            "app.packages.diff.compute_diff",
            SimpleNamespace(
                has_drift=True, to_install=[1], to_remove=[2, 3], to_upgrade=[4], to_hold_change=[5]
            ),
            SimpleNamespace(
                has_drift=False, to_install=[], to_remove=[], to_upgrade=[], to_hold_change=[]
            ),
        ),
        collector="app.packages.collector.collect_package_states",
        collected_state=actual,
        sample_counts={"add_count": 1, "remove_count": 2, "policy_change_count": 2},
        refreshes=True,
    )


def _services():
    desired = [SimpleNamespace(service_name="nginx")]
    current = [SimpleNamespace(service_name="nginx", active_state="active", enabled=True)]
    return Spec(
        module="app.tasks.service_drift",
        check="check_service_drift_for_one_host",
        module_type="service",
        returns={
            "app.services.merge.get_effective_services": desired,
            "app.services.collector.collect_service_states": current,
        },
        diff=(
            "app.services.diff.compute_service_diff",
            SimpleNamespace(has_changes=True, services_to_update=[1, 2]),
            SimpleNamespace(has_changes=False, services_to_update=[]),
        ),
        collector="app.services.collector.collect_service_states",
        collected_state=[{"service_name": "nginx", "active_state": "active", "enabled": True}],
        sample_counts={"add_count": 0, "remove_count": 0, "policy_change_count": 2},
        refreshes=False,
    )


def _users():
    users = [_Dumpable(username="alice", state="present")]
    groups = [_Dumpable(groupname="ops", state="present")]
    actual_users = [{"username": "alice"}]
    actual_groups = [{"groupname": "ops"}]
    return Spec(
        module="app.tasks.user_drift",
        check="check_user_drift_for_one_host",
        module_type="linux_user",
        returns={
            "app.user_mgmt.merge.get_effective_users": users,
            "app.user_mgmt.merge.get_effective_groups": groups,
            "app.user_mgmt.collector.collect_user_states": actual_users,
            "app.user_mgmt.collector.collect_group_states": actual_groups,
        },
        extra_diff=(
            "app.user_mgmt.diff.diff_groups",
            SimpleNamespace(groups_to_add=[1], groups_to_remove=[], groups_to_update=[2]),
            SimpleNamespace(groups_to_add=[], groups_to_remove=[], groups_to_update=[]),
        ),
        diff=(
            "app.user_mgmt.diff.diff_users",
            SimpleNamespace(users_to_add=[1, 2], users_to_remove=[3], users_to_update=[]),
            SimpleNamespace(users_to_add=[], users_to_remove=[], users_to_update=[]),
        ),
        collector="app.user_mgmt.collector.collect_group_states",
        collected_state={"users": actual_users, "groups": actual_groups},
        sample_counts={"add_count": 3, "remove_count": 1, "policy_change_count": 1},
        refreshes=False,
    )


def _hosts_file():
    desired = [SimpleNamespace(ip_address="10.0.0.9", hostname="db")]
    current = [SimpleNamespace(ip_address="10.0.0.9", hostname="db", aliases=["db.lan"])]
    return Spec(
        module="app.tasks.hosts_drift",
        check="check_hosts_drift_for_one_host",
        module_type="hosts_file",
        returns={
            "app.hosts_mgmt.merge.get_effective_hosts_entries": desired,
            "app.hosts_mgmt.collector.collect_hosts_file": current,
        },
        diff=(
            "app.hosts_mgmt.diff.compute_hosts_diff",
            SimpleNamespace(
                has_changes=True, entries_to_add=[1], entries_to_remove=[2], entries_to_update=[3]
            ),
            SimpleNamespace(
                has_changes=False, entries_to_add=[], entries_to_remove=[], entries_to_update=[]
            ),
        ),
        collector="app.hosts_mgmt.collector.collect_hosts_file",
        collected_state=[{"ip_address": "10.0.0.9", "hostname": "db", "aliases": ["db.lan"]}],
        sample_counts={"add_count": 1, "remove_count": 1, "policy_change_count": 1},
        refreshes=False,
        always_connects=True,
    )


SPECS = [_cron, _packages, _services, _users, _hosts_file]
IDS = ["cron", "package", "service", "linux_user", "hosts_file"]


class _Env:
    """Stubs for everything a body reaches outside the session."""

    def __init__(self, spec: Spec, *, drifted: bool):
        self.spec = spec
        self.drifted = drifted
        self.calls: dict[str, list] = {}
        self.connects = 0
        self.connect_error: BaseException | None = None
        self.collector_error: BaseException | None = None
        self.decrypt_error: BaseException | None = None

    def _recorder(self, path, value):
        async def _fn(*args, **kwargs):
            self.calls.setdefault(path, []).append(args)
            if path == self.spec.collector and self.collector_error is not None:
                raise self.collector_error
            return value

        return _fn

    def _sync_recorder(self, path, value):
        def _fn(*args, **kwargs):
            self.calls.setdefault(path, []).append(args)
            return value

        return _fn

    @asynccontextmanager
    async def _connect(self, host, db, client_keys):  # noqa: ARG002
        self.connects += 1
        if self.connect_error is not None:
            raise self.connect_error
        yield SimpleNamespace(name="probe-conn")

    def patches(self):
        ps = []
        for path, value in self.spec.returns.items():
            if ".diff." in path:
                ps.append(patch(path, side_effect=self._sync_recorder(path, value)))
            else:
                ps.append(patch(path, side_effect=self._recorder(path, value)))
        for diff_path, drifted, clean in filter(None, (self.spec.diff, self.spec.extra_diff)):
            outcome = drifted if self.drifted else clean
            ps.append(patch(diff_path, side_effect=self._sync_recorder(diff_path, outcome)))
        ps.append(patch("app.ssh_utils.ssh_connect_host", new=self._connect))
        ps.append(patch("app.ssh_utils.get_source_ip", new=AsyncMock(return_value="192.0.2.7")))
        ps.append(patch("app.api.host_state.refresh_host_sync_status", new=AsyncMock()))
        if self.spec.always_connects:
            ps.append(patch(f"{self.spec.module}._maybe_update_host_ip", new=AsyncMock()))
        if self.decrypt_error is not None:
            ps.append(
                patch("app.crypto.encryption.decrypt_ssh_key", side_effect=self.decrypt_error)
            )
        return ps


async def _run(db, spec: Spec, env: _Env, host, hms):
    import importlib
    from contextlib import ExitStack

    module = importlib.import_module(spec.module)
    with ExitStack() as stack:
        mocks = {}
        for p in env.patches():
            mocks[p.attribute] = stack.enter_context(p)
        ran = await getattr(module, spec.check)(host, hms, db)
    return ran, mocks


async def _setup(db, spec: Spec, *, keyed=True, source_ip=None):
    key = await create_ssh_key(db) if keyed else None
    host = await create_host(db, ssh_key_id=key.id if key else None)
    host.labdog_source_ip = source_ip
    hms = HostModuleStatus(host_id=host.id, module_type=spec.module_type, sync_status="unknown")
    db.add(hms)
    await db.flush()
    return host, hms


async def _samples(db, host_id):
    rows = (await db.execute(select(DriftSample).where(DriftSample.host_id == host_id))).scalars()
    return [
        (
            r.module_type,
            r.status,
            r.add_count,
            r.remove_count,
            r.policy_change_count,
            r.duration_ms is not None,
        )
        for r in rows.all()
    ]


@pytest.mark.parametrize("make", SPECS, ids=IDS)
async def test_a_host_without_a_key_is_skipped_untouched(db, make):
    spec = make()
    host, hms = await _setup(db, spec, keyed=False)
    env = _Env(spec, drifted=True)

    ran, _ = await _run(db, spec, env, host, hms)

    assert ran is False
    assert (hms.sync_status, hms.last_drift_check_at, hms.collected_state) == (
        "unknown",
        None,
        None,
    )
    assert env.calls == {}
    assert await _samples(db, host.id) == []


@pytest.mark.parametrize("make", SPECS, ids=IDS)
@pytest.mark.parametrize("drifted", [True, False], ids=["drifted", "in_sync"])
async def test_a_verdict_is_written_with_its_sample(db, make, drifted):
    spec = make()
    host, hms = await _setup(db, spec)
    hms.error_message = "stale error"
    env = _Env(spec, drifted=drifted)

    ran, mocks = await _run(db, spec, env, host, hms)

    assert ran is True
    status = "out_of_sync" if drifted else "in_sync"
    assert hms.sync_status == status
    assert hms.last_drift_check_at is not None
    assert hms.collected_at is not None
    assert hms.collected_state == spec.collected_state
    assert hms.error_message is None
    counts = spec.sample_counts if drifted else dict.fromkeys(spec.sample_counts, 0)
    assert await _samples(db, host.id) == [
        (
            spec.module_type,
            status,
            counts["add_count"],
            counts["remove_count"],
            counts["policy_change_count"],
            True,
        )
    ]
    refresh = mocks["refresh_host_sync_status"]
    assert refresh.await_args_list == ([((host, db),)] if spec.refreshes else [])
    # The source IP was unknown, so every body probes for it.
    assert host.labdog_source_ip == "192.0.2.7"
    assert env.connects == 1


@pytest.mark.parametrize("make", SPECS, ids=IDS)
async def test_the_collectors_and_diffs_get_the_desired_state(db, make):
    spec = make()
    host, hms = await _setup(db, spec)
    env = _Env(spec, drifted=True)

    await _run(db, spec, env, host, hms)

    pem_args = [
        args[2] for path, calls in env.calls.items() if ".collector." in path for args in calls
    ]
    assert pem_args and all(p.startswith("-----BEGIN OPENSSH PRIVATE KEY-----") for p in pem_args)
    c = env.calls
    if spec.module_type == "cron":
        users = c["app.cron.collector.collect_cron_jobs"][0][3]
        assert sorted(users) == ["ops", "root"]
        desired_dicts = c["app.cron.diff.diff_cron_jobs"][0][0]
        assert desired_dicts[0] == {
            "name": "backup",
            "user": "root",
            "schedule": "0 1 * * *",
            "command": "/bin/a",
        }
    elif spec.module_type == "package":
        assert c["app.packages.collector.collect_package_states"][0][3] == ["curl"]
        assert c["app.packages.diff.compute_diff"][0][0] == [
            {"package_name": "curl", "state": "present"}
        ]
    elif spec.module_type == "service":
        assert c["app.services.collector.collect_service_states"][0][3] == ["nginx"]
        current, desired = c["app.services.diff.compute_service_diff"][0]
        assert current[0].service_name == desired[0].service_name == "nginx"
    elif spec.module_type == "linux_user":
        assert c["app.user_mgmt.collector.collect_user_states"][0][3] == ["alice"]
        assert c["app.user_mgmt.collector.collect_group_states"][0][3] == ["ops"]
        assert c["app.user_mgmt.diff.diff_users"][0][0] == [
            {"username": "alice", "state": "present"}
        ]
        assert c["app.user_mgmt.diff.diff_groups"][0][0] == [
            {"groupname": "ops", "state": "present"}
        ]
    else:
        assert len(c["app.hosts_mgmt.collector.collect_hosts_file"][0]) == 3
        current, desired = c["app.hosts_mgmt.diff.compute_hosts_diff"][0]
        assert current[0].aliases == ["db.lan"] and desired[0].hostname == "db"


@pytest.mark.parametrize("make", SPECS, ids=IDS)
async def test_a_known_source_ip_is_not_probed_again(db, make):
    spec = make()
    host, hms = await _setup(db, spec, source_ip="198.51.100.1")
    env = _Env(spec, drifted=False)

    _, mocks = await _run(db, spec, env, host, hms)

    assert host.labdog_source_ip == "198.51.100.1"
    assert mocks["get_source_ip"].await_count == 0
    if spec.always_connects:
        # /etc/hosts still connects, to check the host's own primary IP.
        assert env.connects == 1
        mocks["_maybe_update_host_ip"].assert_awaited_once()
        assert mocks["_maybe_update_host_ip"].await_args.args[1:] == (host, db)
    else:
        assert env.connects == 0


@pytest.mark.parametrize("make", SPECS, ids=IDS)
async def test_a_failed_probe_does_not_change_the_verdict(db, make, caplog):
    spec = make()
    host, hms = await _setup(db, spec)
    env = _Env(spec, drifted=True)
    env.connect_error = OSError("probe refused")

    with caplog.at_level(logging.DEBUG, logger=spec.module):
        ran, _ = await _run(db, spec, env, host, hms)

    assert ran is True
    assert hms.sync_status == "out_of_sync"
    assert host.labdog_source_ip is None
    records = [r for r in caplog.records if r.name == spec.module]
    assert [(r.levelno, r.getMessage()) for r in records] == [
        (logging.DEBUG, f"source-IP probe failed for host {host.id}")
    ]
    assert records[0].exc_info is not None


@pytest.mark.parametrize("make", SPECS, ids=IDS)
@pytest.mark.parametrize(
    ("error", "message"),
    [
        (OSError("no route"), "Host unreachable: no route"),
        # The fallback is written `e or '...'`, and an exception is always
        # truthy, so a bare timeout leaves the message empty (BUG-144).
        (TimeoutError(), "Host unreachable: "),
        (asyncssh.DisconnectError(11, "bye"), "Host unreachable: bye"),
    ],
    ids=["oserror", "timeout", "asyncssh"],
)
async def test_an_unreachable_host_is_recorded(db, make, error, message):
    spec = make()
    host, hms = await _setup(db, spec)
    hms.collected_state = {"kept": True}
    env = _Env(spec, drifted=True)
    env.collector_error = error

    ran, _ = await _run(db, spec, env, host, hms)

    assert ran is True
    assert (hms.sync_status, hms.error_message) == ("unknown", message)
    assert hms.last_drift_check_at is not None
    assert (hms.collected_state, hms.collected_at) == ({"kept": True}, None)
    assert await _samples(db, host.id) == []
    assert env.connects == 0


@pytest.mark.parametrize("make", SPECS, ids=IDS)
@pytest.mark.parametrize("where", ["collector", "decrypt"])
async def test_an_unexpected_error_is_recorded_and_logged(db, make, where, caplog):
    spec = make()
    host, hms = await _setup(db, spec)
    env = _Env(spec, drifted=True)
    if where == "collector":
        env.collector_error = ValueError("unexpected shape")
    else:
        env.decrypt_error = ValueError("bad tag")

    with caplog.at_level(logging.ERROR, logger=spec.module):
        ran, _ = await _run(db, spec, env, host, hms)

    assert ran is True
    detail = "unexpected shape" if where == "collector" else "bad tag"
    assert (hms.sync_status, hms.error_message) == ("error", detail)
    assert hms.last_drift_check_at is not None
    assert hms.collected_at is None
    assert await _samples(db, host.id) == []
    records = [r for r in caplog.records if r.name == spec.module]
    assert [(r.levelno, r.getMessage()) for r in records] == [
        (logging.ERROR, f"{spec.module_type} drift check failed for host {host.id}")
    ]
    assert records[0].exc_info is not None


@pytest.mark.parametrize("make", SPECS, ids=IDS)
async def test_a_host_whose_key_row_is_gone_is_skipped_untouched(db, make):
    from sqlalchemy import delete

    from app.models.ssh_key import SSHKey

    spec = make()
    host, hms = await _setup(db, spec)
    # A core delete: the database nulls hosts.ssh_key_id, the loaded host
    # still carries the old id, which is the window this branch covers.
    await db.execute(delete(SSHKey).where(SSHKey.id == host.ssh_key_id))
    env = _Env(spec, drifted=True)

    ran, _ = await _run(db, spec, env, host, hms)

    assert ran is False
    assert hms.sync_status == "unknown"
    assert (hms.last_drift_check_at, hms.collected_state) == (None, None)
    assert env.calls == {}


@pytest.mark.parametrize(
    ("error", "status", "message"),
    [
        (OSError("no route"), "unknown", "Host unreachable: no route"),
        (TimeoutError(), "unknown", "Host unreachable: "),
        (ValueError("unexpected shape"), "error", "unexpected shape"),
    ],
    ids=["oserror", "timeout", "unexpected"],
)
async def test_the_resolver_adapter_records_failures_the_same_way(
    db, caplog, error, status, message
):
    """The resolver body keeps its collect-and-diff elsewhere, but its
    adapter records an unreachable host and an unexpected error as the
    five bodies above do."""
    from app.tasks import resolver_drift

    host = await create_host(db)
    hms = HostModuleStatus(host_id=host.id, module_type="resolver", sync_status="in_sync")
    db.add(hms)
    await db.flush()

    with (
        patch.object(resolver_drift, "_collect_and_diff", AsyncMock(side_effect=error)),
        caplog.at_level(logging.ERROR, logger="app.tasks.resolver_drift"),
    ):
        ran = await resolver_drift.check_resolver_drift_for_one_host(host, hms, db)

    assert ran is True
    assert (hms.sync_status, hms.error_message) == (status, message)
    assert hms.last_drift_check_at is not None
    logged = [r.getMessage() for r in caplog.records if r.name == "app.tasks.resolver_drift"]
    expected = [f"resolver drift check failed for host {host.id}"] if status == "error" else []
    assert logged == expected


@pytest.mark.parametrize("diff", [object(), None], ids=["checked", "declined"])
async def test_the_resolver_adapter_reports_whether_a_check_ran(db, diff):
    from app.tasks import resolver_drift

    host = await create_host(db)
    with patch.object(resolver_drift, "_collect_and_diff", AsyncMock(return_value=diff)):
        ran = await resolver_drift.check_resolver_drift_for_one_host(host, None, db)
    assert ran is (diff is not None)
