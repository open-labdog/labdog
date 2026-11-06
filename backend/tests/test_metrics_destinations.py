"""Per-host and per-group metrics destinations.

A host's Alloy ships to the Mimir / Loki instance it resolves to: its own
assignment, else its highest-priority group's, else the default. The
action dispatch, the host page, the fleet read, the alert poll and the AI
tools all ask the same resolver, so these tests pin the precedence once
and then check that each consumer follows it.
"""

from __future__ import annotations

import dataclasses
import uuid
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest

from app.grafana.models import GrafanaInstance
from app.grafana.service import (
    METRICS_DESTINATIONS_PARAM,
    build_metrics_extra_vars,
    resolve_all_hosts,
    resolve_host_instance,
)
from tests.conftest import create_group, create_host

ALLOY_BACKEND = {
    "prometheus_push_var": "alloy_prometheus_url",
    "loki_push_var": "alloy_loki_url",
    "org_id_var": "alloy_mimir_org_id",
    "loki_tenant_var": "alloy_loki_tenant_id",
    "prometheus_tls_skip_verify_var": "alloy_prometheus_tls_insecure_skip_verify",
    "loki_tls_skip_verify_var": "alloy_loki_tls_insecure_skip_verify",
}


async def _instance(db, kind, *, default=False, org_id=None, url=None, verify_ssl=True):
    name = f"{kind}-{uuid.uuid4().hex[:6]}"
    inst = GrafanaInstance(
        name=name,
        kind=kind,
        url=url or f"https://{name}.test/api/v1/push",
        org_id=org_id,
        is_default=default,
        verify_ssl=verify_ssl,
    )
    db.add(inst)
    await db.flush()
    return inst


@pytest.fixture
def task_session_is_test_db(db):
    @asynccontextmanager
    async def _fake():
        yield db

    with patch("app.db.task_session", new=_fake):
        yield


class TestResolution:
    async def test_default_when_nothing_is_assigned(self, db):
        default = await _instance(db, "mimir", default=True)
        host = await create_host(db)

        r = await resolve_host_instance(db, host.id, "mimir")

        assert r.instance.id == default.id
        assert r.source == "default"

    async def test_highest_priority_group_beats_lower_and_default(self, db):
        await _instance(db, "mimir", default=True)
        low_dest = await _instance(db, "mimir")
        high_dest = await _instance(db, "mimir")
        low = await create_group(db)
        high = await create_group(db)  # created later, so higher priority
        assert high.priority > low.priority
        low.mimir_instance_id = low_dest.id
        high.mimir_instance_id = high_dest.id
        host = await create_host(db, group_ids=[low.id, high.id])
        await db.flush()

        r = await resolve_host_instance(db, host.id, "mimir")

        assert (r.instance.id, r.source, r.group_id) == (high_dest.id, "group", high.id)
        assert (await resolve_all_hosts(db, "mimir"))[host.id] == high_dest.id

    async def test_a_group_without_an_opinion_is_skipped(self, db):
        """A higher-priority group that sets nothing must not mask a lower
        one that does."""
        dest = await _instance(db, "loki")
        low = await create_group(db)
        high = await create_group(db)
        low.loki_instance_id = dest.id
        host = await create_host(db, group_ids=[low.id, high.id])
        await db.flush()

        assert (await resolve_host_instance(db, host.id, "loki")).instance.id == dest.id

    async def test_host_assignment_wins(self, db):
        group_dest = await _instance(db, "mimir")
        own = await _instance(db, "mimir")
        group = await create_group(db)
        group.mimir_instance_id = group_dest.id
        host = await create_host(db, group_ids=[group.id])
        host.mimir_instance_id = own.id
        await db.flush()

        r = await resolve_host_instance(db, host.id, "mimir")

        assert (r.instance.id, r.source) == (own.id, "host")
        assert (await resolve_all_hosts(db, "mimir"))[host.id] == own.id

    async def test_kinds_resolve_independently(self, db):
        mimir = await _instance(db, "mimir", default=True)
        loki_own = await _instance(db, "loki")
        host = await create_host(db)
        host.loki_instance_id = loki_own.id
        await db.flush()

        assert (await resolve_host_instance(db, host.id, "mimir")).instance.id == mimir.id
        assert (await resolve_host_instance(db, host.id, "loki")).instance.id == loki_own.id


@pytest.mark.usefixtures("task_session_is_test_db")
class TestDispatchVars:
    async def test_host_with_no_override_gets_the_default(self, db):
        mimir = await _instance(db, "mimir", default=True, org_id="team-a")
        loki = await _instance(db, "loki", default=True)
        host = await create_host(db)

        dest, identity = await build_metrics_extra_vars(host.id, host.hostname, ALLOY_BACKEND)

        assert dest == {
            "alloy_prometheus_url": mimir.url,
            "alloy_mimir_org_id": "team-a",
            "alloy_prometheus_tls_insecure_skip_verify": False,
            "alloy_loki_url": loki.url,
            "alloy_loki_tls_insecure_skip_verify": False,
        }
        assert identity == {"labdog_host_id": str(host.id), "labdog_hostname": host.hostname}

    async def test_each_host_gets_its_own_urls_and_tenants(self, db):
        await _instance(db, "mimir", default=True)
        await _instance(db, "loki", default=True)
        mimir_b = await _instance(db, "mimir", org_id="tenant-b")
        loki_b = await _instance(db, "loki", org_id="logs-b", verify_ssl=False)
        group = await create_group(db)
        group.mimir_instance_id = mimir_b.id
        group.loki_instance_id = loki_b.id
        member = await create_host(db, group_ids=[group.id])
        await db.flush()

        dest, _ = await build_metrics_extra_vars(member.id, member.hostname, ALLOY_BACKEND)

        assert dest["alloy_prometheus_url"] == mimir_b.url
        assert dest["alloy_mimir_org_id"] == "tenant-b"
        assert dest["alloy_loki_url"] == loki_b.url
        assert dest["alloy_loki_tenant_id"] == "logs-b"
        assert dest["alloy_loki_tls_insecure_skip_verify"] is True

    async def test_a_pick_in_the_run_dialog_overrides_assignments(self, db):
        assigned = await _instance(db, "mimir", org_id="assigned")
        picked = await _instance(db, "mimir", org_id="picked")
        host = await create_host(db)
        host.mimir_instance_id = assigned.id
        await db.flush()

        dest, _ = await build_metrics_extra_vars(
            host.id, host.hostname, ALLOY_BACKEND, picked={"mimir": picked.id}
        )

        assert dest["alloy_prometheus_url"] == picked.url
        assert dest["alloy_mimir_org_id"] == "picked"

    async def test_a_typed_url_keeps_the_hosts_tenant_on_a_shared_url(self, db):
        """Two destinations on one URL differ only by tenant. A run that
        names the URL without an instance id (an API caller, an older
        schedule) must keep the tenant the host is assigned."""
        shared = "https://mimir.shared.test/api/v1/push"
        await _instance(db, "mimir", default=True, url=shared, org_id="team-a")
        mine = await _instance(db, "mimir", url=shared, org_id="team-b")
        host = await create_host(db)
        host.mimir_instance_id = mine.id
        await db.flush()

        dest, _ = await build_metrics_extra_vars(
            host.id,
            host.hostname,
            ALLOY_BACKEND,
            operator_params={"alloy_prometheus_url": shared},
        )

        assert dest["alloy_mimir_org_id"] == "team-b"

    async def test_an_unregistered_typed_url_gets_no_tenant(self, db):
        """Another destination's tenant must not travel to a URL it was
        never registered with."""
        await _instance(db, "mimir", default=True, org_id="team-a")
        host = await create_host(db)

        dest, _ = await build_metrics_extra_vars(
            host.id,
            host.hostname,
            ALLOY_BACKEND,
            operator_params={"alloy_prometheus_url": "https://elsewhere.test/push"},
        )

        assert "alloy_mimir_org_id" not in dest
        assert "alloy_prometheus_tls_insecure_skip_verify" not in dest

    async def test_nothing_injected_without_metrics_backend(self, db):
        await _instance(db, "mimir", default=True)
        host = await create_host(db)

        dest, identity = await build_metrics_extra_vars(host.id, host.hostname, None)

        assert dest == {}
        assert identity["labdog_host_id"] == str(host.id)


class TestApi:
    async def test_host_destinations_round_trip(self, superuser_client, db):
        default = await _instance(db, "mimir", default=True)
        own = await _instance(db, "loki")
        host = await create_host(db)

        resp = await superuser_client.put(
            f"/api/grafana/destinations/hosts/{host.id}",
            json={"mimir_instance_id": None, "loki_instance_id": own.id},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["assigned"] == {"mimir_instance_id": None, "loki_instance_id": own.id}
        assert body["effective"]["mimir"]["instance_id"] == default.id
        assert body["effective"]["mimir"]["source"] == "default"
        assert body["effective"]["loki"]["source"] == "host"

    async def test_wrong_kind_is_refused(self, superuser_client, db):
        loki = await _instance(db, "loki")
        group = await create_group(db)

        resp = await superuser_client.put(
            f"/api/grafana/destinations/groups/{group.id}",
            json={"mimir_instance_id": loki.id},
        )
        assert resp.status_code == 422

    async def test_group_destinations_round_trip(self, superuser_client, db):
        mimir = await _instance(db, "mimir")
        group = await create_group(db)

        resp = await superuser_client.put(
            f"/api/grafana/destinations/groups/{group.id}",
            json={"mimir_instance_id": mimir.id},
        )
        assert resp.status_code == 200, resp.text
        got = await superuser_client.get(f"/api/grafana/destinations/groups/{group.id}")
        assert got.json() == {"mimir_instance_id": mimir.id, "loki_instance_id": None}

    async def test_an_instance_in_use_cannot_be_deleted(self, superuser_client, db):
        inst = await _instance(db, "mimir")
        host = await create_host(db)
        host.mimir_instance_id = inst.id
        await db.flush()

        resp = await superuser_client.delete(f"/api/grafana/instances/{inst.id}")

        assert resp.status_code == 409
        assert host.hostname in resp.text

    async def test_host_page_queries_the_hosts_own_destination(self, superuser_client, db):
        await _instance(db, "mimir", default=True)
        own = await _instance(db, "mimir")
        host = await create_host(db)
        host.mimir_instance_id = own.id
        await db.flush()

        seen = {}

        async def _fetch(client, host_id):
            from app.grafana.schemas import HostMetrics

            seen["url"] = client.query_url
            return HostMetrics(configured=True)

        with patch("app.api.grafana.fetch_host_metrics", new=_fetch):
            resp = await superuser_client.get(f"/api/grafana/hosts/{host.id}/metrics")

        assert resp.status_code == 200, resp.text
        assert seen["url"].startswith(f"https://{own.name}.test")

    async def test_fleet_reads_each_host_from_its_destination(self, superuser_client, db):
        """A host's series are taken only from the destination it ships
        to, even when another destination also has data for its id."""
        from app.grafana.schemas import FleetHostMetrics

        default = await _instance(db, "mimir", default=True)
        other = await _instance(db, "mimir")
        a = await create_host(db)
        b = await create_host(db, ip="10.0.0.2")
        b.mimir_instance_id = other.id
        await db.flush()

        asked: dict[str, set[int]] = {}

        async def _fleet(client, hostnames):
            asked[client.query_url] = set(hostnames)
            rows = [FleetHostMetrics(host_id=h, hostname=n, cpu=1.0) for h, n in hostnames.items()]
            return rows, 1000.0

        with patch("app.api.grafana.fetch_fleet_metrics", new=_fleet):
            resp = await superuser_client.get("/api/grafana/fleet/metrics")

        assert resp.status_code == 200, resp.text
        by_url = {u.split("/")[2].split(".")[0]: ids for u, ids in asked.items()}
        assert a.id in by_url[default.name] and b.id not in by_url[default.name]
        assert by_url[other.name] == {b.id}


@pytest.mark.integration
@pytest.mark.usefixtures("sample_pack")
class TestRunCreate:
    @pytest.fixture(autouse=True)
    def _no_dispatch(self):
        with (
            patch("app.api.actions.celery_app", create=True),
            patch("celery.app.base.Celery.send_task"),
        ):
            yield

    async def test_refused_for_an_action_without_metrics_backend(self, superuser_client, db):
        mimir = await _instance(db, "mimir")
        host = await create_host(db)
        resp = await superuser_client.post(
            "/api/actions/runs",
            json={
                "action_key": "example",
                "host_id": host.id,
                "metrics_destinations": {"mimir": mimir.id},
            },
        )
        assert resp.status_code == 422, resp.text

    async def test_stored_under_the_reserved_key(self, superuser_client, db):
        from app.actions.registry import ACTION_REGISTRY
        from app.models.action_run import ActionRun

        mimir = await _instance(db, "mimir")
        host = await create_host(db)
        example = ACTION_REGISTRY["example"]
        with_backend = dataclasses.replace(example, metrics_backend=dict(ALLOY_BACKEND))
        with patch.dict(ACTION_REGISTRY, {"example": with_backend}):
            resp = await superuser_client.post(
                "/api/actions/runs",
                json={
                    "action_key": "example",
                    "host_id": host.id,
                    "metrics_destinations": {"mimir": mimir.id},
                    # An inbound copy is discarded, not honoured.
                    "parameters": {METRICS_DESTINATIONS_PARAM: {"mimir": 999}},
                },
            )
        assert resp.status_code == 201, resp.text
        run = await db.get(ActionRun, resp.json()["id"])
        assert run.parameters[METRICS_DESTINATIONS_PARAM] == {"mimir": mimir.id}
