"""Point-in-time metric collection + TTL cache for the Prometheus exporter.

Turns the raw rows from ``app.metrics.aggregates`` into
``app.metrics.exposition.MetricFamily`` objects, applies the zero-fill rule
for the four statically-known enum columns, and caches the result for
``settings.metrics.cache_ttl_seconds`` behind a single-flight
``asyncio.Lock`` so N concurrent scrapes trigger exactly one collection.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.metrics import aggregates as agg
from app.metrics import broker as broker_probe
from app.metrics.aggregates import _BUCKETS_DRIFT, _BUCKETS_SYNC
from app.metrics.exposition import Labels, MetricFamily, counter, gauge, histogram
from app.models.git_repository import GitAuthType, GitOpsStatus
from app.models.host import SyncStatus
from app.models.sync_job import JobStatus
from app.tasks.sync_sweeper import STALE_THRESHOLD_MINUTES

# ---------------------------------------------------------------------------
# Zero-fill enums — see app.metrics.aggregates module docstring: ONLY these
# four columns are declared as a SQLAlchemy Enum(...) of one of these four
# Python enum classes. Every other label (module_type, action_key, the
# various free-text `status` / `sync_status` String(20) columns) is
# observed-values-only — never zero-filled, never hard-coded here.
# ---------------------------------------------------------------------------

_SYNC_STATUS_VALUES = [s.value for s in SyncStatus]
_JOB_STATUS_VALUES = [s.value for s in JobStatus]
_GITOPS_STATUS_VALUES = [s.value for s in GitOpsStatus]
_GIT_AUTH_TYPE_VALUES = [s.value for s in GitAuthType]

# Non-terminal ActionRun / SyncJob statuses considered "in flight".
_INFLIGHT_ACTION_STATUSES = frozenset({"queued", "pending", "running"})


@dataclass(frozen=True, slots=True)
class Snapshot:
    families: tuple[MetricFamily, ...]
    collected_at: datetime
    collect_duration_seconds: float


_cache: Snapshot | None = None
_cache_lock = asyncio.Lock()
_scrape_errors_total = 0


def reset_cache() -> None:
    """Test hook: drop the cached snapshot and the process-local error
    counter so each test starts from a clean slate. Not used in
    production — the cache is meant to survive across requests."""
    global _cache, _scrape_errors_total
    _cache = None
    _scrape_errors_total = 0


def _labels(*pairs: tuple[str, str]) -> Labels:
    return tuple(pairs)


class _SimpleCounts:
    """The ``get_simple_counts`` rows, split back out by family."""

    def __init__(self, rows: list[tuple[str, str, int]]) -> None:
        self._by_family: dict[str, list[tuple[str, int]]] = {}
        for family, label, value in rows:
            self._by_family.setdefault(family, []).append((label, value))

    def single(self, family: str) -> int:
        """The family's one value, or 0 when it has no row."""
        rows = self._by_family.get(family, [])
        return rows[0][1] if rows else 0

    def grouped(self, family: str) -> dict[str, int]:
        """The family's rows as ``{label: value}``."""
        return dict(self._by_family.get(family, []))


async def collect(db: AsyncSession) -> list[MetricFamily]:
    """Run every point-in-time aggregation and build the metric families.

    Do **not** ``asyncio.gather`` these awaits — they all share one
    ``AsyncSession`` / one asyncpg connection, and asyncpg raises
    ``InterfaceError: another operation is in progress`` the moment two
    coroutines try to drive the same connection concurrently. This is the
    exact hazard documented at the top of ``app.metrics.recorder`` for the
    same underlying reason (one connection, one in-flight query at a time).
    Run everything sequentially; the 15s cache + the UNION-ALL statement
    collapsing in ``aggregates.py`` is what keeps this cheap, not
    parallelism.
    """
    simple = _SimpleCounts(await agg.get_simple_counts(db))
    rule_counts = await agg.get_desired_state_rule_counts(db)
    max_ages = await agg.get_max_ages(db)
    host_module_counts = await agg.get_host_module_counts(db)
    # One scan each of sync_jobs and drift_samples for every family read
    # from them (BUG-160).
    sync_jobs = await agg.get_sync_job_aggregates(db)
    drift = await agg.get_drift_aggregates(db)
    action_run_counts = await agg.get_action_run_counts(db)
    action_run_origin_counts = await agg.get_action_run_origin_counts(db)
    action_host_run_counts = await agg.get_action_host_run_counts(db)
    scheduled_action_state = await agg.get_scheduled_action_state(db)
    stale_ops = await agg.get_stale_operation_counts(db)
    ca_cert_expiries = await agg.get_ca_cert_expiries(db)
    broker = await broker_probe.probe()

    return [
        *_broker_families(broker),
        *_build_info_families(),
        *_host_families(simple, max_ages),
        *_host_module_families(host_module_counts),
        *_rule_families(rule_counts),
        *_group_families(simple, max_ages),
        *_ca_cert_families(ca_cert_expiries),
        *_scan_families(simple),
        *_user_families(simple),
        *_integration_families(simple),
        *_sync_job_families(sync_jobs.counts, stale_ops),
        *_drift_families(drift.counts, drift.change_sums),
        *_action_run_families(
            action_run_counts, action_run_origin_counts, action_host_run_counts, simple
        ),
        *_scheduling_and_pack_families(scheduled_action_state, stale_ops, max_ages, simple),
        *_histogram_families(sync_jobs.duration, drift.duration),
    ]


def _broker_families(broker: broker_probe.BrokerSnapshot) -> list[MetricFamily]:
    """Broker reachability, and queue depth when the broker answered.

    The only family here that does not come from PostgreSQL. Kept behind
    a hard timeout because it puts a second failure domain into an
    unauthenticated request path; see ``app.metrics.broker``.

    On an unreachable broker the depth gauges are omitted rather than
    zero-filled. A zero would read as "the queues are empty" — the
    opposite of what is known — and would silence the "work is piling
    up" alert at the moment it should fire. The zero-fill rule at the
    top of this module is about *label values* for known enums, not
    about inventing a value for a measurement that failed.
    """
    families = [
        gauge(
            "labdog_broker_reachable",
            "1 if the Celery broker answered within its timeout, 0 otherwise. "
            "When 0, labdog_broker_queue_depth is absent rather than zero.",
            [((), 1.0 if broker.reachable else 0.0)],
        )
    ]
    if broker.depths is not None:
        families.append(
            gauge(
                "labdog_broker_queue_depth",
                "Tasks waiting in each Celery queue. Waiting only — a task a "
                "worker has already picked up is not in the queue. Absent when "
                "the broker is unreachable.",
                [
                    (_labels(("queue", queue)), float(depth))
                    for queue, depth in sorted(broker.depths.items())
                ],
            )
        )
    return families


def _build_info_families() -> list[MetricFamily]:
    from app.api.version import _BUILD_DATE, _COMMIT_SHA, _VERSION

    return [
        gauge(
            "labdog_build_info",
            "Always 1; labels carry the running build's identity.",
            [
                (
                    _labels(
                        ("version", _VERSION),
                        ("commit_sha", _COMMIT_SHA or ""),
                        ("build_date", _BUILD_DATE or ""),
                    ),
                    1.0,
                )
            ],
        )
    ]


def _host_families(simple: _SimpleCounts, max_ages: agg.MaxAges) -> list[MetricFamily]:
    hosts_by_status = simple.grouped("hosts_sync_status")
    hosts_status_series = [
        (_labels(("sync_status", v)), float(hosts_by_status.get(v, 0))) for v in _SYNC_STATUS_VALUES
    ]
    hosts_by_backend = simple.grouped("hosts_firewall_backend")
    families = [
        gauge(
            "labdog_hosts",
            "Number of hosts, by sync status.",
            hosts_status_series,
        ),
        gauge(
            "labdog_hosts_firewall_backend",
            "Number of hosts, by firewall backend (observed values only).",
            [(_labels(("backend", v)), float(c)) for v, c in sorted(hosts_by_backend.items())],
        ),
        gauge(
            "labdog_hosts_drift_check_enabled",
            "Number of hosts with drift checking enabled.",
            [((), float(simple.single("hosts_drift_check_enabled")))],
        ),
        gauge(
            "labdog_hosts_never_synced",
            "Number of hosts that have never completed a sync.",
            [((), float(simple.single("hosts_never_synced")))],
        ),
        gauge(
            "labdog_hosts_never_drift_checked",
            "Number of hosts that have never had a drift check.",
            [((), float(simple.single("hosts_never_drift_checked")))],
        ),
    ]
    if max_ages.host_last_sync_age_seconds is not None:
        families.append(
            gauge(
                "labdog_host_last_sync_age_seconds_max",
                "Seconds since the most recently synced host last synced.",
                [((), float(max_ages.host_last_sync_age_seconds))],
            )
        )
    if max_ages.host_last_drift_check_age_seconds is not None:
        families.append(
            gauge(
                "labdog_host_last_drift_check_age_seconds_max",
                "Seconds since the most recently drift-checked host was last checked.",
                [((), float(max_ages.host_last_drift_check_age_seconds))],
            )
        )
    return families


def _host_module_families(host_module_counts: list[tuple[str, str, int]]) -> list[MetricFamily]:
    return [
        gauge(
            "labdog_host_modules",
            "Number of per-host module status rows, by module and sync status "
            "(both observed values — free text, not enums).",
            [
                (_labels(("module", module), ("sync_status", status)), float(count))
                for module, status, count in sorted(host_module_counts)
            ],
        )
    ]


def _rule_families(rule_counts: list[tuple[str, int]]) -> list[MetricFamily]:
    return [
        gauge(
            "labdog_module_rules",
            "Number of desired-state rule rows (host- + group-scoped), by module.",
            [(_labels(("module", module)), float(count)) for module, count in sorted(rule_counts)],
        )
    ]


def _group_families(simple: _SimpleCounts, max_ages: agg.MaxAges) -> list[MetricFamily]:
    groups_gitops = simple.grouped("groups_gitops")
    families = [
        gauge(
            "labdog_groups",
            "Total number of host groups.",
            [((), float(simple.single("groups")))],
        ),
        gauge(
            "labdog_groups_gitops",
            "Number of groups, by GitOps status.",
            [
                (_labels(("status", v)), float(groups_gitops.get(v, 0)))
                for v in _GITOPS_STATUS_VALUES
            ],
        ),
    ]
    if max_ages.group_gitops_last_import_age_seconds is not None:
        families.append(
            gauge(
                "labdog_group_gitops_last_import_age_seconds_max",
                "Seconds since the most recently GitOps-imported group last imported.",
                [((), float(max_ages.group_gitops_last_import_age_seconds))],
            )
        )
    return families


def _ca_cert_families(ca_cert_expiries: list[tuple[str, str, datetime]]) -> list[MetricFamily]:
    """CA certificate expiry, labelled by name only.

    SEC-34: labelled by name only. The endpoint is unauthenticated, and
    everything else it emits is an aggregate count — a per-certificate
    SHA-256 fingerprint is an inventory of the trust unbork manages,
    handed to anyone who can reach the scrape URL. The name is what an
    alert needs to say which certificate is expiring.
    """
    return [
        gauge(
            "labdog_ca_cert_not_after_timestamp_seconds",
            "CA certificate expiry (notAfter), as a Unix timestamp.",
            [
                (_labels(("name", name)), not_after.timestamp())
                for name, _fingerprint, not_after in sorted(ca_cert_expiries)
            ],
        )
    ]


def _scan_families(simple: _SimpleCounts) -> list[MetricFamily]:
    scan_configs = simple.grouped("scan_configs")
    scan_config_last_run = simple.grouped("scan_config_last_run")
    return [
        gauge(
            "labdog_scan_pending_hosts",
            "Number of discovered hosts awaiting review (not yet added).",
            [((), float(simple.single("scan_pending_hosts")))],
        ),
        gauge(
            "labdog_scan_configs",
            "Number of scan configs, by enabled state.",
            [(_labels(("state", v)), float(c)) for v, c in sorted(scan_configs.items())],
        ),
        gauge(
            "labdog_scan_config_last_run",
            "Number of scan configs, by last-run status ('never' if the config has not run yet).",
            [(_labels(("status", v)), float(c)) for v, c in sorted(scan_config_last_run.items())],
        ),
    ]


def _user_families(simple: _SimpleCounts) -> list[MetricFamily]:
    users_by_state = simple.grouped("users")
    return [
        gauge(
            "labdog_users",
            "Number of user accounts, by active state.",
            [(_labels(("state", v)), float(c)) for v, c in sorted(users_by_state.items())],
        ),
        gauge(
            "labdog_superusers",
            "Number of superuser accounts.",
            [((), float(simple.single("superusers")))],
        ),
    ]


def _integration_families(simple: _SimpleCounts) -> list[MetricFamily]:
    """Git repositories, Grafana-stack instances and Proxmox VM mappings."""
    git_repos = simple.grouped("git_repositories")
    grafana_instances = simple.grouped("grafana_instances")
    return [
        gauge(
            "labdog_git_repositories",
            "Number of configured git repositories, by auth type.",
            [
                (_labels(("auth_type", v)), float(git_repos.get(v, 0)))
                for v in _GIT_AUTH_TYPE_VALUES
            ],
        ),
        gauge(
            "labdog_grafana_instances",
            "Number of registered Grafana-stack instances, by kind.",
            [(_labels(("kind", v)), float(c)) for v, c in sorted(grafana_instances.items())],
        ),
        gauge(
            "labdog_vm_mappings",
            "Number of hosts mapped to a Proxmox VM identity.",
            [((), float(simple.single("vm_mappings")))],
        ),
    ]


def _sync_job_families(
    sync_job_counts: list[tuple[str, str, int]],
    stale_ops: agg.StaleOperationCounts,
) -> list[MetricFamily]:
    sync_job_modules = sorted({module for _, module, _ in sync_job_counts})
    sync_job_grid: dict[tuple[str, str], int] = {
        (status, module): count for status, module, count in sync_job_counts
    }
    sync_jobs_total_series = []
    sync_jobs_inflight_series = []
    for module in sync_job_modules:
        for status in _JOB_STATUS_VALUES:
            count = sync_job_grid.get((status, module), 0)
            labels = _labels(("status", status), ("module", module))
            sync_jobs_total_series.append((labels, float(count)))
            if status in ("pending", "running"):
                sync_jobs_inflight_series.append((labels, float(count)))
    return [
        counter(
            "labdog_sync_jobs_total",
            "Total SyncJob rows ever created, by terminal/non-terminal status and module. "
            "All-time (no time predicate) — resets only on host deletion (CASCADE), "
            "future retention, or a DB restore.",
            sync_jobs_total_series,
        ),
        gauge(
            "labdog_sync_jobs_inflight",
            "Current SyncJob queue depth, by status and module.",
            sync_jobs_inflight_series,
        ),
        gauge(
            "labdog_stale_sync_jobs",
            f"SyncJob rows stuck in 'running' for more than "
            f"{STALE_THRESHOLD_MINUTES} minutes (about to be swept).",
            [((), float(stale_ops.stale_sync_jobs))],
        ),
        gauge(
            "labdog_hosts_queue_blocked",
            "Distinct hosts currently blocked behind another in-flight "
            "sync/action on the same host.",
            [((), float(stale_ops.hosts_queue_blocked))],
        ),
    ]


def _drift_families(
    drift_counts: list[tuple[str, str, int]],
    drift_change_sums: list[tuple[str, int, int, int]],
) -> list[MetricFamily]:
    drift_changes_series: list[tuple[Labels, float]] = []
    for module, add_sum, remove_sum, policy_sum in sorted(drift_change_sums):
        drift_changes_series.append((_labels(("module", module), ("kind", "add")), float(add_sum)))
        drift_changes_series.append(
            (_labels(("module", module), ("kind", "remove")), float(remove_sum))
        )
        drift_changes_series.append(
            (_labels(("module", module), ("kind", "policy_change")), float(policy_sum))
        )
    return [
        counter(
            "labdog_drift_checks_total",
            "Total drift checks ever performed, by module and result. All-time — "
            "resets only on host deletion (CASCADE), future retention, or a DB restore.",
            [
                (_labels(("module", module), ("result", status)), float(count))
                for module, status, count in sorted(drift_counts)
            ],
        ),
        counter(
            "labdog_drift_changes_total",
            "Total drift changes ever observed, by module and kind (add/remove/"
            "policy_change). All-time — same reset conditions as labdog_drift_checks_total.",
            drift_changes_series,
        ),
    ]


def _action_run_families(
    action_run_counts: list[tuple[str, str, int]],
    action_run_origin_counts: list[tuple[str, int]],
    action_host_run_counts: list[tuple[str, int]],
    simple: _SimpleCounts,
) -> list[MetricFamily]:
    include_action_key = settings.metrics.action_key_label
    action_runs_total_series, inflight_by_status = _action_run_series(
        action_run_counts, include_action_key
    )
    return [
        counter(
            "labdog_action_runs_total",
            "Total ActionRun rows ever created, by status"
            + (
                ", and action_key (settings.metrics.action_key_label=true)"
                if include_action_key
                else ""
            )
            + ". All-time — resets only on host deletion (CASCADE via host_id=NULL doesn't "
            "delete the run, so in practice this only resets on retention or a DB restore.",
            action_runs_total_series,
        ),
        gauge(
            "labdog_action_runs_inflight",
            "Current ActionRun queue depth, by status (queued/pending/running).",
            [
                (_labels(("status", status)), float(count))
                for status, count in sorted(inflight_by_status.items())
            ],
        ),
        counter(
            "labdog_action_runs_scheduled_total",
            "Total ActionRun rows dispatched via a ScheduledAction, by origin "
            "('cron' = the 60s scheduler tick, 'manual' = POST run-now).",
            [
                (_labels(("origin", origin)), float(count))
                for origin, count in sorted(action_run_origin_counts)
            ],
        ),
        counter(
            "labdog_action_host_runs_total",
            "Total ActionHostRun rows ever created, by status. All-time — resets "
            "only on host deletion (CASCADE), future retention, or a DB restore.",
            [
                (_labels(("status", status)), float(count))
                for status, count in sorted(action_host_run_counts)
            ],
        ),
        counter(
            "labdog_action_host_runs_nonzero_exit_total",
            "Total ActionHostRun rows that finished with a nonzero exit code. "
            "All-time — same reset conditions as labdog_action_host_runs_total.",
            [((), float(simple.single("action_host_runs_nonzero_exit_total")))],
        ),
    ]


def _action_run_series(
    action_run_counts: list[tuple[str, str, int]],
    include_action_key: bool,
) -> tuple[list[tuple[Labels, float]], dict[str, int]]:
    """Return the ``labdog_action_runs_total`` series and the in-flight count per status.

    The series is labelled by action key and status when
    ``include_action_key`` is set, and collapsed to status alone otherwise.
    """
    action_runs_total_series: list[tuple[Labels, float]] = []
    inflight_by_status: dict[str, int] = {}
    collapsed_by_status: dict[str, int] = {}
    for action_key, status, count in action_run_counts:
        if status in _INFLIGHT_ACTION_STATUSES:
            inflight_by_status[status] = inflight_by_status.get(status, 0) + count
        if include_action_key:
            action_runs_total_series.append(
                (_labels(("action_key", action_key), ("status", status)), float(count))
            )
        else:
            collapsed_by_status[status] = collapsed_by_status.get(status, 0) + count
    if not include_action_key:
        action_runs_total_series = [
            (_labels(("status", status)), float(count))
            for status, count in sorted(collapsed_by_status.items())
        ]
    return action_runs_total_series, inflight_by_status


def _scheduling_and_pack_families(
    scheduled_action_state: list[tuple[str, str, int]],
    stale_ops: agg.StaleOperationCounts,
    max_ages: agg.MaxAges,
    simple: _SimpleCounts,
) -> list[MetricFamily]:
    families = [
        gauge(
            "labdog_scheduled_actions",
            "Number of ScheduledAction rows, by target kind and enabled state.",
            [
                (_labels(("target_kind", tk), ("state", state)), float(count))
                for tk, state, count in sorted(scheduled_action_state)
            ],
        ),
        gauge(
            "labdog_scheduled_actions_orphaned",
            "Enabled ScheduledAction rows whose action_key has no "
            "action_registry_snapshot row (the pack that provided it was "
            "removed or disabled).",
            [((), float(stale_ops.scheduled_actions_orphaned))],
        ),
    ]
    if max_ages.scheduled_action_last_dispatch_age_seconds is not None:
        families.append(
            gauge(
                "labdog_scheduled_action_last_dispatch_age_seconds_max",
                "Seconds since the most recently dispatched enabled schedule last dispatched.",
                [((), float(max_ages.scheduled_action_last_dispatch_age_seconds))],
            )
        )
    action_packs = simple.grouped("action_packs")
    families.append(
        gauge(
            "labdog_action_packs",
            "Number of action packs, by last-sync status ('never' if never synced).",
            [(_labels(("sync_status", v)), float(c)) for v, c in sorted(action_packs.items())],
        )
    )
    if max_ages.action_pack_last_sync_age_seconds is not None:
        families.append(
            gauge(
                "labdog_action_pack_last_sync_age_seconds_max",
                "Seconds since the most recently synced action pack last synced.",
                [((), float(max_ages.action_pack_last_sync_age_seconds))],
            )
        )
    families.append(
        gauge(
            "labdog_action_registry_keys",
            "Number of action keys with a resolved winner in the action registry.",
            [((), float(simple.single("action_registry_keys")))],
        )
    )
    if max_ages.action_registry_computed_at_timestamp_seconds is not None:
        families.append(
            gauge(
                "labdog_action_registry_computed_timestamp_seconds",
                "Unix timestamp of the most recent action-registry rebuild.",
                [((), float(max_ages.action_registry_computed_at_timestamp_seconds))],
            )
        )
    action_resolutions = simple.grouped("action_resolutions")
    families.append(
        gauge(
            "labdog_action_resolutions",
            "Number of action-key resolutions, by origin "
            "('user' = operator-picked, 'frozen' = auto-pinned on fresh conflict).",
            [(_labels(("origin", v)), float(c)) for v, c in sorted(action_resolutions.items())],
        )
    )
    return families


def _histogram_families(
    sync_duration_hist: agg.HistogramCounts,
    drift_duration_hist: list[tuple[str, agg.HistogramCounts]],
) -> list[MetricFamily]:
    return [
        histogram(
            "labdog_sync_job_duration_seconds",
            "SyncJob wall-clock duration (completed_at - started_at), for jobs "
            "with both timestamps set and completed_at >= started_at.",
            bounds=_BUCKETS_SYNC,
            series=[
                (
                    (),
                    sync_duration_hist.bucket_counts,
                    sync_duration_hist.total_count,
                    sync_duration_hist.total_sum,
                )
            ],
        ),
        histogram(
            "labdog_drift_check_duration_seconds",
            "Drift-check wall-clock duration, by module. duration_ms is nullable "
            "and NULL rows are excluded entirely, so _count is NOT the drift-check "
            "count for that module — use labdog_drift_checks_total for that.",
            bounds=_BUCKETS_DRIFT,
            series=[
                (_labels(("module", module)), hist.bucket_counts, hist.total_count, hist.total_sum)
                for module, hist in sorted(drift_duration_hist, key=lambda item: item[0])
            ],
        ),
    ]


async def get_snapshot(db: AsyncSession) -> Snapshot:
    """Return the cached snapshot, refreshing it if stale.

    Single-flight: the first caller past the TTL takes ``_cache_lock`` and
    collects; every other concurrent caller either sees the fresh cache
    before taking the lock, or blocks on the lock and then sees the fresh
    cache the winner just wrote — either way, at most one ``collect()``
    call happens per TTL window regardless of how many requests arrive
    concurrently.
    """
    global _cache, _scrape_errors_total

    now = datetime.now(UTC)
    ttl = settings.metrics.cache_ttl_seconds
    cached = _cache
    if cached is not None and (now - cached.collected_at).total_seconds() < ttl:
        return cached

    async with _cache_lock:
        now = datetime.now(UTC)
        cached = _cache
        if cached is not None and (now - cached.collected_at).total_seconds() < ttl:
            return cached

        started = time.monotonic()
        try:
            families = await collect(db)
        except Exception:
            _scrape_errors_total += 1
            raise
        duration = time.monotonic() - started

        snapshot = Snapshot(
            families=tuple(families),
            collected_at=datetime.now(UTC),
            collect_duration_seconds=duration,
        )
        _cache = snapshot
        return snapshot


def with_meta(snapshot: Snapshot) -> list[MetricFamily]:
    """Append the three exporter self-metrics after the cached families,
    computed fresh for *this* request — ``cache_age`` in particular must
    reflect "now", not the moment the snapshot was collected, even when
    the snapshot itself is served straight from cache."""
    cache_age = (datetime.now(UTC) - snapshot.collected_at).total_seconds()
    return [
        *snapshot.families,
        gauge(
            "labdog_metrics_scrape_duration_seconds",
            "Wall-clock duration of the most recent underlying metrics collection "
            "(not this HTTP request — cached responses don't re-collect).",
            [((), float(snapshot.collect_duration_seconds))],
        ),
        gauge(
            "labdog_metrics_cache_age_seconds",
            "Seconds since the cached metrics snapshot was collected.",
            [((), cache_age)],
        ),
        counter(
            "labdog_metrics_scrape_errors_total",
            "Total failed metrics collection attempts. The one process-local "
            "counter in this exporter — every other counter is DB-derived and "
            "therefore identical across workers.",
            [((), float(_scrape_errors_total))],
        ),
    ]
