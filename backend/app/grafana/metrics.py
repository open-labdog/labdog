"""Build and run the instant CPU/memory/disk queries for a host.

Metrics are matched by the ``labdog_host_id`` external label that the
Alloy install action stamps on every series (= LabDog's DB host id), so
queries are immune to hostname/IP changes. PromQL builders are pure for
easy testing; ``fetch_host_metrics`` runs them concurrently.
"""

from __future__ import annotations

import asyncio

from app.grafana.client import PrometheusClient
from app.grafana.schemas import HostMetrics, MetricValue

# node_exporter pseudo-filesystems we never want to treat as "disk".
_FS_EXCLUDE = "tmpfs|overlay|squashfs|ramfs|devtmpfs"


def cpu_percent_query(host_id: int) -> str:
    # 5m rate window (not 2m): CPU is the only counter-derived metric, so it
    # needs >=2 samples inside the window or rate() returns nothing and the
    # tile blanks out. A 5m window matches Prometheus' default lookback, so
    # CPU tolerates ingestion gaps/jitter the same as the instant gauges
    # (memory/disk) and stops intermittently disappearing on refresh.
    return (
        f"100 - (avg(rate(node_cpu_seconds_total"
        f'{{labdog_host_id="{host_id}",mode="idle"}}[5m])) * 100)'
    )


def cpu_cores_query(host_id: int) -> str:
    return f'count(count by (cpu)(node_cpu_seconds_total{{labdog_host_id="{host_id}"}}))'


def mem_total_query(host_id: int) -> str:
    return f'node_memory_MemTotal_bytes{{labdog_host_id="{host_id}"}}'


def mem_available_query(host_id: int) -> str:
    return f'node_memory_MemAvailable_bytes{{labdog_host_id="{host_id}"}}'


def _root_fs_selector(host_id: int) -> str:
    return f'labdog_host_id="{host_id}",mountpoint="/",fstype!~"{_FS_EXCLUDE}"'


def disk_size_query(host_id: int) -> str:
    return f"node_filesystem_size_bytes{{{_root_fs_selector(host_id)}}}"


def disk_available_query(host_id: int) -> str:
    return f"node_filesystem_avail_bytes{{{_root_fs_selector(host_id)}}}"


async def fetch_host_metrics(client: PrometheusClient, host_id: int) -> HostMetrics:
    """Query the backend for one host's instant CPU/memory/disk.

    Returns ``HostMetrics(configured=True, ...)``. When the host has no
    series yet (agent not installed / no data) the three values are ``None``
    and ``sampled_at`` is ``None``. Propagates :class:`PrometheusError` to
    the caller, which maps it to the ``error`` field.
    """
    (
        cpu_pct,
        cpu_cores,
        mem_total,
        mem_avail,
        disk_size,
        disk_avail,
    ) = await asyncio.gather(
        client.query_scalar(cpu_percent_query(host_id)),
        client.query_scalar(cpu_cores_query(host_id)),
        client.query_scalar(mem_total_query(host_id)),
        client.query_scalar(mem_available_query(host_id)),
        client.query_scalar(disk_size_query(host_id)),
        client.query_scalar(disk_available_query(host_id)),
    )

    sampled_ts: float | None = None

    def _track(scalar: tuple[float, float] | None) -> float | None:
        nonlocal sampled_ts
        if scalar is None:
            return None
        value, ts = scalar
        sampled_ts = ts if sampled_ts is None else max(sampled_ts, ts)
        return value

    cpu_v = _track(cpu_pct)
    cores_v = _track(cpu_cores)
    mt = _track(mem_total)
    ma = _track(mem_avail)
    ds = _track(disk_size)
    da = _track(disk_avail)

    cpu: MetricValue | None = None
    if cpu_v is not None:
        cpu = MetricValue(percent=round(max(0.0, min(100.0, cpu_v)), 1))
        if cores_v is not None and cores_v > 0:
            cpu.total = cores_v
            cpu.used = round(cores_v * cpu.percent / 100, 2)
            cpu.unit = "cores"

    memory: MetricValue | None = None
    if mt is not None and mt > 0 and ma is not None:
        used = mt - ma
        memory = MetricValue(
            percent=round(max(0.0, min(100.0, used / mt * 100)), 1),
            used=used,
            total=mt,
            unit="bytes",
        )

    disk: MetricValue | None = None
    if ds is not None and ds > 0 and da is not None:
        used = ds - da
        disk = MetricValue(
            percent=round(max(0.0, min(100.0, used / ds * 100)), 1),
            used=used,
            total=ds,
            unit="bytes",
        )

    from datetime import UTC, datetime

    sampled_at = datetime.fromtimestamp(sampled_ts, tz=UTC) if sampled_ts is not None else None
    return HostMetrics(configured=True, sampled_at=sampled_at, cpu=cpu, memory=memory, disk=disk)
