"""The AI actions' deadline must track ``ai.wall_clock_seconds``.

Two failure modes this pins, pulling in opposite directions:

* Too short — the orchestrator's Celery limit overrides the task's own
  decorator, so a deadline under the wall-clock cap hard-kills the worker
  mid-session and orphans the run in ``running``.
* Too long — the action sweeper uses the same number to reap an orphaned
  run, and until it does, that host's queue stays blocked. A generous
  constant would have hidden the first problem by creating the second.

Deriving the floor from the setting satisfies both, which is only true so
long as nobody replaces it with a constant again.
"""

from __future__ import annotations

import pytest

from app.tasks import action_timeouts
from app.tasks.action_timeouts import (
    ENVELOPE_GRACE_SECONDS,
    FALLBACK_VERIFY_TIMEOUT_SECONDS,
    per_host_deadline_seconds,
)

AI_KEYS = ["_builtin.ai_task", "_builtin.ai_task_group"]


@pytest.fixture
def wall_clock(monkeypatch):
    """Set ai.wall_clock_seconds without touching the database."""

    def _set(seconds: int) -> None:
        monkeypatch.setattr(action_timeouts, "_ai_wall_clock_floor", lambda: seconds, raising=True)

    return _set


@pytest.mark.parametrize("action_key", AI_KEYS)
@pytest.mark.parametrize("seconds", [900, 1800, 3600, 21600])
def test_deadline_exceeds_the_wall_clock_cap(wall_clock, action_key, seconds):
    """A session that runs to its own cap must not be killed first."""
    wall_clock(seconds)
    assert per_host_deadline_seconds(action_key) > seconds


@pytest.mark.parametrize("action_key", AI_KEYS)
def test_deadline_scales_with_the_setting(wall_clock, action_key):
    """Raising the cap widens the deadline — no manual second edit."""
    wall_clock(900)
    at_default = per_host_deadline_seconds(action_key)
    wall_clock(7200)
    at_raised = per_host_deadline_seconds(action_key)
    assert at_raised > at_default


@pytest.mark.parametrize("action_key", AI_KEYS)
def test_deadline_is_not_wildly_generous(wall_clock, action_key):
    """The margin over the cap stays bounded.

    An orphaned run holds its host's queue until the sweeper reaps it at
    this deadline, so the headroom has to be enough to cover the envelope
    and no more.
    """
    wall_clock(900)
    expected_margin = FALLBACK_VERIFY_TIMEOUT_SECONDS + ENVELOPE_GRACE_SECONDS
    assert per_host_deadline_seconds(action_key) <= 900 + expected_margin


def test_non_ai_actions_are_unaffected(wall_clock):
    """The AI floor must not widen every other action's deadline."""
    wall_clock(21600)
    assert per_host_deadline_seconds("_builtin.drift_check") < 21600
