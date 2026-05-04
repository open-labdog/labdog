"""Unit tests for orchestrator outcome helpers.

Pure in-memory tests — no DB, no Celery, no ansible-runner. Inputs
are constructed by hand to exercise the contract.
"""

from __future__ import annotations

import pytest

from app.ansible_runtime.composer import CANONICAL_ORDER
from app.ansible_runtime.outcomes import (
    aggregate_module_outcomes,
    determine_modules_to_run,
)

# ---------------------------------------------------------------------------
# determine_modules_to_run
# ---------------------------------------------------------------------------


def test_determine_modules_none_returns_all_canonical():
    assert determine_modules_to_run(None) == CANONICAL_ORDER


def test_determine_modules_subset_returned_in_canonical_order():
    # Caller supplied them out of order; we should sort them by
    # CANONICAL_ORDER (packages before firewall).
    out = determine_modules_to_run(["firewall", "packages"])
    assert out == ["packages", "firewall"]


def test_determine_modules_single_filter():
    assert determine_modules_to_run(["firewall"]) == ["firewall"]


def test_determine_modules_empty_filter_raises():
    with pytest.raises(ValueError, match="module_filter must not be empty"):
        determine_modules_to_run([])


def test_determine_modules_unknown_module_raises():
    with pytest.raises(ValueError, match="unknown module"):
        determine_modules_to_run(["firewall", "nope"])


def test_determine_modules_duplicate_filter_dedupes():
    # Defensive: caller supplies duplicates, we still produce one entry.
    assert determine_modules_to_run(["firewall", "firewall"]) == ["firewall"]


def test_determine_modules_custom_all_modules_overrides_default():
    out = determine_modules_to_run(["a", "b"], all_modules=["b", "a", "c"])
    assert out == ["b", "a"]


# ---------------------------------------------------------------------------
# aggregate_module_outcomes
# ---------------------------------------------------------------------------


def _evt(tags: list[str], failed: bool = False, unreachable: bool = False) -> dict:
    return {"tags": tags, "failed": failed, "unreachable": unreachable}


def test_aggregate_no_events_all_modules_no_tasks():
    out = aggregate_module_outcomes([], ["firewall", "services"])
    assert out == {"firewall": "no_tasks", "services": "no_tasks"}


def test_aggregate_one_ok_event_per_module_marks_in_sync():
    events = [_evt(["firewall"]), _evt(["services"])]
    out = aggregate_module_outcomes(events, ["firewall", "services"])
    assert out == {"firewall": "in_sync", "services": "in_sync"}


def test_aggregate_failed_event_marks_module_error():
    events = [_evt(["firewall"]), _evt(["services"], failed=True)]
    out = aggregate_module_outcomes(events, ["firewall", "services"])
    assert out == {"firewall": "in_sync", "services": "error"}


def test_aggregate_unreachable_event_marks_module_error():
    events = [_evt(["firewall"], unreachable=True)]
    out = aggregate_module_outcomes(events, ["firewall"])
    assert out == {"firewall": "error"}


def test_aggregate_error_is_sticky_even_with_later_success():
    # Module hits one failure, then later tasks succeed — module
    # stays "error" because partial-failure means the module is in a
    # bad state, not "in_sync".
    events = [_evt(["packages"], failed=True), _evt(["packages"])]
    out = aggregate_module_outcomes(events, ["packages"])
    assert out == {"packages": "error"}


def test_aggregate_event_with_irrelevant_tag_is_ignored():
    # Event tagged with a module not in modules_run shouldn't pollute
    # results.
    events = [_evt(["rogue"]), _evt(["firewall"])]
    out = aggregate_module_outcomes(events, ["firewall"])
    assert out == {"firewall": "in_sync"}


def test_aggregate_event_with_multiple_tags_counts_for_each_relevant():
    # A task tagged with two modules counts toward both.
    events = [_evt(["firewall", "services"], failed=True)]
    out = aggregate_module_outcomes(events, ["firewall", "services", "packages"])
    assert out == {"firewall": "error", "services": "error", "packages": "no_tasks"}


def test_aggregate_module_not_in_run_list_absent_from_output():
    events = [_evt(["firewall"]), _evt(["services"])]
    out = aggregate_module_outcomes(events, ["firewall"])
    assert out == {"firewall": "in_sync"}
    assert "services" not in out


def test_aggregate_event_with_no_tags_ignored():
    events = [_evt([])]
    out = aggregate_module_outcomes(events, ["firewall"])
    assert out == {"firewall": "no_tasks"}


def test_aggregate_event_missing_tags_key_treated_as_empty():
    # Defensive: a malformed event without a "tags" key should not crash.
    events = [{"failed": True}]
    out = aggregate_module_outcomes(events, ["firewall"])
    assert out == {"firewall": "no_tasks"}
