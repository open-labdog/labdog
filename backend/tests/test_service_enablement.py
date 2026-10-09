"""BUG-87: service enablement in collected state, and the drift it feeds.

``collect_state`` lists services with ``systemctl list-units``, which has
no enablement column, and the inline drift check used to read it from
``sub_state`` instead, a field that never says "enabled". Every service
rule with ``enabled: true`` was reported as drifted after each collection,
and no sync could clear it.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.services.collector import _parse_unit_file_states, _unit_enabled, list_all_services

# Trimmed from a Debian 12 host (systemd 252).
LIST_UNITS = """\
UNIT                                 LOAD      ACTIVE   SUB     DESCRIPTION
alloy.service                        loaded    active   running Grafana Alloy
container-getty@1.service            loaded    active   running Container Getty on /dev/tty1
dbus.service                         loaded    active   running D-Bus System Message Bus
getty@tty1.service                   loaded    active   running Getty on tty1

4 loaded units listed.
"""
LIST_UNIT_FILES = """\
UNIT FILE                              STATE           PRESET
alloy.service                          enabled         enabled
container-getty@.service               static          -
dbus.service                           static          -
getty@.service                         enabled         enabled
ssh.service                            disabled        enabled

5 unit files listed.
"""


def test_unit_file_states_are_parsed_without_header_or_summary():
    assert _parse_unit_file_states(LIST_UNIT_FILES) == {
        "alloy": "enabled",
        "container-getty@": "static",
        "dbus": "static",
        "getty@": "enabled",
        "ssh": "disabled",
    }


@pytest.mark.parametrize(
    ("unit", "expected"),
    [
        pytest.param("alloy", True, id="enabled"),
        pytest.param("ssh", False, id="disabled"),
        pytest.param("dbus", False, id="static"),
        pytest.param("getty@tty1", True, id="instance-of-an-enabled-template"),
        pytest.param("container-getty@1", False, id="instance-of-a-static-template"),
        pytest.param("run-u42", False, id="no-unit-file"),
    ],
)
def test_unit_enabled_matches_is_enabled(unit, expected):
    assert _unit_enabled(unit, _parse_unit_file_states(LIST_UNIT_FILES)) is expected


def _fake_host_connection(monkeypatch, outputs: dict[str, str]) -> None:
    """Answer each systemctl call from *outputs*, keyed by subcommand."""

    class Conn:
        async def run(self, command, check=False):
            key = "list-unit-files" if "list-unit-files" in command else "list-units"
            return SimpleNamespace(stdout=outputs[key], exit_status=0)

    @asynccontextmanager
    async def connect(host, db, client_keys=None):
        yield Conn()

    monkeypatch.setattr("app.services.collector.ssh_connect_host", connect)
    monkeypatch.setattr("app.services.collector.asyncssh.import_private_key", lambda pem: pem)


async def test_list_all_services_records_enablement(monkeypatch):
    _fake_host_connection(
        monkeypatch, {"list-units": LIST_UNITS, "list-unit-files": LIST_UNIT_FILES}
    )
    services = await list_all_services(SimpleNamespace(), None, "key")
    assert {s["unit"]: s["enabled"] for s in services} == {
        "alloy": True,
        "container-getty@1": False,
        "dbus": False,
        "getty@tty1": True,
    }


async def test_list_all_services_leaves_enablement_out_when_unknown(monkeypatch):
    """A host whose list-unit-files yields nothing gets no guess."""
    _fake_host_connection(monkeypatch, {"list-units": LIST_UNITS, "list-unit-files": ""})
    services = await list_all_services(SimpleNamespace(), None, "key")
    assert services and all("enabled" not in s for s in services)


async def _drift_verdict(db, collected_entry: dict) -> str:
    from app.api.host_state import _drift_service
    from app.models.host_module_status import HostModuleStatus
    from app.services.models import ServiceRule, ServiceState
    from tests.conftest import create_host

    host = await create_host(db)
    db.add(
        ServiceRule(host_id=host.id, service_name="alloy", state=ServiceState.running, enabled=True)
    )
    hms = HostModuleStatus(
        host_id=host.id, module_type="service", collected_state=[collected_entry]
    )
    db.add(hms)
    await db.flush()
    await _drift_service(host.id, hms, db)
    return hms.sync_status


ALLOY_RUNNING = {
    "unit": "alloy",
    "load_state": "loaded",
    "active_state": "active",
    "sub_state": "running",
    "description": "Vendor-neutral programmable observability pipelines.",
}


async def test_an_enabled_running_service_is_in_sync(db):
    assert await _drift_verdict(db, {**ALLOY_RUNNING, "enabled": True}) == "in_sync"


async def test_state_collected_without_enablement_is_not_a_mismatch(db):
    """What every host carried until this fix: the exact entry lin-manager
    and mail reported as drifted."""
    assert await _drift_verdict(db, ALLOY_RUNNING) == "in_sync"


async def test_a_disabled_service_is_still_drift(db):
    assert await _drift_verdict(db, {**ALLOY_RUNNING, "enabled": False}) == "out_of_sync"
