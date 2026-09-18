"""``GET /api/dashboard/drift-coverage`` — is anything being checked at all?

Every empty drift surface raises the same question and none of them could
answer it: no results because nothing drifted, or because nothing is being
checked? On a fresh install it is always the second, permanently and
silently — every flag defaults to off and the sweep finds no candidates.

The two flags are independent (see ``docs/ui/drift-detection.md``), so the
interesting cases are the asymmetric ones: firewall-only coverage,
module-only coverage, and a host that has both without being counted twice.
"""

from app.models.host_module_status import HostModuleStatus
from tests.conftest import create_host


async def _coverage(client) -> dict:
    resp = await client.get("/api/dashboard/drift-coverage")
    assert resp.status_code == 200
    return resp.json()


async def _enable_module(db, host_id: int, module_type: str) -> None:
    db.add(
        HostModuleStatus(
            host_id=host_id,
            module_type=module_type,
            sync_status="unknown",
            drift_check_enabled=True,
        )
    )
    await db.commit()


class TestTheEmptyFleet:
    async def test_no_hosts_is_all_zeroes(self, superuser_client):
        cov = await _coverage(superuser_client)
        assert cov == {
            "hosts_total": 0,
            "firewall_enabled_hosts": 0,
            "module_enabled_hosts": 0,
            "any_enabled_hosts": 0,
        }

    async def test_a_fresh_host_is_covered_by_nothing(self, superuser_client, db):
        """The state a fresh install sits in, and the reason this exists."""
        await create_host(db, hostname="fresh")
        cov = await _coverage(superuser_client)
        assert cov["hosts_total"] == 1
        assert cov["any_enabled_hosts"] == 0


class TestTheTwoFlagsCountSeparately:
    async def test_the_host_flag_counts_as_firewall_coverage(self, superuser_client, db):
        host = await create_host(db, hostname="fw-only")
        await superuser_client.put(
            f"/api/drift/hosts/{host.id}/settings", json={"drift_check_enabled": True}
        )

        cov = await _coverage(superuser_client)
        assert cov["firewall_enabled_hosts"] == 1
        assert cov["module_enabled_hosts"] == 0
        assert cov["any_enabled_hosts"] == 1

    async def test_a_module_flag_counts_without_the_host_flag(self, superuser_client, db):
        host = await create_host(db, hostname="svc-only")
        await _enable_module(db, host.id, "service")

        cov = await _coverage(superuser_client)
        assert cov["firewall_enabled_hosts"] == 0
        assert cov["module_enabled_hosts"] == 1
        assert cov["any_enabled_hosts"] == 1

    async def test_the_firewall_module_row_is_not_counted(self, superuser_client, db):
        """``HostModuleStatus`` for firewall has no writer and is always false.

        If it were ever set — by hand, or by a per-module toggle wired up by
        mistake — it must not read as coverage, because the sweep is
        host-gated and would still skip the host.
        """
        host = await create_host(db, hostname="fw-module-row")
        await _enable_module(db, host.id, "firewall")

        cov = await _coverage(superuser_client)
        assert cov["module_enabled_hosts"] == 0
        assert cov["any_enabled_hosts"] == 0


class TestTheUnionIsNotTheSum:
    async def test_a_host_with_both_is_counted_once(self, superuser_client, db):
        host = await create_host(db, hostname="both")
        await _enable_module(db, host.id, "service")
        await superuser_client.put(
            f"/api/drift/hosts/{host.id}/settings", json={"drift_check_enabled": True}
        )

        cov = await _coverage(superuser_client)
        assert cov["firewall_enabled_hosts"] == 1
        assert cov["module_enabled_hosts"] == 1
        assert cov["any_enabled_hosts"] == 1, "the union, not the sum"

    async def test_several_modules_on_one_host_is_still_one_host(self, superuser_client, db):
        host = await create_host(db, hostname="many-modules")
        for module in ("service", "cron", "package"):
            await _enable_module(db, host.id, module)

        cov = await _coverage(superuser_client)
        assert cov["module_enabled_hosts"] == 1
        assert cov["any_enabled_hosts"] == 1

    async def test_distinct_hosts_add_up(self, superuser_client, db):
        a = await create_host(db, hostname="cov-a")
        b = await create_host(db, hostname="cov-b")
        await create_host(db, hostname="cov-c")
        await _enable_module(db, a.id, "cron")
        await superuser_client.put(
            f"/api/drift/hosts/{b.id}/settings", json={"drift_check_enabled": True}
        )

        cov = await _coverage(superuser_client)
        assert cov["hosts_total"] == 3
        assert cov["any_enabled_hosts"] == 2


class TestItNeedsAuth:
    async def test_anonymous_is_refused(self, client):
        resp = await client.get("/api/dashboard/drift-coverage")
        assert resp.status_code == 401
