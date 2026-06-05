"""Unit tests for the Grafana metrics layer — pure logic, no DB/network."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.grafana import metrics as m
from app.grafana.schemas import GrafanaInstanceCreate, derive_query_url


def test_promql_builders_filter_by_host_id():
    for q in (
        m.cpu_percent_query(7),
        m.cpu_cores_query(7),
        m.mem_total_query(7),
        m.mem_available_query(7),
        m.disk_size_query(7),
        m.disk_available_query(7),
    ):
        assert 'labdog_host_id="7"' in q
    # Root filesystem only, pseudo-filesystems excluded.
    assert 'mountpoint="/"' in m.disk_size_query(7)
    assert "tmpfs" in m.disk_size_query(7)


class _FakeClient:
    """Returns canned (value, ts) per distinctive query substring."""

    def __init__(self, mapping: dict[str, tuple[float, float]]):
        self.mapping = mapping

    async def query_scalar(self, promql: str):
        for key, val in self.mapping.items():
            if key in promql:
                return val
        return None


async def test_fetch_host_metrics_computes_values():
    ts = 1000.0
    client = _FakeClient(
        {
            'mode="idle"': (23.0, ts),  # cpu_percent
            "count by (cpu)": (8.0, ts),  # cpu_cores
            "MemTotal": (16e9, ts),
            "MemAvailable": (4e9, ts),
            "node_filesystem_size_bytes": (100e9, ts),
            "node_filesystem_avail_bytes": (10e9, ts),
        }
    )
    hm = await m.fetch_host_metrics(client, 7)  # type: ignore[arg-type]

    assert hm.configured is True
    assert hm.error is None
    assert hm.cpu is not None and hm.cpu.percent == 23.0
    assert hm.cpu.total == 8.0 and hm.cpu.unit == "cores"
    assert hm.memory is not None and hm.memory.percent == 75.0  # (16-4)/16
    assert hm.memory.used == 12e9 and hm.memory.total == 16e9
    assert hm.disk is not None and hm.disk.percent == 90.0  # (100-10)/100
    assert hm.sampled_at is not None


async def test_fetch_host_metrics_no_data():
    hm = await m.fetch_host_metrics(_FakeClient({}), 7)  # type: ignore[arg-type]
    assert hm.configured is True
    assert hm.sampled_at is None
    assert hm.cpu is None and hm.memory is None and hm.disk is None


def test_schema_strips_trailing_slash_and_requires_scheme():
    inst = GrafanaInstanceCreate(
        name="hl",
        kind="mimir",
        url="http://mimir:9009/api/v1/push/",
    )
    assert inst.url == "http://mimir:9009/api/v1/push"

    with pytest.raises(ValidationError):
        GrafanaInstanceCreate(name="bad", kind="mimir", url="mimir:9009")  # no scheme

    with pytest.raises(ValidationError):
        GrafanaInstanceCreate(name="bad", kind="splunk", url="http://x:1")  # bad kind


def test_derive_query_url_strips_path_and_appends_kind_prefix():
    # The operator's push URL → host-only + the kind's query prefix.
    assert (
        derive_query_url("https://mimir.lan/api/v1/push", "mimir") == "https://mimir.lan/prometheus"
    )
    assert derive_query_url("https://loki.lan/loki/api/v1/push", "loki") == "https://loki.lan/loki"
    # Port preserved; trailing path discarded.
    assert (
        derive_query_url("http://mimir:9009/api/v1/push", "mimir") == "http://mimir:9009/prometheus"
    )
