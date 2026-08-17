"""Alert intake: one shape, two producers, one row per firing.

A Grafana contact point can POST straight to LabDog, and LabDog can poll
an Alertmanager API. **Both are enabled together on purpose.** The
webhook is immediate but only works while LabDog is reachable from
Grafana; the poller is slower but catches everything that happened while
it was not — a restart, a network partition, a container that was down
for an upgrade. Alerts that arrive by both paths must not become two
rows, which is what the fingerprint dedup below is for.

Nothing here starts an investigation. Recording an alert and deciding to
spend money on it are separate concerns, and keeping them separate is
what lets the poller catch up on a hundred alerts without opening a
hundred sessions.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.models import AlertEvent
from app.models.host import Host

logger = logging.getLogger(__name__)

#: Alertmanager renders "no end time" as year 1 rather than omitting the
#: field, and a naive parse turns that into a timestamp two thousand
#: years in the past sitting in a column that means "when this resolved".
_NULL_TIME_YEAR = 1

#: Severity ordering for the auto-investigation threshold. Deliberately
#: short: these are the three Prometheus/Grafana conventions. Anything
#: else is unrecognised rather than silently ranked — see
#: :func:`meets_severity`.
SEVERITY_ORDER = ("info", "warning", "critical")

#: Labels that conventionally carry the host, most specific first.
#: ``instance`` is last because it is the noisiest — it usually carries a
#: port, and sometimes an address that is a scrape target rather than the
#: machine.
HOST_LABELS = ("nodename", "hostname", "host", "node", "instance")


@dataclass
class NormalizedAlert:
    """One alert, in the shape LabDog stores regardless of who sent it."""

    fingerprint: str
    alertname: str
    starts_at: datetime
    status: str = "firing"
    severity: str | None = None
    ends_at: datetime | None = None
    labels: dict[str, Any] = field(default_factory=dict)
    annotations: dict[str, Any] = field(default_factory=dict)

    @property
    def is_firing(self) -> bool:
        return self.status == "firing"


def _parse_time(raw: Any) -> datetime | None:
    """Parse an Alertmanager timestamp, treating year 1 as absent."""
    if not raw or not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.year <= _NULL_TIME_YEAR:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def compute_fingerprint(labels: dict[str, Any]) -> str:
    """A stable hash of the label set.

    Only used when the sender did not supply one. Alertmanager's own
    fingerprint is computed this way — from the sorted label set — so a
    LabDog-computed one is stable across restarts and identical for the
    same alert, which is all the dedup key needs. It will not *match*
    Alertmanager's, so a single alert arriving once with a fingerprint and
    once without would deduplicate as two; in practice both producers
    supply one, and this exists so a hand-rolled webhook sender does not
    silently collapse every alert into a single row keyed on "".
    """
    canonical = "\n".join(f"{k}={labels[k]}" for k in sorted(labels))
    return hashlib.sha256(canonical.encode()).hexdigest()[:64]


def _normalize(
    labels: dict[str, Any],
    annotations: dict[str, Any],
    *,
    status: str,
    starts_at: Any,
    ends_at: Any,
    fingerprint: str | None,
) -> NormalizedAlert | None:
    """Shared tail of both parsers. ``None`` when the alert is unusable."""
    labels = {str(k): v for k, v in (labels or {}).items()}
    alertname = str(labels.get("alertname") or "").strip()
    if not alertname:
        # Without a name there is nothing to show an operator and nothing
        # to investigate. Dropping it beats storing a row called "".
        return None

    started = _parse_time(starts_at)
    if started is None:
        # Half the dedup key. An alert with no start time cannot be told
        # apart from the next firing of the same rule, so recording it
        # would corrupt the history of both.
        return None

    severity = labels.get("severity")
    return NormalizedAlert(
        fingerprint=str(fingerprint) if fingerprint else compute_fingerprint(labels),
        alertname=alertname[:255],
        starts_at=started,
        status="resolved" if status == "resolved" else "firing",
        severity=str(severity)[:32] if severity else None,
        ends_at=_parse_time(ends_at),
        labels=labels,
        annotations={str(k): v for k, v in (annotations or {}).items()},
    )


def from_grafana_webhook(payload: dict[str, Any]) -> list[NormalizedAlert]:
    """Parse a Grafana contact-point (Alertmanager-compatible) payload.

    The envelope carries a ``status`` for the whole notification and each
    entry carries its own. The per-alert one wins: a grouped notification
    can contain both firing and resolved entries, and the envelope only
    tells you that *something* in the group is firing.
    """
    out: list[NormalizedAlert] = []
    for raw in payload.get("alerts") or []:
        if not isinstance(raw, dict):
            continue
        alert = _normalize(
            raw.get("labels") or {},
            raw.get("annotations") or {},
            status=str(raw.get("status") or payload.get("status") or "firing"),
            starts_at=raw.get("startsAt"),
            ends_at=raw.get("endsAt"),
            fingerprint=raw.get("fingerprint"),
        )
        if alert is not None:
            out.append(alert)
    return out


def from_alertmanager_v2(alerts: list[Any]) -> list[NormalizedAlert]:
    """Parse the ``GET /api/v2/alerts`` response.

    The v2 API's ``status`` is an object (``{"state": "active", ...}``)
    rather than the string the webhook sends, and its states are
    ``active`` / ``suppressed`` / ``unprocessed`` rather than firing /
    resolved. A suppressed alert is silenced or inhibited — somebody has
    already decided it should not page — so it is recorded as resolved
    rather than firing, which keeps it out of the investigation path
    without discarding it.
    """
    out: list[NormalizedAlert] = []
    for raw in alerts or []:
        if not isinstance(raw, dict):
            continue
        status_obj = raw.get("status")
        state = status_obj.get("state") if isinstance(status_obj, dict) else None
        alert = _normalize(
            raw.get("labels") or {},
            raw.get("annotations") or {},
            status="firing" if state == "active" else "resolved",
            starts_at=raw.get("startsAt"),
            ends_at=raw.get("endsAt"),
            fingerprint=raw.get("fingerprint"),
        )
        if alert is not None:
            out.append(alert)
    return out


async def resolve_host(db: AsyncSession, labels: dict[str, Any]) -> int | None:
    """Best-effort match from alert labels to a LabDog host.

    Returns ``None`` freely. Plenty of alerts are about a service, a
    cluster, or a job rather than a machine LabDog manages, and guessing
    would put an investigation on the wrong host — worse than putting it
    on none, because the session would then read a healthy host and
    report that nothing is wrong.
    """
    for key in HOST_LABELS:
        raw = labels.get(key)
        if not raw:
            continue
        candidate = str(raw).strip()
        if not candidate:
            continue
        # `instance` is conventionally host:port; the port is not part of
        # any identity LabDog stores.
        bare = candidate.rsplit(":", 1)[0] if ":" in candidate else candidate
        for value in (candidate, bare):
            result = await db.execute(
                select(Host.id)
                .where((Host.hostname == value) | (Host.ip_address == value))
                .limit(1)
            )
            host_id = result.scalar_one_or_none()
            if host_id is not None:
                return host_id
    return None


async def record(
    db: AsyncSession, alert: NormalizedAlert, *, source: str
) -> tuple[AlertEvent, bool]:
    """Upsert one alert. Returns ``(row, created)``.

    The insert is ``ON CONFLICT (fingerprint, starts_at) DO UPDATE`` so
    the webhook and the poller cannot race into two rows for the same
    firing — whichever loses increments ``dedup_count`` and refreshes the
    status instead.

    ``created`` is what the caller uses to decide whether to investigate.
    A repeat notification about an alert already being looked at should
    not start a second session.
    """
    now = datetime.now(UTC)
    host_id = await resolve_host(db, alert.labels)

    stmt = (
        pg_insert(AlertEvent)
        .values(
            source=source,
            fingerprint=alert.fingerprint,
            alertname=alert.alertname,
            severity=alert.severity,
            status=alert.status,
            labels=alert.labels,
            annotations=alert.annotations,
            starts_at=alert.starts_at,
            ends_at=alert.ends_at,
            dedup_count=1,
            host_id=host_id,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            constraint="uq_alert_events_fingerprint_starts",
            set_={
                "dedup_count": AlertEvent.dedup_count + 1,
                # A firing alert that later resolves arrives as a second
                # notification with the same start time; the row should
                # follow it rather than stay stuck on "firing".
                "status": alert.status,
                "ends_at": alert.ends_at,
                "updated_at": now,
            },
        )
        .returning(AlertEvent.id, AlertEvent.dedup_count)
    )
    row = (await db.execute(stmt)).one()
    created = row.dedup_count == 1
    event = await db.get(AlertEvent, row.id)
    if event is None:  # pragma: no cover - the row was just written
        raise RuntimeError(f"alert_events row {row.id} vanished after upsert")
    return event, created


def meets_severity(severity: str | None, minimum: str) -> bool:
    """Whether ``severity`` is at least ``minimum``.

    An unrecognised severity does **not** meet the threshold. Grafana
    lets you label an alert anything, so treating "sev1" as critical
    would be a guess, and treating it as trivial would be another one —
    but only one of those spends money unattended. The outcome is
    recorded as ``skipped_severity`` with the value that was not
    understood, so it shows up in the UI rather than vanishing.
    """
    if severity is None:
        return False
    try:
        want = SEVERITY_ORDER.index(minimum.strip().lower())
    except ValueError:
        # A threshold nobody can parse should not silently open the gate.
        return False
    try:
        have = SEVERITY_ORDER.index(severity.strip().lower())
    except ValueError:
        return False
    return have >= want
