"""The firewall module row must report the flag its toggle actually writes.

Firewall drift is the one sweep still gated by ``Host.drift_check_enabled``
(``drift_sweep.sweep_module(..., host_gated=True)``); the other six read
their own ``HostModuleStatus.drift_check_enabled``. Nothing anywhere writes
the firewall module's copy of that column, so it sits at its ``false``
server default forever.

``GET /hosts/{id}/current-state`` reported that column regardless, and the
Host detail page renders the row's "Enable Drift Check" / "Disable Drift
Check" label from it while the button PUTs
``/api/drift/hosts/{id}/settings`` — which sets the *host* flag. The label
therefore never changed, and each click silently alternated drift checking
for the whole host.
"""

from app.models.host_module_status import HostModuleStatus
from tests.conftest import create_host


async def _firewall_row(client, host_id: int) -> dict:
    resp = await client.get(f"/api/hosts/{host_id}/current-state")
    assert resp.status_code == 200
    rows = [m for m in resp.json() if m["module_type"] == "firewall"]
    assert rows, "expected a firewall module row"
    return rows[0]


class TestTheFirewallRowFollowsTheHostFlag:
    async def test_enabling_host_drift_shows_on_the_firewall_row(self, superuser_client, db):
        host = await create_host(db, hostname="fw-drift-on")
        db.add(HostModuleStatus(host_id=host.id, module_type="firewall", sync_status="unknown"))
        await db.commit()

        assert (await _firewall_row(superuser_client, host.id))["drift_check_enabled"] is False

        resp = await superuser_client.put(
            f"/api/drift/hosts/{host.id}/settings", json={"drift_check_enabled": True}
        )
        assert resp.status_code == 200

        assert (await _firewall_row(superuser_client, host.id))["drift_check_enabled"] is True, (
            "the row must report the host flag its own toggle writes — reporting "
            "HostModuleStatus.drift_check_enabled means the label never changes"
        )

    async def test_disabling_it_again_shows_too(self, superuser_client, db):
        host = await create_host(db, hostname="fw-drift-off")
        db.add(HostModuleStatus(host_id=host.id, module_type="firewall", sync_status="unknown"))
        await db.commit()

        for target in (True, False):
            await superuser_client.put(
                f"/api/drift/hosts/{host.id}/settings", json={"drift_check_enabled": target}
            )
            row = await _firewall_row(superuser_client, host.id)
            assert row["drift_check_enabled"] is target

    async def test_the_host_level_route_is_the_one_that_moves_it(self, superuser_client, db):
        """Writing the module column directly must not change what is reported.

        This is the asymmetry worth pinning: for firewall the module column
        is inert, so a stray write to it (a future per-module toggle wired
        up by mistake) must not make the row claim drift is on when the
        sweep would still skip the host.
        """
        host = await create_host(db, hostname="fw-drift-inert")
        hms = HostModuleStatus(
            host_id=host.id,
            module_type="firewall",
            sync_status="unknown",
            drift_check_enabled=True,
        )
        db.add(hms)
        await db.commit()

        row = await _firewall_row(superuser_client, host.id)
        assert row["drift_check_enabled"] is False, (
            "host flag is off, so firewall drift is off — the module column is inert here"
        )


class TestTheOtherModulesKeepTheirOwnFlag:
    async def test_a_per_module_flag_is_reported_unchanged(self, superuser_client, db):
        """The six per-module sweeps read HostModuleStatus, so it must win there."""
        host = await create_host(db, hostname="svc-drift")
        db.add(
            HostModuleStatus(
                host_id=host.id,
                module_type="service",
                sync_status="unknown",
                drift_check_enabled=True,
            )
        )
        await db.commit()

        resp = await superuser_client.get(f"/api/hosts/{host.id}/current-state")
        svc = next(m for m in resp.json() if m["module_type"] == "service")
        assert svc["drift_check_enabled"] is True
        # ...and the host flag is still off, proving the two are independent.
        h = await superuser_client.get(f"/api/hosts/{host.id}")
        assert h.json()["drift_check_enabled"] is False
