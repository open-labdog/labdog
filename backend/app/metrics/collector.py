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
    families: list[MetricFamily] = []

    simple = await agg.get_simple_counts(db)
    rule_counts = await agg.get_desired_state_rule_counts(db)
    max_ages = await agg.get_max_ages(db)
    host_module_counts = await agg.get_host_module_counts(db)
    sync_job_counts = await agg.get_sync_job_counts(db)
    drift_counts = await agg.get_drift_counts(db)
    drift_change_sums = await agg.get_drift_change_sums(db)
    action_run_counts = await agg.get_action_run_counts(db)
    action_run_origin_counts = await agg.get_action_run_origin_counts(db)
    action_host_run_counts = await agg.get_action_host_run_counts(db)
    scheduled_action_state = await agg.get_scheduled_action_state(db)
    stale_ops = await agg.get_stale_operation_counts(db)
    ca_cert_expiries = await agg.get_ca_cert_expiries(db)
    sync_duration_hist = await agg.get_sync_duration_histogram(db)
    drift_duration_hist = await agg.get_drift_duration_histogram(db)

    # -- build_info ---------------------------------------------------
    from app.api.version import _BUILD_DATE, _COMMIT_SHA, _VERSION

    families.append(
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
    )

    # -- simple counts, split back out by family ----------------------
    by_family: dict[str, list[tuple[str, int]]] = {}
    for family, label, value in simple:
        by_family.setdefault(family, []).append((label, value))

    def _single(family: str) -> int:
        rows = by_family.get(family, [])
        return rows[0][1] if rows else 0

    def _grouped(family: str) -> dict[str, int]:
        return dict(by_family.get(family, []))

    hosts_by_status = _grouped("hosts_sync_status")
    hosts_status_series = [
        (_labels(("sync_status", v)), float(hosts_by_status.get(v, 0))) for v in _SYNC_STATUS_VALUES
    ]
    families.append(
        gauge(
            "labdog_hosts",
            "Number of hosts, by sync status.",
            hosts_status_series,
        )
    )

    hosts_by_backend = _grouped("hosts_firewall_backend")
    families.append(
        gauge(
            "labdog_hosts_firewall_backend",
            "Number of hosts, by firewall backend (observed values only).",
            [(_labels(("backend", v)), float(c)) for v, c in sorted(hosts_by_backend.items())],
        )
    )

    families.append(
        gauge(
            "labdog_hosts_drift_check_enabled",
            "Number of hosts with drift checking enabled.",
            [((), float(_single("hosts_drift_check_enabled")))],
        )
    )
    families.append(
        gauge(
            "labdog_hosts_never_synced",
            "Number of hosts that have never completed a sync.",
            [((), float(_single("hosts_never_synced")))],
        )
    )
    families.append(
        gauge(
            "labdog_hosts_never_drift_checked",
            "Number of hosts that have never had a drift check.",
            [((), float(_single("hosts_never_drift_checked")))],
        )
    )

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

    # -- host modules ---------------------------------------------------
    families.append(
        gauge(
            "labdog_host_modules",
            "Number of per-host module status rows, by module and sync status "
            "(both observed values — free text, not enums).",
            [
                (_labels(("module", module), ("sync_status", status)), float(count))
                for module, status, count in sorted(host_module_counts)
            ],
        )
    )

    # -- desired-state rule counts ---------------------------------------
    families.append(
        gauge(
            "labdog_module_rules",
            "Number of desired-state rule rows (host- + group-scoped), by module.",
            [(_labels(("module", module)), float(count)) for module, count in sorted(rule_counts)],
        )
    )

    # -- groups -----------------------------------------------------------
    families.append(
        gauge(
            "labdog_groups",
            "Total number of host groups.",
            [((), float(_single("groups")))],
        )
    )
    groups_gitops = _grouped("groups_gitops")
    families.append(
        gauge(
            "labdog_groups_gitops",
            "Number of groups, by GitOps status.",
            [
                (_labels(("status", v)), float(groups_gitops.get(v, 0)))
                for v in _GITOPS_STATUS_VALUES
            ],
        )
    )
    if max_ages.group_gitops_last_import_age_seconds is not None:
        families.append(
            gauge(
                "labdog_group_gitops_last_import_age_seconds_max",
                "Seconds since the most recently GitOps-imported group last imported.",
                [((), float(max_ages.group_gitops_last_import_age_seconds))],
            )
        )

    # -- CA cert expiry ------------------------------------------------
    families.append(
        gauge(
            "labdog_ca_cert_not_after_timestamp_seconds",
            "CA certificate expiry (notAfter), as a Unix timestamp.",
            [
                (
                    _labels(("name", name), ("fingerprint", fingerprint)),
                    not_after.timestamp(),
                )
                for name, fingerprint, not_after in sorted(ca_cert_expiries)
            ],
        )
    )

    # -- discovery / scans --------------------------------------------
    families.append(
        gauge(
            "labdog_scan_pending_hosts",
            "Number of discovered hosts awaiting review (not yet added).",
            [((), float(_single("scan_pending_hosts")))],
        )
    )
    scan_configs = _grouped("scan_configs")
    families.append(
        gauge(
            "labdog_scan_configs",
            "Number of scan configs, by enabled state.",
            [(_labels(("state", v)), float(c)) for v, c in sorted(scan_configs.items())],
        )
    )
    scan_config_last_run = _grouped("scan_config_last_run")
    families.append(
        gauge(
            "labdog_scan_config_last_run",
            "Number of scan configs, by last-run status ('never' if the config has not run yet).",
            [(_labels(("status", v)), float(c)) for v, c in sorted(scan_config_last_run.items())],
        )
    )

    # -- users -----------------------------------------------------------
    users_by_state = _grouped("users")
    families.append(
        gauge(
            "labdog_users",
            "Number of user accounts, by active state.",
            [(_labels(("state", v)), float(c)) for v, c in sorted(users_by_state.items())],
        )
    )
    families.append(
        gauge(
            "labdog_superusers",
            "Number of superuser accounts.",
            [((), float(_single("superusers")))],
        )
    )

    # -- git / grafana / proxmox integrations -----------------------------
    git_repos = _grouped("git_repositories")
    families.append(
        gauge(
            "labdog_git_repositories",
            "Number of configured git repositories, by auth type.",
            [
                (_labels(("auth_type", v)), float(git_repos.get(v, 0)))
                for v in _GIT_AUTH_TYPE_VALUES
            ],
        )
    )
    grafana_instances = _grouped("grafana_instances")
    families.append(
        gauge(
            "labdog_grafana_instances",
            "Number of registered Grafana-stack instances, by kind.",
            [(_labels(("kind", v)), float(c)) for v, c in sorted(grafana_instances.items())],
        )
    )
    families.append(
        gauge(
            "labdog_vm_mappings",
            "Number of hosts mapped to a Proxmox VM identity.",
            [((), float(_single("vm_mappings")))],
        )
    )

    # -- sync jobs ---------------------------------------------------------
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
    families.append(
        counter(
            "labdog_sync_jobs_total",
            "Total SyncJob rows ever created, by terminal/non-terminal status and module. "
            "All-time (no time predicate) — resets only on host deletion (CASCADE), "
            "future retention, or a DB restore.",
            sync_jobs_total_series,
        )
    )
    families.append(
        gauge(
            "labdog_sync_jobs_inflight",
            "Current SyncJob queue depth, by status and module.",
            sync_jobs_inflight_series,
        )
    )
    families.append(
        gauge(
            "labdog_stale_sync_jobs",
            f"SyncJob rows stuck in 'running' for more than "
            f"{STALE_THRESHOLD_MINUTES} minutes (about to be swept).",
            [((), float(stale_ops.stale_sync_jobs))],
        )
    )
    families.append(
        gauge(
            "labdog_hosts_queue_blocked",
            "Distinct hosts currently blocked behind another in-flight "
            "sync/action on the same host.",
            [((), float(stale_ops.hosts_queue_blocked))],
        )
    )

    # -- drift ---------------------------------------------------------
    families.append(
        counter(
            "labdog_drift_checks_total",
            "Total drift checks ever performed, by module and result. All-time — "
            "resets only on host deletion (CASCADE), future retention, or a DB restore.",
            [
                (_labels(("module", module), ("result", status)), float(count))
                for module, status, count in sorted(drift_counts)
            ],
        )
    )
    drift_changes_series: list[tuple[Labels, float]] = []
    for module, add_sum, remove_sum, policy_sum in sorted(drift_change_sums):
        drift_changes_series.append((_labels(("module", module), ("kind", "add")), float(add_sum)))
        drift_changes_series.append(
            (_labels(("module", module), ("kind", "remove")), float(remove_sum))
        )
        drift_changes_series.append(
            (_labels(("module", module), ("kind", "policy_change")), float(policy_sum))
        )
    families.append(
        counter(
            "labdog_drift_changes_total",
            "Total drift changes ever observed, by module and kind (add/remove/"
            "policy_change). All-time — same reset conditions as labdog_drift_checks_total.",
            drift_changes_series,
        )
    )

    # -- action runs ---------------------------------------------------
    include_action_key = settings.metrics.action_key_label
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
    families.append(
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
        )
    )
    families.append(
        gauge(
            "labdog_action_runs_inflight",
            "Current ActionRun queue depth, by status (queued/pending/running).",
            [
                (_labels(("status", status)), float(count))
                for status, count in sorted(inflight_by_status.items())
            ],
        )
    )
    families.append(
        counter(
            "labdog_action_runs_scheduled_total",
            "Total ActionRun rows dispatched via a ScheduledAction, by origin "
            "('cron' = the 60s scheduler tick, 'manual' = POST run-now).",
            [
                (_labels(("origin", origin)), float(count))
                for origin, count in sorted(action_run_origin_counts)
            ],
        )
    )
    families.append(
        counter(
            "labdog_action_host_runs_total",
            "Total ActionHostRun rows ever created, by status. All-time — resets "
            "only on host deletion (CASCADE), future retention, or a DB restore.",
            [
                (_labels(("status", status)), float(count))
                for status, count in sorted(action_host_run_counts)
            ],
        )
    )
    families.append(
        counter(
            "labdog_action_host_runs_nonzero_exit_total",
            "Total ActionHostRun rows that finished with a nonzero exit code. "
            "All-time — same reset conditions as labdog_action_host_runs_total.",
            [((), float(_single("action_host_runs_nonzero_exit_total")))],
        )
    )

    # -- scheduling & packs ---------------------------------------------
    families.append(
        gauge(
            "labdog_scheduled_actions",
            "Number of ScheduledAction rows, by target kind and enabled state.",
            [
                (_labels(("target_kind", tk), ("state", state)), float(count))
                for tk, state, count in sorted(scheduled_action_state)
            ],
        )
    )
    families.append(
        gauge(
            "labdog_scheduled_actions_orphaned",
            "Enabled ScheduledAction rows whose action_key has no "
            "action_registry_snapshot row (the pack that provided it was "
            "removed or disabled).",
            [((), float(stale_ops.scheduled_actions_orphaned))],
        )
    )
    if max_ages.scheduled_action_last_dispatch_age_seconds is not None:
        families.append(
            gauge(
                "labdog_scheduled_action_last_dispatch_age_seconds_max",
                "Seconds since the most recently dispatched enabled schedule last dispatched.",
                [((), float(max_ages.scheduled_action_last_dispatch_age_seconds))],
            )
        )
    action_packs = _grouped("action_packs")
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
            [((), float(_single("action_registry_keys")))],
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
    action_resolutions = _grouped("action_resolutions")
    families.append(
        gauge(
            "labdog_action_resolutions",
            "Number of action-key resolutions, by origin "
            "('user' = operator-picked, 'frozen' = auto-pinned on fresh conflict).",
            [(_labels(("origin", v)), float(c)) for v, c in sorted(action_resolutions.items())],
        )
    )

    # -- histograms ------------------------------------------------------
    families.append(
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
        )
    )
    families.append(
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
        )
    )

    return families


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
