"""BUG-70: registering a schedule must not reset when it is next due.

Fifteen task modules registered their RedBeat entry at *import*, inside a
bare ``try: ... except Exception: pass``. Three consequences:

* ``RedBeatSchedulerEntry.save()`` with no ``last_run_at`` resets
  ``due_at`` to ``now + run_every``. Both the API process and the Celery
  worker import these modules, and every restart did it again — so a
  deployment restarting more often than once a day meant the daily jobs
  **never fired**. Audit-log pruning, SSH-transcript pruning and AI
  snapshot retention are all daily, and all three back settings an
  operator can see and change.
* It ran in processes with no scheduler at all.
* Six modules swallowed failures, so a Redis rejecting writes produced an
  install with no periodic tasks and no log line saying why.

Registration now runs once, from ``beat_init``, and writes only when the
entry would actually change.
"""

import ast
import inspect
from pathlib import Path
from unittest.mock import patch

import pytest

from app.tasks import celery_app
from app.tasks.beat_registry import ensure_entry, register_all, registrars_in


class _FakeEntry:
    saved: list = []

    def __init__(self, name=None, task=None, schedule=None, app=None, **kw):
        self.name, self.task, self.schedule, self.app = name, task, schedule, app

    def save(self):
        type(self).saved.append(self.name)


@pytest.fixture
def redbeat(monkeypatch):
    _FakeEntry.saved = []
    stored: dict = {}

    class _Entry(_FakeEntry):
        deleted: list = []

        @classmethod
        def from_key(cls, key, app=None):
            if key not in stored:
                raise KeyError(key)
            return stored[key]

        def delete(self):
            type(self).deleted.append(self.name)

    monkeypatch.setattr("redbeat.RedBeatSchedulerEntry", _Entry)
    return stored, _Entry


class TestWritingOnlyWhenSomethingChanged:
    def test_a_missing_entry_is_created(self, redbeat):
        _, entry_cls = redbeat
        assert ensure_entry("x", "app.tasks.x", 60, celery_app) is True
        assert entry_cls.saved == ["x"]

    def test_an_unchanged_entry_is_left_alone(self, redbeat):
        """The whole bug. Re-saving resets due_at, so a daily job on a
        restart-happy deployment is perpetually 24h away."""
        from celery.schedules import schedule

        stored, entry_cls = redbeat
        stored["redbeat:x"] = _FakeEntry(
            name="x", task="app.tasks.x", schedule=schedule(run_every=60)
        )

        assert ensure_entry("x", "app.tasks.x", 60, celery_app) is False
        assert entry_cls.saved == [], "an unchanged entry was rewritten"

    def test_seconds_and_a_timedelta_are_the_same_schedule(self, redbeat):
        """celery normalises run_every to a timedelta; comparing raw
        numbers would call every entry changed and rewrite it — which is
        the bug, restored."""
        from datetime import timedelta

        from celery.schedules import schedule

        stored, entry_cls = redbeat
        stored["redbeat:x"] = _FakeEntry(
            name="x", task="app.tasks.x", schedule=schedule(run_every=timedelta(seconds=60))
        )

        assert ensure_entry("x", "app.tasks.x", 60, celery_app) is False
        assert entry_cls.saved == []

    def test_a_changed_interval_is_written(self, redbeat):
        from celery.schedules import schedule

        stored, entry_cls = redbeat
        stored["redbeat:x"] = _FakeEntry(
            name="x", task="app.tasks.x", schedule=schedule(run_every=60)
        )

        assert ensure_entry("x", "app.tasks.x", 900, celery_app) is True
        assert entry_cls.saved == ["x"]

    def test_a_changed_task_name_is_written(self, redbeat):
        """A task renamed across an upgrade must not leave beat firing at
        the old name."""
        from celery.schedules import schedule

        stored, _ = redbeat
        stored["redbeat:x"] = _FakeEntry(
            name="x", task="app.tasks.old", schedule=schedule(run_every=60)
        )

        assert ensure_entry("x", "app.tasks.new", 60, celery_app) is True

    def test_an_unreadable_entry_is_rewritten_rather_than_skipped(self, redbeat):
        """`from_key` raising anything means we do not know the current
        state; writing is the safe answer, skipping is not."""
        _, entry_cls = redbeat

        def _boom(key, app=None):
            raise RuntimeError("redis said no")

        entry_cls.from_key = staticmethod(_boom)
        assert ensure_entry("x", "app.tasks.x", 60, celery_app) is True


class TestNothingRegistersAtImport:
    """The property, checked at source level — a re-added call at module
    scope is exactly how this regresses."""

    def test_no_task_module_calls_a_registrar_at_import(self):
        offenders = []
        for path in sorted(Path("app/tasks").glob("*.py")):
            tree = ast.parse(path.read_text())
            for node in tree.body:  # module scope only
                for call in ast.walk(node):
                    if (
                        isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Name)
                        and call.func.id.startswith("_register_")
                        and "schedule" in call.func.id
                    ):
                        offenders.append(f"{path.name}:{call.lineno}")
        assert not offenders, (
            f"schedules registered at import time in {offenders}. "
            "Every importing process rewrites due_at, so daily jobs never fire. "
            "Register from beat_init instead."
        )

    def test_beat_init_is_wired(self):
        import app.tasks as tasks

        assert hasattr(tasks, "_register_beat_schedules_on_beat_start")


class TestEveryModuleIsStillFound:
    """``register_all`` matches registrar functions by name convention.
    A rename would otherwise drop a schedule silently."""

    def test_every_redbeat_module_exposes_a_registrar(self):
        import importlib

        missing = []
        for module_path in celery_app.conf.include:
            try:
                module = importlib.import_module(module_path)
            except Exception:
                continue
            src = Path(inspect.getfile(module)).read_text()
            if "RedBeatSchedulerEntry" not in src and "ensure_entry" not in src:
                continue
            if module_path.endswith("beat_registry"):
                continue
            if not registrars_in(module):
                missing.append(module_path)
        assert not missing, (
            f"{missing} register RedBeat entries but expose no _register_*schedule(s) "
            "function, so register_all() will never call them"
        )

    def test_register_all_reports_per_module_status(self, redbeat):
        # `redbeat` stubs RedBeatSchedulerEntry: two registrars call
        # `from_key` directly (the legacy-entry cleanups), and against a
        # real absent Redis those sit through celery's 20×1s retry.
        with patch("app.tasks.beat_registry.ensure_entry", return_value=True):
            results = register_all(celery_app)
        assert results, "no registrars were found at all"
        assert all(isinstance(v, str) for v in results.values())

    def test_one_broken_module_does_not_stop_the_others(self, redbeat):
        """A beat process that refuses to start is worse than one missing
        a job — and this is what six modules used to hide with `pass`."""
        import app.tasks.drift as drift
        import app.tasks.sync_sweeper as sweeper

        with (
            patch("app.tasks.beat_registry.ensure_entry", return_value=True),
            patch.object(drift, "_register_beat_schedule", side_effect=RuntimeError("nope")),
        ):
            results = register_all(celery_app)

        assert results["app.tasks.drift._register_beat_schedule"] == "failed"
        assert results["app.tasks.sync_sweeper._register_beat_schedule"] == "ok", (
            f"a later module stopped registering: {results}"
        )
        assert sweeper is not None  # imported for the name above
