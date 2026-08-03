"""Tests for the Prometheus ``/metrics`` exporter and its status endpoint.

``TestExposition`` is a pure unit-test class (no DB, no app) — everything
else needs the real Postgres testcontainer via the shared ``client``/``db``
fixtures, so ``pytestmark = pytest.mark.integration`` applies to the rest of
the module.

The autouse ``reset_metrics_cache`` fixture is **non-negotiable**: without
it, whichever test runs first "wins" the module-level TTL cache in
``app.metrics.collector`` and every later test in the same process would
observe *its* snapshot instead of its own seeded rows.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from app.metrics.exposition import (
    CONTENT_TYPE,
    escape_help,
    escape_label_value,
    format_float,
    histogram,
    render,
    render_labels,
)

from .conftest import create_host

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_metrics_cache():
    """Non-negotiable: drop the module-level TTL cache before AND after
    every test, otherwise test N's assertions can observe test N-1's
    cached snapshot instead of its own seeded rows."""
    from app.metrics.collector import reset_cache

    reset_cache()
    yield
    reset_cache()


@pytest.fixture
def metrics_enabled(monkeypatch):
    """Flip ``settings.metrics.enabled`` for the duration of one test.

    This only works because ``app.api.metrics.get_metrics`` (and
    ``app.api.metrics.get_metrics_status``) read ``settings.metrics.enabled``
    **at call time** rather than capturing it as a module-level constant —
    the ``app`` fixture is session-scoped, so the app object (and every
    module it imported) was already constructed before this fixture ever
    runs; a value captured at import time would never observe this
    monkeypatch.
    """
    from app.config import settings

    monkeypatch.setattr(settings.metrics, "enabled", True)


def _parse_samples(text: str) -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
    from prometheus_client.parser import text_string_to_metric_families

    out: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
    for family in text_string_to_metric_families(text):
        for sample in family.samples:
            key = (sample.name, tuple(sorted(sample.labels.items())))
            out[key] = sample.value
    return out


# ---------------------------------------------------------------------------
# Disabled by default
# ---------------------------------------------------------------------------


class TestDisabledByDefault:
    async def test_get_returns_404(self, client):
        resp = await client.get("/metrics")
        assert resp.status_code == 404

    async def test_head_returns_404(self, client):
        resp = await client.head("/metrics")
        assert resp.status_code == 404

    async def test_get_is_not_the_spa_shell(self, client):
        """Regression guard: if the router were ever conditionally
        registered instead of always-registered-with-internal-404, this
        request would silently fall through to the SPA catch-all and come
        back 200 + index.html instead of 404."""
        resp = await client.get("/metrics")
        assert resp.status_code == 404
        assert "text/html" not in resp.headers.get("content-type", "")
        body = resp.text
        assert "<!DOCTYPE" not in body
        assert "<html" not in body

    async def test_head_is_not_the_spa_shell(self, client):
        resp = await client.head("/metrics")
        assert resp.status_code == 404
        assert "text/html" not in resp.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# Enabled — shape / plumbing
# ---------------------------------------------------------------------------


class TestEnabled:
    async def test_exact_content_type(self, metrics_enabled, client):
        resp = await client.get("/metrics")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == CONTENT_TYPE

    async def test_unauthenticated_client_gets_200(self, metrics_enabled, client):
        """Mirrors tests/test_version.py's public-endpoint check."""
        resp = await client.get("/metrics")
        assert resp.status_code == 200

    async def test_parses_with_real_prometheus_client(self, metrics_enabled, client):
        pytest.importorskip("prometheus_client")
        from prometheus_client.parser import text_string_to_metric_families

        resp = await client.get("/metrics")
        families = list(text_string_to_metric_families(resp.text))
        assert families, "no metric families parsed"
        for family in families:
            assert family.documentation, f"{family.name} has no HELP text"
            assert family.type in ("gauge", "counter", "histogram")

    async def test_no_duplicate_name_labelset(self, metrics_enabled, client):
        pytest.importorskip("prometheus_client")
        from prometheus_client.parser import text_string_to_metric_families

        resp = await client.get("/metrics")
        seen: set[tuple[str, tuple]] = set()
        for family in text_string_to_metric_families(resp.text):
            for sample in family.samples:
                key = (sample.name, tuple(sorted(sample.labels.items())))
                assert key not in seen, f"duplicate exposition sample: {key}"
                seen.add(key)

    async def test_empty_db_zero_fills_known_enums(self, metrics_enabled, client):
        """SyncStatus / GitOpsStatus / GitAuthType are zero-filled even with
        no rows at all — a Host.sync_status/HostGroup.gitops_status/
        GitRepository.auth_type column exists on an empty DB regardless."""
        resp = await client.get("/metrics")
        body = resp.text
        assert 'labdog_hosts{sync_status="error"} 0' in body
        assert 'labdog_hosts{sync_status="pending"} 0' in body
        assert 'labdog_hosts{sync_status="in_sync"} 0' in body
        assert 'labdog_hosts{sync_status="out_of_sync"} 0' in body
        assert 'labdog_hosts{sync_status="unknown"} 0' in body
        assert 'labdog_groups_gitops{status="error"} 0' in body
        assert 'labdog_groups_gitops{status="importing"} 0' in body
        assert 'labdog_git_repositories{auth_type="ssh_key"} 0' in body
        assert 'labdog_git_repositories{auth_type="https_token"} 0' in body

    async def test_cache_is_single_flight(self, metrics_enabled, client, monkeypatch):
        import app.metrics.collector as collector_module

        original_collect = collector_module.collect
        calls: list[int] = []

        async def _counting_collect(db):
            calls.append(1)
            return await original_collect(db)

        monkeypatch.setattr(collector_module, "collect", _counting_collect)

        first = await client.get("/metrics")
        second = await client.get("/metrics")

        assert first.status_code == 200
        assert second.status_code == 200
        assert len(calls) == 1, "second scrape within the TTL window re-collected"

    async def test_exporter_self_metrics_present(self, metrics_enabled, client):
        resp = await client.get("/metrics")
        body = resp.text
        assert "labdog_metrics_scrape_duration_seconds" in body
        assert "labdog_metrics_cache_age_seconds" in body
        assert "labdog_metrics_scrape_errors_total" in body


# ---------------------------------------------------------------------------
# Values match seeded rows
# ---------------------------------------------------------------------------


class TestValuesMatchSeededRows:
    async def test_hosts_by_sync_status(self, metrics_enabled, client, db):
        from app.models.host import SyncStatus

        host_a = await create_host(db, hostname="metrics-a.test")
        host_b = await create_host(db, hostname="metrics-b.test")
        host_a.sync_status = SyncStatus.error
        host_b.sync_status = SyncStatus.error
        await db.flush()

        resp = await client.get("/metrics")
        samples = _parse_samples(resp.text)
        assert samples[("labdog_hosts", (("sync_status", "error"),))] == 2
        assert samples[("labdog_hosts", (("sync_status", "pending"),))] == 0

    async def test_sync_jobs_total_vs_inflight(self, metrics_enabled, client, db):
        from app.models.sync_job import JobStatus, SyncJob

        host = await create_host(db)
        db.add_all(
            [
                SyncJob(host_id=host.id, status=JobStatus.success, module_type="firewall"),
                SyncJob(host_id=host.id, status=JobStatus.success, module_type="firewall"),
                SyncJob(host_id=host.id, status=JobStatus.failed, module_type="firewall"),
                SyncJob(host_id=host.id, status=JobStatus.running, module_type="firewall"),
                SyncJob(host_id=host.id, status=JobStatus.pending, module_type="package"),
            ]
        )
        await db.flush()

        resp = await client.get("/metrics")
        samples = _parse_samples(resp.text)

        total_key = lambda status, module: (  # noqa: E731
            "labdog_sync_jobs_total",
            (("module", module), ("status", status)),
        )
        inflight_key = lambda status, module: (  # noqa: E731
            "labdog_sync_jobs_inflight",
            (("module", module), ("status", status)),
        )

        assert samples[total_key("success", "firewall")] == 2
        assert samples[total_key("failed", "firewall")] == 1
        assert samples[total_key("running", "firewall")] == 1
        assert samples[total_key("cancelled", "firewall")] == 0  # zero-filled within the module
        assert samples[total_key("pending", "package")] == 1

        assert samples[inflight_key("running", "firewall")] == 1
        assert samples[inflight_key("pending", "package")] == 1
        assert (
            "labdog_sync_jobs_inflight",
            (("module", "firewall"), ("status", "success")),
        ) not in samples

    async def test_drift_checks_total_error_result(self, metrics_enabled, client, db):
        from app.models.drift_sample import DriftSample

        host = await create_host(db)
        db.add_all(
            [
                DriftSample(host_id=host.id, module_type="firewall", status="error"),
                DriftSample(host_id=host.id, module_type="firewall", status="error"),
                DriftSample(host_id=host.id, module_type="firewall", status="in_sync"),
            ]
        )
        await db.flush()

        resp = await client.get("/metrics")
        samples = _parse_samples(resp.text)
        assert (
            samples[("labdog_drift_checks_total", (("module", "firewall"), ("result", "error")))]
            == 2
        )
        assert (
            samples[("labdog_drift_checks_total", (("module", "firewall"), ("result", "in_sync")))]
            == 1
        )

    async def test_host_module_status_observed_value_not_in_enum(self, metrics_enabled, client, db):
        """HostModuleStatus.sync_status is free text, not the Host SyncStatus
        enum — 'drifted' isn't one of SyncStatus's 5 canonical values, which
        proves this dimension is observed-values-only, not validated/enum."""
        from app.models.host_module_status import HostModuleStatus

        host = await create_host(db)
        db.add(HostModuleStatus(host_id=host.id, module_type="firewall", sync_status="drifted"))
        await db.flush()

        resp = await client.get("/metrics")
        samples = _parse_samples(resp.text)
        assert (
            samples[("labdog_host_modules", (("module", "firewall"), ("sync_status", "drifted")))]
            == 1
        )

    async def test_sync_and_drift_duration_histograms(self, metrics_enabled, client, db):
        from app.models.drift_sample import DriftSample
        from app.models.sync_job import JobStatus, SyncJob

        host = await create_host(db)
        now = datetime.now(UTC)

        # Sync duration histogram (unlabelled): 3s and 45s completed jobs.
        db.add_all(
            [
                SyncJob(
                    host_id=host.id,
                    status=JobStatus.success,
                    module_type="firewall",
                    started_at=now,
                    completed_at=now + timedelta(seconds=3),
                ),
                SyncJob(
                    host_id=host.id,
                    status=JobStatus.success,
                    module_type="firewall",
                    started_at=now,
                    completed_at=now + timedelta(seconds=45),
                ),
                # In-flight job: no completed_at — must not appear in the histogram.
                SyncJob(host_id=host.id, status=JobStatus.running, module_type="firewall"),
            ]
        )

        # Drift duration histogram, module="service": two valid durations
        # (0.8s, 1.5s) and one NULL-duration row that must be excluded from
        # the histogram but still counts toward labdog_drift_checks_total.
        db.add_all(
            [
                DriftSample(
                    host_id=host.id,
                    module_type="service",
                    status="in_sync",
                    duration_ms=800,
                ),
                DriftSample(
                    host_id=host.id,
                    module_type="service",
                    status="in_sync",
                    duration_ms=1500,
                ),
                DriftSample(
                    host_id=host.id,
                    module_type="service",
                    status="in_sync",
                    duration_ms=None,
                ),
            ]
        )
        await db.flush()

        resp = await client.get("/metrics")
        samples = _parse_samples(resp.text)

        # format_float() renders integral bounds without a decimal point.
        assert samples[("labdog_sync_job_duration_seconds_bucket", (("le", "5"),))] == 1
        assert samples[("labdog_sync_job_duration_seconds_bucket", (("le", "1800"),))] == 2
        assert samples[("labdog_sync_job_duration_seconds_count", ())] == 2
        assert samples[("labdog_sync_job_duration_seconds_sum", ())] == pytest.approx(48.0)

        drift_count_key = ("labdog_drift_check_duration_seconds_count", (("module", "service"),))
        drift_sum_key = ("labdog_drift_check_duration_seconds_sum", (("module", "service"),))
        assert samples[drift_count_key] == 2  # excludes the NULL-duration row
        assert samples[drift_sum_key] == pytest.approx(2.3)

        # But labdog_drift_checks_total DOES include the NULL-duration row.
        assert (
            samples[("labdog_drift_checks_total", (("module", "service"), ("result", "in_sync")))]
            == 3
        )

        bucket_1 = (
            "labdog_drift_check_duration_seconds_bucket",
            (("le", "1"), ("module", "service")),
        )
        bucket_2_5 = (
            "labdog_drift_check_duration_seconds_bucket",
            (("le", "2.5"), ("module", "service")),
        )
        assert samples[bucket_1] == 1
        assert samples[bucket_2_5] == 2

    async def test_orphaned_scheduled_action(self, metrics_enabled, client, db):
        from app.models.scheduled_action import ScheduledAction

        db.add(
            ScheduledAction(
                target_kind="fleet",
                target_id=None,
                action_key="orphan.no-pack",
                schedule_cron="* * * * *",
                enabled=True,
            )
        )
        await db.flush()

        resp = await client.get("/metrics")
        samples = _parse_samples(resp.text)
        assert samples[("labdog_scheduled_actions_orphaned", ())] == 1

    async def test_ca_cert_expiry(self, metrics_enabled, client, db):
        from app.ca_certs.models import CACertRule, CertState

        host = await create_host(db)
        not_after = datetime(2030, 1, 1, tzinfo=UTC)
        db.add(
            CACertRule(
                host_id=host.id,
                name="Test Root CA",
                pem_content="-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----",
                fingerprint_sha256="deadbeef" * 8,
                not_after=not_after,
                state=CertState.present,
            )
        )
        await db.flush()

        resp = await client.get("/metrics")
        samples = _parse_samples(resp.text)
        key = (
            "labdog_ca_cert_not_after_timestamp_seconds",
            (("fingerprint", "deadbeef" * 8), ("name", "Test Root CA")),
        )
        assert samples[key] == pytest.approx(not_after.timestamp())


# ---------------------------------------------------------------------------
# Exposition — pure unit tests, no DB / no app.
# ---------------------------------------------------------------------------


class TestExposition:
    def test_escape_help_backslash_and_newline_only(self):
        assert escape_help('line\\one\nline"two') == 'line\\\\one\\nline"two'

    def test_escape_label_value_backslash_before_quote_ordering(self):
        # Raw content is the 4 characters: a \ " b. Backslash must be
        # escaped BEFORE quote — otherwise the backslash the quote-escape
        # pass introduces would itself get doubled by a later
        # backslash-escaping pass.
        raw = 'a\\"b'
        assert escape_label_value(raw) == 'a\\\\\\"b'

    def test_escape_label_value_newline(self):
        assert escape_label_value("a\nb") == "a\\nb"

    def test_format_float_special_values(self):
        assert format_float(float("nan")) == "NaN"
        assert format_float(float("inf")) == "+Inf"
        assert format_float(float("-inf")) == "-Inf"

    def test_format_float_integral_has_no_decimal_point(self):
        assert format_float(3.0) == "3"
        assert format_float(0.0) == "0"
        assert format_float(-5.0) == "-5"

    def test_format_float_non_integral_keeps_precision(self):
        assert format_float(3.14) == "3.14"
        assert math.isclose(float(format_float(0.1)), 0.1)

    def test_render_labels_empty_has_no_braces(self):
        assert render_labels(()) == ""

    def test_render_labels_non_empty(self):
        assert render_labels((("a", "1"), ("b", "2"))) == '{a="1",b="2"}'

    def test_histogram_le_inf_equals_count(self):
        family = histogram(
            "labdog_test_duration_seconds",
            "help text",
            bounds=(1.0, 5.0),
            series=[((), [1, 3], 4, 12.5)],
        )
        inf_samples = [
            s for s in family.samples if s.suffix == "_bucket" and s.labels[-1] == ("le", "+Inf")
        ]
        count_samples = [s for s in family.samples if s.suffix == "_count"]
        assert inf_samples[0].value == count_samples[0].value == 4

    def test_histogram_non_monotonic_bucket_raises_under_debug(self):
        if not __debug__:  # pragma: no cover - only runs under `python -O`
            pytest.skip("assertions disabled (python -O)")
        with pytest.raises(AssertionError):
            histogram(
                "labdog_test_duration_seconds",
                "help",
                bounds=(1.0, 5.0),
                series=[((), [3, 1], 3, 1.0)],
            )

    def test_render_emits_help_and_type_once(self):
        family = histogram(
            "labdog_test_duration_seconds",
            "help text",
            bounds=(1.0,),
            series=[((), [1], 1, 1.0)],
        )
        body = render([family])
        assert body.count("# HELP labdog_test_duration_seconds") == 1
        assert body.count("# TYPE labdog_test_duration_seconds histogram") == 1
        assert body.endswith("\n")


# ---------------------------------------------------------------------------
# Status endpoint
# ---------------------------------------------------------------------------


class TestMetricsStatusEndpoint:
    async def test_unauthenticated_gets_401(self, client):
        resp = await client.get("/api/metrics/status")
        assert resp.status_code == 401

    async def test_authenticated_gets_200(self, metrics_enabled, regular_user_client):
        resp = await regular_user_client.get("/api/metrics/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["path"] == "/metrics"
        assert data["scrape_url"].endswith("/metrics")
        assert data["cache_ttl_seconds"] == pytest.approx(15.0)
        assert data["authenticated"] is False
        assert "enabled = true" in data["toml_snippet"]
        assert "LABDOG_METRICS__ENABLED" in data["env_snippet"]

    async def test_reflects_disabled_state(self, regular_user_client):
        resp = await regular_user_client.get("/api/metrics/status")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False
