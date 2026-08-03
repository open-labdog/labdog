"""Point-in-time SQL aggregations for the Prometheus ``/metrics`` exporter.

This is the exporter's own aggregation layer — it does **not** reuse
``app.metrics.service`` (see that module's docstring for why: ``service.py``
returns ``date_trunc`` time series for the dashboard charts, Prometheus needs
current point-in-time values).

Every function takes a plain ``AsyncSession`` and returns plain tuples or a
small local dataclass — never Pydantic models, never loaded ORM entities
(``select(Model)``). That keeps every function a single, cheap, read-only
statement suitable for a collector that runs on a timer, not a page load.
Where the same shape of query repeats many times (trivial counts, the 9
desired-state rule tables) they're collapsed into one ``UNION ALL`` rather
than issuing N awaits — see ``get_simple_counts`` and
``get_desired_state_rule_counts``. Do **not** re-split those back into
one-model-per-await helpers like ``app.api.hosts._counts`` /
``app.api.groups._counts`` — those exist for a once-per-page-load list view;
this module runs on every cache-miss scrape.

``module_type`` / free-text status columns are emitted **verbatim** —
this module must never import ``CANONICAL_ORDER`` from
``app.ansible_runtime.composer`` (a different, incompatible module-name
vocabulary) and must never hard-code a module list to zero-fill against.
See ``app.metrics.collector`` for which dimensions *do* get zero-filled
(only columns actually typed as ``Enum(SyncStatus | JobStatus |
GitOpsStatus | GitAuthType)`` — everything else is observed-values-only).

Callers (``app.metrics.collector.collect``) MUST await these sequentially,
never via ``asyncio.gather`` on the same session — see the warning
repeated at the top of ``collector.collect()`` (and originally in
``app.metrics.recorder``): asyncpg raises ``InterfaceError: another
operation is in progress`` when two coroutines drive the same connection
concurrently.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import String, and_, case, cast, func, literal, select, union, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.action_run import ActionHostRun, ActionRun
from app.models.drift_sample import DriftSample
from app.models.git_repository import GitRepository
from app.models.host import Host
from app.models.host_group import HostGroup
from app.models.host_module_status import HostModuleStatus
from app.models.scan_config import PendingHost, ScanConfig
from app.models.scheduled_action import ScheduledAction
from app.models.sync_job import JobStatus, SyncJob
from app.models.user import User
from app.packs.models import ActionPack, ActionRegistrySnapshot, ActionResolution
from app.proxmox.vm_mapping import VMMapping
from app.tasks.sync_sweeper import STALE_THRESHOLD_MINUTES

# ---------------------------------------------------------------------------
# Histogram bucket bounds — single source of truth for both the SQL FILTER
# columns built below and the exposition-format buckets emitted by
# app.metrics.collector, so the two can never drift apart.
# ---------------------------------------------------------------------------

_BUCKETS_SYNC: tuple[float, ...] = (1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0, 1800.0)
_BUCKETS_DRIFT: tuple[float, ...] = (0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0)


# ---------------------------------------------------------------------------
# Small result dataclasses (never Pydantic — internal to the exporter)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MaxAges:
    """Ages (seconds, ``now() - MAX(col)``), or ``None`` when no row has a
    non-null value for that column. ``registry_computed_at_timestamp`` is an
    absolute Unix timestamp, not an age."""

    host_last_sync_age_seconds: float | None
    host_last_drift_check_age_seconds: float | None
    group_gitops_last_import_age_seconds: float | None
    scheduled_action_last_dispatch_age_seconds: float | None
    action_pack_last_sync_age_seconds: float | None
    action_registry_computed_at_timestamp_seconds: float | None


@dataclass(frozen=True, slots=True)
class StaleOperationCounts:
    stale_sync_jobs: int
    hosts_queue_blocked: int
    scheduled_actions_orphaned: int


@dataclass(frozen=True, slots=True)
class HistogramCounts:
    """Cumulative bucket counts aligned 1:1 with a bounds tuple, plus the
    overall (unlabelled) total count and sum — ready for
    ``app.metrics.exposition.histogram()``."""

    bucket_counts: list[int]
    total_count: int
    total_sum: float


# ---------------------------------------------------------------------------
# 1. Simple trivial counts — one UNION ALL for everything that reduces to
#    "count, optionally grouped by one label column".
# ---------------------------------------------------------------------------


async def get_simple_counts(db: AsyncSession) -> list[tuple[str, str, int]]:
    """Return ``(family, label, count)`` rows for every trivial gauge count.

    ``label`` is ``""`` for unlabelled families. One round trip covers:
    hosts by sync_status / firewall_backend, host boolean flags (never
    synced / never drift-checked / drift-check enabled), groups (total +
    by gitops status), users (by active state) + superusers, git
    repositories (by auth_type), grafana instances (by kind), vm mappings,
    scan configs (by enabled state + by last-run status), pending hosts,
    action packs (by last-sync status), the action registry key count,
    action resolutions (by origin: user vs. freeze-auto-pinned), and the
    one non-status-grouped action-host-run count (nonzero exit).
    """
    # Each conditional/coalesce label expression is built ONCE and reused in
    # both the SELECT list and GROUP BY — Postgres (via asyncpg) matches
    # GROUP BY membership by comparing the compiled expression tree, and two
    # separately-constructed `case()`/`coalesce()` calls compile to
    # different bind-parameter placeholders even though they're textually
    # identical, which asyncpg rejects with "must appear in the GROUP BY
    # clause or be used in an aggregate function".
    users_state = case((User.is_active.is_(True), "active"), else_="inactive")
    scan_config_state = case((ScanConfig.enabled.is_(True), "enabled"), else_="disabled")
    scan_config_last_run_status = func.coalesce(ScanConfig.last_run_status, "never")
    action_pack_last_sync_status = func.coalesce(ActionPack.last_sync_status, "never")
    resolution_origin = case(
        (ActionResolution.decided_by_user_id.is_(None), "frozen"), else_="user"
    )

    members = [
        select(
            literal("hosts_sync_status").label("family"),
            cast(Host.sync_status, String).label("label"),
            func.count().label("value"),
        ).group_by(Host.sync_status),
        select(
            literal("hosts_firewall_backend").label("family"),
            cast(Host.firewall_backend, String).label("label"),
            func.count().label("value"),
        ).group_by(Host.firewall_backend),
        select(
            literal("hosts_drift_check_enabled").label("family"),
            literal("").label("label"),
            func.count().label("value"),
        ).where(Host.drift_check_enabled.is_(True)),
        select(
            literal("hosts_never_synced").label("family"),
            literal("").label("label"),
            func.count().label("value"),
        ).where(Host.last_sync_at.is_(None)),
        select(
            literal("hosts_never_drift_checked").label("family"),
            literal("").label("label"),
            func.count().label("value"),
        ).where(Host.last_drift_check_at.is_(None)),
        select(
            literal("groups").label("family"),
            literal("").label("label"),
            func.count().label("value"),
        ).select_from(HostGroup),
        select(
            literal("groups_gitops").label("family"),
            cast(HostGroup.gitops_status, String).label("label"),
            func.count().label("value"),
        ).group_by(HostGroup.gitops_status),
        select(
            literal("users").label("family"),
            users_state.label("label"),
            func.count().label("value"),
        ).group_by(users_state),
        select(
            literal("superusers").label("family"),
            literal("").label("label"),
            func.count().label("value"),
        ).where(User.is_superuser.is_(True)),
        select(
            literal("git_repositories").label("family"),
            cast(GitRepository.auth_type, String).label("label"),
            func.count().label("value"),
        ).group_by(GitRepository.auth_type),
        select(
            literal("scan_pending_hosts").label("family"),
            literal("").label("label"),
            func.count().label("value"),
        ).select_from(PendingHost),
        select(
            literal("scan_configs").label("family"),
            scan_config_state.label("label"),
            func.count().label("value"),
        ).group_by(scan_config_state),
        select(
            literal("scan_config_last_run").label("family"),
            scan_config_last_run_status.label("label"),
            func.count().label("value"),
        ).group_by(scan_config_last_run_status),
        select(
            literal("action_packs").label("family"),
            action_pack_last_sync_status.label("label"),
            func.count().label("value"),
        ).group_by(action_pack_last_sync_status),
        select(
            literal("action_registry_keys").label("family"),
            literal("").label("label"),
            func.count().label("value"),
        ).select_from(ActionRegistrySnapshot),
        select(
            literal("action_resolutions").label("family"),
            resolution_origin.label("label"),
            func.count().label("value"),
        ).group_by(resolution_origin),
        select(
            literal("action_host_runs_nonzero_exit_total").label("family"),
            literal("").label("label"),
            func.count().label("value"),
        ).where(ActionHostRun.exit_code.isnot(None), ActionHostRun.exit_code != 0),
    ]

    # Grafana instances and VM mappings live in external-module tables that
    # are lazy-loaded to avoid circular imports at package init (see
    # app.models.__init__.import_all_models) — imported locally, mirroring
    # app.api.hosts.list_hosts_summary / app.api.groups.list_groups_summary.
    from app.grafana.models import GrafanaInstance

    members.append(
        select(
            literal("grafana_instances").label("family"),
            GrafanaInstance.kind.label("label"),
            func.count().label("value"),
        ).group_by(GrafanaInstance.kind)
    )
    members.append(
        select(
            literal("vm_mappings").label("family"),
            literal("").label("label"),
            func.count().label("value"),
        ).select_from(VMMapping)
    )

    result = await db.execute(union_all(*members))
    return [(row.family, row.label, row.value) for row in result.all()]


# ---------------------------------------------------------------------------
# 2. Desired-state rule counts — the 9 per-module rule tables, one UNION ALL.
# ---------------------------------------------------------------------------


async def get_desired_state_rule_counts(db: AsyncSession) -> list[tuple[str, int]]:
    """Return ``(module, count)`` — total row count (host- + group-scoped)
    for each of the 9 desired-state rule tables.

    ``module`` here is a fixed label LabDog assigns per rule *table*, not a
    DB column value — unlike ``module_type`` columns (free text, emitted
    verbatim elsewhere in this module), there is one rule table per module
    so the mapping is 1:1 and static. Deliberately **not** the
    9-sequential-await ``_counts()`` shape from ``app.api.hosts`` /
    ``app.api.groups`` (those run once per page load; this runs on a timer).
    """
    from app.ca_certs.models import CACertRule
    from app.cron.models import CronJob
    from app.hosts_mgmt.models import HostsEntry
    from app.models.firewall_rule import FirewallRule
    from app.packages.models import PackageRule
    from app.resolver.models import ResolverConfig
    from app.services.models import ServiceRule
    from app.user_mgmt.models import LinuxGroup, LinuxUser

    model_labels: list[tuple[str, type]] = [
        ("firewall", FirewallRule),
        ("hosts_file", HostsEntry),
        ("service", ServiceRule),
        ("linux_user", LinuxUser),
        ("linux_group", LinuxGroup),
        ("cron", CronJob),
        ("package", PackageRule),
        ("resolver", ResolverConfig),
        ("ca_cert", CACertRule),
    ]
    members = [
        select(literal(label).label("module"), func.count().label("value")).select_from(model)
        for label, model in model_labels
    ]
    result = await db.execute(union_all(*members))
    return [(row.module, row.value) for row in result.all()]


# ---------------------------------------------------------------------------
# 3. Max-age gauges — one row, six scalar subqueries.
# ---------------------------------------------------------------------------


async def get_max_ages(db: AsyncSession) -> MaxAges:
    host_sync_age = select(
        func.extract("epoch", func.now() - func.max(Host.last_sync_at))
    ).scalar_subquery()
    host_drift_age = select(
        func.extract("epoch", func.now() - func.max(Host.last_drift_check_at))
    ).scalar_subquery()
    group_gitops_age = select(
        func.extract("epoch", func.now() - func.max(HostGroup.gitops_last_import_at))
    ).scalar_subquery()
    scheduled_dispatch_age = (
        select(func.extract("epoch", func.now() - func.max(ScheduledAction.last_dispatched_at)))
        .where(ScheduledAction.enabled.is_(True))
        .scalar_subquery()
    )
    pack_sync_age = select(
        func.extract("epoch", func.now() - func.max(ActionPack.last_synced_at))
    ).scalar_subquery()
    registry_computed_ts = select(
        func.extract("epoch", func.max(ActionRegistrySnapshot.computed_at))
    ).scalar_subquery()

    stmt = select(
        host_sync_age.label("host_last_sync_age_seconds"),
        host_drift_age.label("host_last_drift_check_age_seconds"),
        group_gitops_age.label("group_gitops_last_import_age_seconds"),
        scheduled_dispatch_age.label("scheduled_action_last_dispatch_age_seconds"),
        pack_sync_age.label("action_pack_last_sync_age_seconds"),
        registry_computed_ts.label("action_registry_computed_at_timestamp_seconds"),
    )
    row = (await db.execute(stmt)).one()
    return MaxAges(
        host_last_sync_age_seconds=row.host_last_sync_age_seconds,
        host_last_drift_check_age_seconds=row.host_last_drift_check_age_seconds,
        group_gitops_last_import_age_seconds=row.group_gitops_last_import_age_seconds,
        scheduled_action_last_dispatch_age_seconds=row.scheduled_action_last_dispatch_age_seconds,
        action_pack_last_sync_age_seconds=row.action_pack_last_sync_age_seconds,
        action_registry_computed_at_timestamp_seconds=(
            row.action_registry_computed_at_timestamp_seconds
        ),
    )


# ---------------------------------------------------------------------------
# 4. Host module status — module_type × sync_status, both free text.
# ---------------------------------------------------------------------------


async def get_host_module_counts(db: AsyncSession) -> list[tuple[str, str, int]]:
    """Return ``(module_type, sync_status, count)`` from ``host_module_status``.

    Both columns are ``String(20)`` free text (not SQLAlchemy enums) — emit
    observed values verbatim, no zero-fill.
    """
    stmt = select(
        HostModuleStatus.module_type,
        HostModuleStatus.sync_status,
        func.count().label("value"),
    ).group_by(HostModuleStatus.module_type, HostModuleStatus.sync_status)
    result = await db.execute(stmt)
    return [(row.module_type, row.sync_status, row.value) for row in result.all()]


# ---------------------------------------------------------------------------
# 5. Sync jobs — status (real Enum(JobStatus)) × module_type (free text).
# ---------------------------------------------------------------------------


async def get_sync_job_counts(db: AsyncSession) -> list[tuple[str, str, int]]:
    """Return ``(status, module_type, count)`` — ALL-TIME, no time filter.

    Serves both ``labdog_sync_jobs_total`` (every row) and
    ``labdog_sync_jobs_inflight`` (the collector filters this same result
    to pending/running) — ``SyncJob`` rows are mutated in place as a job
    progresses rather than appended per transition, so "count of rows
    currently status='running'" *is* the current in-flight count.
    """
    stmt = select(
        cast(SyncJob.status, String).label("status"),
        SyncJob.module_type,
        func.count().label("value"),
    ).group_by(SyncJob.status, SyncJob.module_type)
    result = await db.execute(stmt)
    return [(row.status, row.module_type, row.value) for row in result.all()]


# ---------------------------------------------------------------------------
# 6. Drift checks — module_type × result, both free text.
# ---------------------------------------------------------------------------


async def get_drift_counts(db: AsyncSession) -> list[tuple[str, str, int]]:
    """Return ``(module_type, status, count)`` from ``drift_samples`` —
    ALL-TIME, no time filter. Feeds ``labdog_drift_checks_total``."""
    stmt = select(
        DriftSample.module_type,
        DriftSample.status,
        func.count().label("value"),
    ).group_by(DriftSample.module_type, DriftSample.status)
    result = await db.execute(stmt)
    return [(row.module_type, row.status, row.value) for row in result.all()]


async def get_drift_change_sums(db: AsyncSession) -> list[tuple[str, int, int, int]]:
    """Return ``(module_type, add_sum, remove_sum, policy_change_sum)`` —
    the collector unpivots each row into 3 ``kind``-labelled counter
    series for ``labdog_drift_changes_total``."""
    stmt = select(
        DriftSample.module_type,
        func.coalesce(func.sum(DriftSample.add_count), 0).label("add_sum"),
        func.coalesce(func.sum(DriftSample.remove_count), 0).label("remove_sum"),
        func.coalesce(func.sum(DriftSample.policy_change_count), 0).label("policy_change_sum"),
    ).group_by(DriftSample.module_type)
    result = await db.execute(stmt)
    return [
        (row.module_type, row.add_sum, row.remove_sum, row.policy_change_sum)
        for row in result.all()
    ]


# ---------------------------------------------------------------------------
# 7. Action runs.
# ---------------------------------------------------------------------------


async def get_action_run_counts(db: AsyncSession) -> list[tuple[str, str, int]]:
    """Return ``(action_key, status, count)`` — ALL-TIME. Feeds both
    ``labdog_action_runs_total`` and (summed across action_key by the
    collector, for statuses queued/pending/running)
    ``labdog_action_runs_inflight``."""
    stmt = select(
        ActionRun.action_key,
        ActionRun.status,
        func.count().label("value"),
    ).group_by(ActionRun.action_key, ActionRun.status)
    result = await db.execute(stmt)
    return [(row.action_key, row.status, row.value) for row in result.all()]


async def get_action_run_origin_counts(db: AsyncSession) -> list[tuple[str, int]]:
    """Return ``(origin, count)`` for scheduler-dispatched action runs
    (``scheduled_action_id IS NOT NULL``). ``origin`` is ``"cron"`` when
    ``triggered_by_user_id IS NULL`` (the unified scheduler's tick — see
    ``app.tasks.scheduled_action_schedule``) or ``"manual"`` when it's set
    (``POST /api/scheduled-actions/{id}/run-now``, which stamps the calling
    user). Feeds ``labdog_action_runs_scheduled_total``."""
    origin_expr = case((ActionRun.triggered_by_user_id.is_(None), "cron"), else_="manual")
    stmt = (
        select(origin_expr.label("origin"), func.count().label("value"))
        .where(ActionRun.scheduled_action_id.isnot(None))
        .group_by(origin_expr)
    )
    result = await db.execute(stmt)
    return [(row.origin, row.value) for row in result.all()]


async def get_action_host_run_counts(db: AsyncSession) -> list[tuple[str, int]]:
    """Return ``(status, count)`` from ``action_host_runs`` — ALL-TIME.
    Feeds ``labdog_action_host_runs_total``. The nonzero-exit counter is
    folded into ``get_simple_counts`` (a single unrelated predicate, not a
    grouping)."""
    stmt = select(ActionHostRun.status, func.count().label("value")).group_by(ActionHostRun.status)
    result = await db.execute(stmt)
    return [(row.status, row.value) for row in result.all()]


# ---------------------------------------------------------------------------
# 8. Scheduled actions.
# ---------------------------------------------------------------------------


async def get_scheduled_action_state(db: AsyncSession) -> list[tuple[str, str, int]]:
    """Return ``(target_kind, state, count)`` from ``scheduled_actions``.
    ``state`` is ``"enabled"``/``"disabled"`` derived from the boolean
    ``enabled`` column (not one of the 4 zero-filled enums — observed
    combinations only)."""
    state_expr = case((ScheduledAction.enabled.is_(True), "enabled"), else_="disabled")
    stmt = select(
        ScheduledAction.target_kind,
        state_expr.label("state"),
        func.count().label("value"),
    ).group_by(ScheduledAction.target_kind, state_expr)
    result = await db.execute(stmt)
    return [(row.target_kind, row.state, row.value) for row in result.all()]


# ---------------------------------------------------------------------------
# 9. Stale-operation counts — stuck syncs, blocked host queue, orphaned
#    schedules. One SELECT, three scalar subqueries.
# ---------------------------------------------------------------------------


async def get_stale_operation_counts(db: AsyncSession) -> StaleOperationCounts:
    cutoff = datetime.now(UTC) - timedelta(minutes=STALE_THRESHOLD_MINUTES)

    stale_sync_jobs_sq = (
        select(func.count())
        .select_from(SyncJob)
        .where(
            SyncJob.status == JobStatus.running,
            SyncJob.started_at.isnot(None),
            SyncJob.started_at < cutoff,
        )
        .scalar_subquery()
    )

    # "Blocked" spans every queue in the shared per-host serialisation
    # system (app.tasks.host_lock): a sync, a host-targeted action run, or
    # a group-dispatched action's per-host row can each independently defer
    # a host to 'pending'. UNION (not UNION ALL) de-dupes hosts blocked in
    # more than one queue at once.
    pending_sync_hosts = select(SyncJob.host_id).where(SyncJob.status == JobStatus.pending)
    pending_action_hosts = select(ActionRun.host_id).where(
        ActionRun.status == "pending", ActionRun.host_id.isnot(None)
    )
    pending_action_host_run_hosts = select(ActionHostRun.host_id).where(
        ActionHostRun.status == "pending"
    )
    blocked_hosts = union(
        pending_sync_hosts, pending_action_hosts, pending_action_host_run_hosts
    ).subquery()
    hosts_queue_blocked_sq = select(func.count()).select_from(blocked_hosts).scalar_subquery()

    orphaned_sq = (
        select(func.count())
        .select_from(ScheduledAction)
        .where(
            ScheduledAction.enabled.is_(True),
            ~ScheduledAction.action_key.in_(select(ActionRegistrySnapshot.action_key)),
        )
        .scalar_subquery()
    )

    stmt = select(
        stale_sync_jobs_sq.label("stale_sync_jobs"),
        hosts_queue_blocked_sq.label("hosts_queue_blocked"),
        orphaned_sq.label("scheduled_actions_orphaned"),
    )
    row = (await db.execute(stmt)).one()
    return StaleOperationCounts(
        stale_sync_jobs=row.stale_sync_jobs,
        hosts_queue_blocked=row.hosts_queue_blocked,
        scheduled_actions_orphaned=row.scheduled_actions_orphaned,
    )


# ---------------------------------------------------------------------------
# 10. CA certificate expiries.
# ---------------------------------------------------------------------------


async def get_ca_cert_expiries(db: AsyncSession) -> list[tuple[str, str, datetime]]:
    """Return distinct ``(name, fingerprint_sha256, not_after)`` for every
    present CA cert rule with a known expiry. ``DISTINCT`` collapses the
    (rare, but possible) case of the identical cert assigned to more than
    one host/group scope — otherwise it would produce a duplicate
    ``(name, fingerprint)`` label set, which every Prometheus exposition
    must never do."""
    from app.ca_certs.models import CACertRule, CertState

    stmt = (
        select(CACertRule.name, CACertRule.fingerprint_sha256, CACertRule.not_after)
        .where(CACertRule.state == CertState.present, CACertRule.not_after.isnot(None))
        .distinct()
    )
    result = await db.execute(stmt)
    return [(row.name, row.fingerprint_sha256, row.not_after) for row in result.all()]


# ---------------------------------------------------------------------------
# 11 & 12. Duration histograms — bucket columns generated from the bounds
#          constants so SQL and the exposed buckets can never drift apart.
# ---------------------------------------------------------------------------


async def get_sync_duration_histogram(db: AsyncSession) -> HistogramCounts:
    """Unlabelled sync-job duration histogram (seconds).

    ``duration = EXTRACT(EPOCH FROM (completed_at - started_at))``, guarded
    to only include rows where both timestamps are set and
    ``completed_at >= started_at`` (clock skew / bad data guard).
    """
    duration = func.extract("epoch", SyncJob.completed_at - SyncJob.started_at)
    valid = and_(
        SyncJob.started_at.isnot(None),
        SyncJob.completed_at.isnot(None),
        SyncJob.completed_at >= SyncJob.started_at,
    )
    bucket_cols = [
        func.count().filter(duration <= bound).label(f"le_{i}")
        for i, bound in enumerate(_BUCKETS_SYNC)
    ]
    stmt = select(
        *bucket_cols,
        func.count().label("total_count"),
        func.coalesce(func.sum(duration), 0.0).label("total_sum"),
    ).where(valid)
    row = (await db.execute(stmt)).one()
    bucket_counts = [getattr(row, f"le_{i}") for i in range(len(_BUCKETS_SYNC))]
    return HistogramCounts(
        bucket_counts=bucket_counts, total_count=row.total_count, total_sum=float(row.total_sum)
    )


async def get_drift_duration_histogram(db: AsyncSession) -> list[tuple[str, HistogramCounts]]:
    """Per-module drift-check duration histogram (seconds).

    ``drift_samples.duration_ms`` is milliseconds and nullable — rows with
    ``duration_ms IS NULL`` are excluded entirely (they still count toward
    ``labdog_drift_checks_total``; see the histogram's HELP text for this
    caveat, since it means ``..._count`` is NOT the drift-check count).
    """
    duration_seconds = DriftSample.duration_ms / 1000.0
    bucket_cols = [
        func.count().filter(duration_seconds <= bound).label(f"le_{i}")
        for i, bound in enumerate(_BUCKETS_DRIFT)
    ]
    stmt = (
        select(
            DriftSample.module_type,
            *bucket_cols,
            func.count().label("total_count"),
            func.coalesce(func.sum(duration_seconds), 0.0).label("total_sum"),
        )
        .where(DriftSample.duration_ms.isnot(None))
        .group_by(DriftSample.module_type)
    )
    result = await db.execute(stmt)
    out: list[tuple[str, HistogramCounts]] = []
    for row in result.all():
        bucket_counts = [getattr(row, f"le_{i}") for i in range(len(_BUCKETS_DRIFT))]
        out.append(
            (
                row.module_type,
                HistogramCounts(
                    bucket_counts=bucket_counts,
                    total_count=row.total_count,
                    total_sum=float(row.total_sum),
                ),
            )
        )
    return out
