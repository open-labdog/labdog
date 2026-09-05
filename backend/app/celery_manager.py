"""Manages the Celery worker subprocesses of the main LabDog process.

Two workers, not one. ``action_orchestrator.run_action`` blocks in
``result.join()`` waiting for per-host children it published itself, so
sharing a pool with those children is a self-deadlock: with the default
``concurrency=4``, four schedules landing on the same cron minute — and
``0 3 * * *`` is the obvious default — take all four slots, leaving none
for any child. Nothing progresses until the orchestrator's 12h soft limit
fires, and then four runs finalise ``partial`` having touched zero hosts.

Giving the orchestrator its own queue and its own worker makes the
starvation impossible by construction rather than by sizing: the pool an
orchestrator waits on is never the pool it occupies. A chord would also
remove the join, but the join is what carries the mid-run cancel poll and
the batched parallelism, both of which a chord drops.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from types import TracebackType

from app.config import settings

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[1]


#: Which queues each worker consumes, keyed by worker name.
#:
#: ``work`` runs everything and carries beat. ``orchestrator`` exists only
#: so ``run_action`` never waits on a pool it is itself occupying — see the
#: module docstring. Nothing else may be routed to it: an orchestrator slot
#: held by unrelated work reintroduces exactly the starvation the split
#: removes.
WORKER_QUEUES: dict[str, tuple[str, ...]] = {
    "work": ("default", "long_running"),
    "orchestrator": ("orchestrator",),
}


#: The manager this process owns, or None.
#:
#: ``/health/ready`` needs to know whether the Celery children are alive,
#: and the manager is created by ``app.__main__`` rather than by the
#: FastAPI app, so there is nothing on ``app.state`` to read. Set by
#: ``start()`` and cleared by ``stop()``.
#:
#: Only meaningful in the process that spawned the workers. Under
#: ``--workers N`` uvicorn forks, and a forked child sees None — which is
#: why the health check reports "not supervised here" rather than "down"
#: when it is unset. A single-worker deployment, which is what the
#: container ships, always has it.
_active_manager: CeleryManager | None = None


def active_manager() -> CeleryManager | None:
    """The CeleryManager supervising this process's workers, if any."""
    return _active_manager


class CeleryManager:
    """Spawn and manage the Celery worker subprocesses."""

    #: Every queue some worker consumes.
    #:
    #: Named rather than inlined because it is half of a contract: a task
    #: published to a queue absent from this tuple is accepted by the broker
    #: and never executed. `tests/test_task_routing.py` imports this to check
    #: the other half — that every registered task routes into it.
    QUEUES: tuple[str, ...] = tuple(q for queues in WORKER_QUEUES.values() for q in queues)

    def __init__(self) -> None:
        self._processes: dict[str, subprocess.Popen[bytes]] = {}

    def _command(self, name: str, queues: tuple[str, ...]) -> list[str]:
        cmd = [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "app.tasks",
            "worker",
        ]
        if name == "work":
            # Beat belongs to exactly one worker; two schedulers against one
            # RedBeat keyspace would double-fire every periodic task.
            cmd += ["--beat", "--scheduler", "redbeat.RedBeatScheduler"]
        concurrency = (
            settings.celery.orchestrator_concurrency
            if name == "orchestrator"
            else settings.celery.concurrency
        )
        cmd += [
            f"--max-tasks-per-child={settings.celery.max_tasks_per_child}",
            f"--concurrency={concurrency}",
            "-Q",
            ",".join(queues),
            # Distinct node names: two workers sharing one would collide in
            # the broker's control/mingle channels, and `celery inspect`
            # would report whichever answered first.
            "-n",
            f"{name}@%h",
            f"--loglevel={settings.logging.level}",
        ]
        return cmd

    def start(self) -> None:
        """Spawn one Celery worker subprocess per entry in WORKER_QUEUES."""
        global _active_manager
        _active_manager = self
        for name, queues in WORKER_QUEUES.items():
            cmd = self._command(name, queues)
            logger.info("Starting Celery worker %r: %s", name, " ".join(cmd))
            self._processes[name] = subprocess.Popen(
                cmd,
                cwd=BACKEND_DIR,
                stderr=subprocess.STDOUT,
            )
            logger.info("Celery worker %r started (pid=%d)", name, self._processes[name].pid)

    def stop(self, timeout: int = 60) -> None:
        """SIGTERM every worker, wait up to *timeout* seconds each, then SIGKILL.

        Terminate all of them first and only then wait: signalling serially
        would give the last worker `timeout × (n-1)` seconds less to drain.
        """
        global _active_manager
        if _active_manager is self:
            _active_manager = None
        live = [(n, p) for n, p in self._processes.items() if p.poll() is None]
        for name, proc in live:
            logger.info("Stopping Celery worker %r (pid=%d) ...", name, proc.pid)
            proc.terminate()
        for name, proc in live:
            try:
                proc.wait(timeout=timeout)
                logger.info("Celery worker %r exited gracefully", name)
            except subprocess.TimeoutExpired:
                logger.warning(
                    "Celery worker %r did not exit within %ds, sending SIGKILL",
                    name,
                    timeout,
                )
                proc.kill()
                proc.wait()
                logger.info("Celery worker %r killed", name)

    def is_alive(self) -> bool:
        """True only if every worker is still running.

        All-or-nothing on purpose: a dead orchestrator worker means no
        action run ever starts, which is not a healthy process.
        """
        return bool(self._processes) and all(p.poll() is None for p in self._processes.values())

    def dead_workers(self) -> list[str]:
        """Names of workers that have exited. Empty when healthy."""
        return sorted(n for n, p in self._processes.items() if p.poll() is not None)

    # -- context manager --------------------------------------------------

    def __enter__(self) -> CeleryManager:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.stop()
