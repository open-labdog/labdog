"""Where periodic schedules get registered, and when.

Fifteen task modules each registered their RedBeat entry at *import*
time, inside a bare ``try: ... except Exception: pass``. Three problems
came out of that (BUG-70):

1. **Every import rewrote the entry.** ``RedBeatSchedulerEntry.save()``
   with no ``last_run_at`` resets ``due_at`` to ``now + run_every``. Both
   the FastAPI app and the Celery worker import these modules, and every
   process restart did it again — so a deployment that restarts more
   often than once a day meant the daily jobs never fired, *ever*. The
   audit-log and SSH-transcript pruners are daily. So is AI snapshot
   retention. All three back settings an operator can see and change.

2. **It ran in processes that have no scheduler.** The API process does
   not run beat. Neither does the orchestrator worker. Registering from
   all of them is at best redundant and at worst the rewrite above.

3. **Failures were invisible.** Six of the modules swallowed the
   exception with ``pass``, so a Redis that was up but rejecting writes
   produced an install with no periodic tasks and no log line saying so.

Registration now happens once, from ``beat_init``, in the one process
that owns the schedule — and :func:`ensure_entry` writes only when the
entry would actually change, so a restart leaves ``due_at`` alone and a
job that was due in ten minutes is still due in ten minutes.
"""

from __future__ import annotations

import importlib
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

#: Module-level functions that register schedules. The convention is
#: already in use across every task module; the pattern is matched rather
#: than a hand-maintained list so adding a module cannot silently miss.
#: ``tests/test_beat_registry.py`` fails if a module that uses RedBeat has
#: no function matching this, which is what makes a rename loud.
_REGISTRAR_RE = re.compile(r"^_register_\w*schedules?$")


def ensure_entry(name: str, task: str, run_every_seconds: float, app: Any, **kwargs) -> bool:
    """Create or update one RedBeat entry. Returns True if it wrote.

    Writes only when the stored entry is missing or actually differs.
    An unconditional ``save()`` resets ``due_at`` to ``now + run_every``,
    which is how a daily job on a frequently-restarted deployment never
    ran at all.
    """
    from celery.schedules import schedule
    from redbeat import RedBeatSchedulerEntry

    new_schedule = schedule(run_every=run_every_seconds)

    try:
        existing = RedBeatSchedulerEntry.from_key(f"redbeat:{name}", app=app)
    except Exception:
        # Missing (KeyError) or unreadable — either way, write it.
        existing = None

    # Compare the schedule objects, not raw numbers: celery normalises
    # ``run_every`` to a timedelta, so ``60`` and ``timedelta(seconds=60)``
    # are the same schedule and must not look like a change.
    if existing is not None and existing.task == task and existing.schedule == new_schedule:
        logger.debug("beat entry %r already current; leaving due_at alone", name)
        return False

    RedBeatSchedulerEntry(name=name, task=task, schedule=new_schedule, app=app, **kwargs).save()
    logger.info(
        "beat entry %r %s (every %ss)",
        name,
        "updated" if existing is not None else "created",
        run_every_seconds,
    )
    return True


def registrars_in(module) -> list[str]:
    """Names of the schedule-registering functions a module exposes."""
    return sorted(n for n in dir(module) if _REGISTRAR_RE.match(n) and callable(getattr(module, n)))


def register_all(app) -> dict[str, str]:
    """Run every task module's schedule registration. Returns per-module status.

    Failures are logged and collected rather than raised: one module with
    a broken schedule must not stop the other fourteen from being
    registered, and a beat process that refuses to start is worse than
    one missing a job. The return value is what the tests assert on.
    """
    results: dict[str, str] = {}
    for module_path in app.conf.include:
        try:
            module = importlib.import_module(module_path)
        except Exception:
            logger.exception("beat registration: could not import %s", module_path)
            results[module_path] = "import-failed"
            continue

        names = registrars_in(module)
        if not names:
            continue
        for fn_name in names:
            key = f"{module_path}.{fn_name}"
            try:
                getattr(module, fn_name)()
                results[key] = "ok"
            except Exception:
                # Logged, not swallowed. An install with no periodic tasks
                # and no log line saying why is the failure mode this
                # whole module exists to remove.
                logger.exception("beat registration failed: %s", key)
                results[key] = "failed"
    return results
