"""Unit tests for the shared action-deadline arithmetic.

Pure logic — no DB. The registry and settings lookups are patched so
the math is exercised in isolation.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app.tasks import action_timeouts as at

# ---------------------------------------------------------------------------
# effective_playbook_timeout: max(setting, floor)
# ---------------------------------------------------------------------------


def test_effective_timeout_setting_wins_when_higher():
    with patch("app.settings_service.get_setting_cached_typed", return_value=3600):
        assert at.effective_playbook_timeout(1800) == 3600


def test_effective_timeout_floor_wins_when_higher():
    with patch("app.settings_service.get_setting_cached_typed", return_value=300):
        # linux-os-upgrade's 5400 floor exceeds the setting's 3600 ceiling.
        assert at.effective_playbook_timeout(5400) == 5400


def test_effective_timeout_no_floor_uses_setting():
    with patch("app.settings_service.get_setting_cached_typed", return_value=300):
        assert at.effective_playbook_timeout(None) == 300


def test_effective_timeout_setting_error_falls_back():
    with patch(
        "app.settings_service.get_setting_cached_typed",
        side_effect=RuntimeError("no db"),
    ):
        # Falls back to FALLBACK_TIMEOUT_SECONDS, then floored by the action.
        assert at.effective_playbook_timeout(None) == at.FALLBACK_TIMEOUT_SECONDS
        assert at.effective_playbook_timeout(5400) == 5400


# ---------------------------------------------------------------------------
# per_host_deadline_seconds: effective + verify + grace
# ---------------------------------------------------------------------------


def test_per_host_deadline_uses_action_verify_and_floor():
    action = SimpleNamespace(
        playbook_timeout_seconds=1800, verify_timeout_seconds=180, destructive=False
    )
    with (
        patch("app.settings_service.get_setting_cached_typed", return_value=300),
        patch.object(at, "_registry_lookup", return_value=action),
    ):
        # max(300, 1800) + 180 + 900
        assert at.per_host_deadline_seconds("linux-upgrade") == 1800 + 180 + 900


def test_per_host_deadline_adds_the_snapshot_lock_wait_for_destructive_actions():
    action = SimpleNamespace(
        playbook_timeout_seconds=1800, verify_timeout_seconds=180, destructive=True
    )
    with (
        patch("app.settings_service.get_setting_cached_typed", return_value=300),
        patch.object(at, "_registry_lookup", return_value=action),
        patch.object(at, "snapshot_lock_wait_budget", return_value=1200),
    ):
        # A host can sit behind a locked VM for the whole wait before its
        # playbook starts, so the wait comes on top of the rest.
        assert at.per_host_deadline_seconds("linux-upgrade") == 1800 + 180 + 900 + 1200


def test_per_host_deadline_registry_miss_uses_fallbacks():
    with (
        patch("app.settings_service.get_setting_cached_typed", return_value=300),
        patch.object(at, "_registry_lookup", return_value=None),
        patch.object(at, "snapshot_lock_wait_budget", return_value=1200),
    ):
        # setting(300) + FALLBACK_VERIFY(300) + grace(900) + lock wait, since
        # a missing action might have been destructive.
        expected = 300 + at.FALLBACK_VERIFY_TIMEOUT_SECONDS + at.ENVELOPE_GRACE_SECONDS + 1200
        assert at.per_host_deadline_seconds("gone") == expected


def test_snapshot_lock_wait_budget_reads_the_setting_in_minutes():
    with patch("app.settings_service.get_setting_cached_typed", return_value=45):
        assert at.snapshot_lock_wait_budget() == 45 * 60
    with patch("app.settings_service.get_setting_cached_typed", return_value=0):
        assert at.snapshot_lock_wait_budget() == 0


# ---------------------------------------------------------------------------
# run_deadline_seconds: batch math
# ---------------------------------------------------------------------------


def test_run_deadline_parallelism_all_at_once_single_batch():
    with patch.object(at, "per_host_deadline_seconds", return_value=1000):
        # parallelism <= 0 → one batch of all hosts.
        assert at.run_deadline_seconds("k", host_count=5, parallelism=0) == (
            1 * 1000 + at.RUN_DEADLINE_SLACK_SECONDS
        )


def test_run_deadline_parallelism_one_is_sequential():
    with patch.object(at, "per_host_deadline_seconds", return_value=1000):
        # parallelism 1 → 5 sequential batches.
        assert at.run_deadline_seconds("k", host_count=5, parallelism=1) == (
            5 * 1000 + at.RUN_DEADLINE_SLACK_SECONDS
        )


def test_run_deadline_parallelism_n_ceils_batches():
    with patch.object(at, "per_host_deadline_seconds", return_value=1000):
        # 5 hosts, 2 at a time → ceil(5/2) = 3 batches.
        assert at.run_deadline_seconds("k", host_count=5, parallelism=2) == (
            3 * 1000 + at.RUN_DEADLINE_SLACK_SECONDS
        )


def test_run_deadline_zero_hosts_treated_as_one():
    with patch.object(at, "per_host_deadline_seconds", return_value=1000):
        assert at.run_deadline_seconds("k", host_count=0, parallelism=1) == (
            1000 + at.RUN_DEADLINE_SLACK_SECONDS
        )
