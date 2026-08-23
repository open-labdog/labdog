"""Alert intake: parsing, deduplication, and the investigation policy.

The parsers and the severity threshold need no database. The dedup does,
because the contract it enforces is a database constraint — two producers
racing into one row is exactly what an in-memory test would not catch.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.ai.alerts import (
    compute_fingerprint,
    from_alertmanager_v2,
    from_grafana_webhook,
    meets_severity,
    record,
    resolve_host,
)
from app.ai.models import AlertEvent
from tests.conftest import create_host, create_ssh_key


def _grafana(**over):
    alert = {
        "status": "firing",
        "labels": {"alertname": "HostDown", "severity": "critical", "instance": "web-1:9100"},
        "annotations": {"summary": "host is down"},
        "startsAt": "2026-08-17T10:00:00Z",
        "endsAt": "0001-01-01T00:00:00Z",
        "fingerprint": "fp-1",
    }
    alert.update(over)
    return {"status": "firing", "alerts": [alert]}


class TestParsingWhatGrafanaSends:
    def test_a_firing_alert_is_read(self) -> None:
        (alert,) = from_grafana_webhook(_grafana())
        assert alert.alertname == "HostDown"
        assert alert.severity == "critical"
        assert alert.status == "firing"
        assert alert.fingerprint == "fp-1"

    def test_the_year_one_end_time_is_not_a_timestamp(self) -> None:
        """Alertmanager renders "no end time" as year 1 rather than
        omitting it. Parsed naively that lands a date two thousand years
        in the past in a column meaning "when this resolved"."""
        (alert,) = from_grafana_webhook(_grafana())
        assert alert.ends_at is None

    def test_a_real_end_time_survives(self) -> None:
        (alert,) = from_grafana_webhook(_grafana(status="resolved", endsAt="2026-08-17T11:00:00Z"))
        assert alert.status == "resolved"
        assert alert.ends_at is not None

    def test_the_per_alert_status_beats_the_envelope(self) -> None:
        """A grouped notification can carry both; the envelope only says
        that *something* in the group is firing."""
        payload = _grafana()
        payload["alerts"][0]["status"] = "resolved"
        (alert,) = from_grafana_webhook(payload)
        assert alert.status == "resolved"

    def test_an_alert_with_no_name_is_dropped(self) -> None:
        """There would be nothing to show an operator and nothing to
        investigate — a row called "" is worse than no row."""
        payload = _grafana()
        payload["alerts"][0]["labels"] = {"severity": "critical"}
        assert from_grafana_webhook(payload) == []

    def test_an_alert_with_no_start_time_is_dropped(self) -> None:
        """It is half the dedup key. Without it this firing cannot be told
        apart from the next one, which would corrupt both."""
        payload = _grafana()
        payload["alerts"][0]["startsAt"] = ""
        assert from_grafana_webhook(payload) == []

    def test_an_empty_payload_is_not_an_error(self) -> None:
        assert from_grafana_webhook({}) == []

    def test_a_missing_fingerprint_is_computed(self) -> None:
        payload = _grafana()
        del payload["alerts"][0]["fingerprint"]
        (alert,) = from_grafana_webhook(payload)
        assert alert.fingerprint
        assert alert.fingerprint == compute_fingerprint(alert.labels)


class TestParsingWhatAlertmanagerSends:
    @staticmethod
    def _am(state: str):
        return [
            {
                "labels": {"alertname": "DiskFull", "severity": "warning"},
                "annotations": {},
                "startsAt": "2026-08-17T10:00:00Z",
                "endsAt": "0001-01-01T00:00:00Z",
                "status": {"state": state},
                "fingerprint": "fp-2",
            }
        ]

    def test_active_is_firing(self) -> None:
        (alert,) = from_alertmanager_v2(self._am("active"))
        assert alert.status == "firing"

    def test_suppressed_is_not_firing(self) -> None:
        """Silenced or inhibited — somebody already decided it should not
        page, so it is recorded but kept out of the investigation path."""
        (alert,) = from_alertmanager_v2(self._am("suppressed"))
        assert alert.status == "resolved"

    def test_the_v2_status_object_does_not_crash_the_string_parser(self) -> None:
        """v2 sends ``{"state": ...}`` where the webhook sends a string."""
        raw = [
            {
                "labels": {"alertname": "X"},
                "startsAt": "2026-08-17T10:00:00Z",
                # A string where v2 sends an object — a shape that would
                # crash a parser assuming one or the other.
                "status": "active",
            }
        ]
        assert from_alertmanager_v2(raw)[0].status == "resolved"

    def test_junk_entries_are_skipped(self) -> None:
        assert from_alertmanager_v2([None, "nonsense", 42]) == []


class TestTheSeverityThreshold:
    @pytest.mark.parametrize(
        ("severity", "minimum", "expected"),
        [
            ("critical", "critical", True),
            ("critical", "warning", True),
            ("warning", "warning", True),
            ("warning", "critical", False),
            ("info", "warning", False),
            ("CRITICAL", "critical", True),
        ],
    )
    def test_the_ordering(self, severity: str, minimum: str, expected: bool) -> None:
        assert meets_severity(severity, minimum) is expected

    def test_an_unlabelled_alert_does_not_meet_any_threshold(self) -> None:
        assert meets_severity(None, "info") is False

    def test_an_unrecognised_severity_does_not_meet_the_threshold(self) -> None:
        """Grafana lets you label an alert anything. Treating "sev1" as
        critical would be a guess; treating it as trivial would be
        another — but only one of them spends money unattended. The
        outcome is recorded so it shows up rather than vanishing."""
        assert meets_severity("sev1", "info") is False

    def test_an_unparseable_threshold_does_not_open_the_gate(self) -> None:
        assert meets_severity("critical", "banana") is False


class TestDedup:
    """The contract is a database constraint, so these need a database."""

    @staticmethod
    def _alert(fingerprint: str = "fp-1", **over):
        payload = _grafana(fingerprint=fingerprint, **over)
        return from_grafana_webhook(payload)[0]

    async def test_the_first_arrival_creates_a_row(self, db) -> None:
        event, created = await record(db, self._alert(), source="grafana_webhook")
        assert created is True
        assert event.dedup_count == 1

    async def test_the_second_arrival_increments_instead(self, db) -> None:
        await record(db, self._alert(), source="grafana_webhook")
        event, created = await record(db, self._alert(), source="alertmanager_poll")
        assert created is False
        assert event.dedup_count == 2

    async def test_the_webhook_and_the_poller_do_not_make_two_rows(self, db) -> None:
        """The whole reason both producers can be enabled at once."""
        await record(db, self._alert(), source="grafana_webhook")
        await record(db, self._alert(), source="alertmanager_poll")
        rows = (
            (await db.execute(select(AlertEvent).where(AlertEvent.fingerprint == "fp-1")))
            .scalars()
            .all()
        )
        assert len(rows) == 1

    async def test_a_new_firing_of_the_same_rule_is_a_new_row(self, db) -> None:
        """The fingerprint hashes the label set, so it is identical for
        this month's outage and next month's. Keying on it alone would
        fold the second into the first and lose the history."""
        await record(db, self._alert(), source="grafana_webhook")
        later = self._alert(startsAt="2026-09-01T10:00:00Z")
        _, created = await record(db, later, source="grafana_webhook")
        assert created is True

    async def test_resolving_updates_the_existing_row(self, db) -> None:
        await record(db, self._alert(), source="grafana_webhook")
        resolved = self._alert(status="resolved", endsAt="2026-08-17T11:00:00Z")
        event, created = await record(db, resolved, source="grafana_webhook")
        assert created is False
        assert event.status == "resolved"
        assert event.ends_at is not None


class TestHostResolution:
    async def test_an_instance_label_with_a_port_still_matches(self, db) -> None:
        key = await create_ssh_key(db)
        host = await create_host(db, hostname="web-1", ssh_key_id=key.id)
        assert await resolve_host(db, {"instance": "web-1:9100"}) == host.id

    async def test_an_ip_address_matches(self, db) -> None:
        key = await create_ssh_key(db)
        host = await create_host(db, hostname="web-2", ip="10.9.9.9", ssh_key_id=key.id)
        assert await resolve_host(db, {"instance": "10.9.9.9:9100"}) == host.id

    async def test_an_unknown_host_resolves_to_nothing(self, db) -> None:
        """Guessing would put the investigation on the wrong host, which
        then reads healthy and reports that nothing is wrong."""
        assert await resolve_host(db, {"instance": "not-a-labdog-host:9100"}) is None

    async def test_no_host_label_at_all(self, db) -> None:
        assert await resolve_host(db, {"job": "blackbox"}) is None

    async def test_the_alert_records_the_host_it_resolved(self, db) -> None:
        key = await create_ssh_key(db)
        host = await create_host(db, hostname="web-1", ssh_key_id=key.id)
        alert = from_grafana_webhook(_grafana())[0]
        event, _ = await record(db, alert, source="grafana_webhook")
        assert event.host_id == host.id


class TestTheRecordedAlert:
    async def test_labels_and_annotations_are_kept_whole(self, db) -> None:
        """LabDog reads three keys out of them, but an investigation is
        only as good as its context and the operator chose what to
        label."""
        alert = from_grafana_webhook(_grafana())[0]
        event, _ = await record(db, alert, source="grafana_webhook")
        assert event.labels["instance"] == "web-1:9100"
        assert event.annotations["summary"] == "host is down"

    async def test_the_source_is_recorded(self, db) -> None:
        alert = from_grafana_webhook(_grafana())[0]
        event, _ = await record(db, alert, source="alertmanager_poll")
        assert event.source == "alertmanager_poll"

    async def test_a_fresh_alert_has_no_investigation_yet(self, db) -> None:
        alert = from_grafana_webhook(_grafana())[0]
        event, _ = await record(db, alert, source="grafana_webhook")
        assert event.investigation_session_id is None
        assert event.investigation_outcome is None


def _now() -> datetime:
    return datetime.now(UTC)


class TestWhatTheAlertRowSaysAboutItsInvestigation:
    """``investigation_outcome`` is written once and never revised.

    It records the *decision* to start, so a row whose session finished an
    hour ago, one still running, and one that failed all read "started" —
    which the alerts page rendered as "Investigating" indefinitely. The
    session's own status is what answers "and then what happened", so the
    list resolves it rather than leaving the reader to open each alert.
    """

    def test_the_conclusion_is_the_reports_first_paragraph(self) -> None:
        from app.api.ai import _conclusion

        report = (
            "**Verdict: false positive** — the host is healthy.\n\n"
            "Full detail follows, which nobody needs on a list row.\n\n"
            "- uptime 4 days\n- load 0.11\n"
        )

        assert _conclusion(report) == "Verdict: false positive — the host is healthy."

    def test_a_long_conclusion_is_cut_on_a_word_and_marked(self) -> None:
        """A hard character cut mid-word reads as the assistant trailing
        off rather than as the UI abbreviating."""
        from app.api.ai import _SUMMARY_CHARS, _conclusion

        summary = _conclusion("word " * 200)

        assert summary is not None
        assert summary.endswith("…")
        assert len(summary) <= _SUMMARY_CHARS + 1
        assert not summary.rstrip("…").endswith("wor")

    @pytest.mark.parametrize("report", [None, "", "   \n\n  "])
    def test_nothing_to_quote_yields_nothing(self, report) -> None:
        """A running session has no report yet. Returning "" would put an
        empty quote block on the row."""
        from app.api.ai import _conclusion

        assert _conclusion(report) is None
