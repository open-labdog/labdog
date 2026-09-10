"""Opting a host into drift checking as it is created.

Drift checking is off by default and stays off — the default is a
deliberate product choice, not an oversight (see
``docs/ui/drift-detection.md``). What was missing is that nobody was ever
*asked*. The Add Host form now asks, at the moment someone is already
deciding to manage the host.

The interesting part is that one checkbox has to set seven things.
``Host.drift_check_enabled`` gates the firewall sweep and nothing else;
the other six modules each read their own ``HostModuleStatus`` row. A
control that set only the host flag would enable one seventh of what it
says, which is exactly the half-connected control BUG-82 was about.
"""

from sqlalchemy import select

from app.api.host_state import COLLECTABLE_MODULES, refresh_host_sync_status
from app.models.host import Host
from app.models.host_module_status import HostModuleStatus


async def _create(client, **overrides) -> dict:
    body = {"hostname": "drift-opt", "ip_address": "10.44.0.1", "ssh_user": "root"}
    body.update(overrides)
    resp = await client.post("/api/hosts", json=body)
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


async def _modules(db, host_id: int) -> dict[str, bool]:
    rows = (
        (await db.execute(select(HostModuleStatus).where(HostModuleStatus.host_id == host_id)))
        .scalars()
        .all()
    )
    return {r.module_type: r.drift_check_enabled for r in rows}


class TestTheDefaultIsStillOff:
    async def test_omitting_the_field_leaves_drift_off(self, superuser_client, db):
        """An existing API client must keep the behaviour it has."""
        created = await _create(superuser_client)

        host = await db.get(Host, created["id"])
        assert host.drift_check_enabled is False
        assert await _modules(db, host.id) == {}

    async def test_explicitly_false_is_the_same(self, superuser_client, db):
        created = await _create(superuser_client, drift_check_enabled=False)
        host = await db.get(Host, created["id"])
        assert host.drift_check_enabled is False


class TestOptingIn:
    async def test_it_sets_the_host_flag(self, superuser_client, db):
        created = await _create(superuser_client, drift_check_enabled=True)
        host = await db.get(Host, created["id"])
        assert host.drift_check_enabled is True

    async def test_it_enables_the_other_six_modules(self, superuser_client, db):
        """The half that a host-flag-only implementation would miss."""
        created = await _create(superuser_client, drift_check_enabled=True)

        modules = await _modules(db, created["id"])
        assert set(modules) == COLLECTABLE_MODULES - {"firewall"}
        assert all(modules.values())

    async def test_the_firewall_module_row_is_not_written(self, superuser_client, db):
        """Its module column has no reader — the host flag is what the
        firewall sweep consults. A second copy is a value to keep in sync
        for no benefit."""
        created = await _create(superuser_client, drift_check_enabled=True)
        assert "firewall" not in await _modules(db, created["id"])

    async def test_the_api_reports_it_back(self, superuser_client):
        created = await _create(superuser_client, drift_check_enabled=True)
        assert created["drift_check_enabled"] is True


class TestItDoesNotFakeASyncStatus:
    async def test_a_new_opted_in_host_is_not_reported_in_sync(self, superuser_client, db):
        """The hazard of writing module rows eagerly.

        ``refresh_host_sync_status`` walks the module statuses and falls
        through to ``in_sync`` for any non-empty set that has no error and
        no drift. Seven fresh rows all reading ``unknown`` must not be
        enough to call a host that has never been synced "in sync" — that
        would be a green badge earned by ticking a checkbox.
        """
        created = await _create(superuser_client, drift_check_enabled=True)
        host = await db.get(Host, created["id"])

        await refresh_host_sync_status(host, db)

        assert host.sync_status.value != "in_sync"
