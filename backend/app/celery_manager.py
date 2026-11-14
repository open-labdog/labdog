"""Manages the Celery subprocesses of the main unbork process.

Three of them: two workers and the beat scheduler. Beat is separate for
the reason given at :data:`BEAT`. The workers are two for the reason
below.

Two workers, not one. ``action_orchestrator.run_action`` blocks waiting
for per-host children it published itself, so sharing a pool with those
children is a self-deadlock: with the default
``concurrency=4``, four schedules landing on the same cron minute — and
``0 3 * * *`` is the obvious default — take all four slots, leaving none
for any child. Nothing progresses until the orchestrator's 12h soft limit
fires, and then four runs finalise ``partial`` having touched zero hosts.

Giving the orchestrator its own queue and its own worker makes the
starvation impossible by construction rather than by sizing: the pool an
orchestrator waits on is never the pool it occupies. A chord would also
remove the wait, but the wait is what carries the mid-run cancel poll, the
batched parallelism and the heartbeat a dead orchestrator is noticed by,
all of which a chord drops.
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


#: Which queues each worker consumes, keyed by worker name, for the
#: default ``all`` role (one container runs everything).
#:
#: ``work`` runs everything except the orchestrator. ``orchestrator``
#: exists only so ``run_action`` never waits on a pool it is itself
#: occupying — see the module docstring. Nothing else may be routed to
#: it: an orchestrator slot held by unrelated work reintroduces exactly
#: the starvation the split removes.
#:
#: ``drift`` carries the per-host drift checks the periodic sweeps fan
#: out (``app.tasks.drift_sweep``). In the ``all`` role ``work`` consumes
#: it too; a split deployment gives it its own ``worker-drift`` container
#: so a sync backlog in ``long_running`` cannot delay drift, and the
#: other way round.
#:
#: ``actions`` carries the per-host (and group-wide) half of every action
#: run, published by the orchestrator (``CHILD_QUEUE`` in
#: ``app.tasks.action_orchestrator``). Same reasoning as ``drift``: in a
#: split deployment the ``worker-actions`` role serves it, so a bulk-sync
#: backlog in ``long_running`` cannot hold action runs back.
WORKER_QUEUES: dict[str, tuple[str, ...]] = {
    "work": ("default", "long_running", "drift", "actions"),
    "orchestrator": ("orchestrator",),
}

#: The scheduler's own process name. Not in WORKER_QUEUES because it
#: consumes nothing; it only publishes.
#:
#: Beat used to ride inside the ``work`` worker as ``--beat``, which
#: Celery runs as a *child* of that worker. The child's tick loop catches
#: only KeyboardInterrupt and SystemExit, so the first Redis restart
#: killed it — RedBeat's ``tick()`` begins with an unguarded
#: ``lock.extend()`` that raises ConnectionError while Redis is down and
#: LockNotOwnedError once it is back with the lock key gone — and nothing
#: restarted it. The worker itself was fine, so ``is_alive()`` said so,
#: and ``/health/ready`` stayed green while every periodic job had
#: stopped: drift sweeps, scheduled actions, retention, alert polling
#: (BUG-83). Running beat as its own supervised subprocess is what puts
#: it under the same ``poll()`` as the workers.
BEAT = "beat"


#: Every role ``python -m app --role`` accepts, and the Celery processes
#: each one runs (worker name -> queues; ``None`` is the scheduler).
#:
#: ``all`` is the default and is exactly the single-container install:
#: both workers plus beat, next to the API. The others split those same
#: processes across containers that run the same image, so worker
#: capacity scales with ``docker compose up --scale``. ``api`` runs no
#: Celery process at all. Node names stay ``<worker>@%h``; ``%h`` is the
#: container hostname, so replicas never collide, and the
#: ``orchestrator@`` prefix that ``_is_orchestrator_worker`` keys off is
#: kept.
ROLE_PROCESSES: dict[str, dict[str, tuple[str, ...] | None]] = {
    "all": {**WORKER_QUEUES, BEAT: None},
    "api": {},
    "beat": {BEAT: None},
    "orchestrator": {"orchestrator": WORKER_QUEUES["orchestrator"]},
    "worker": {"work": ("default", "long_running")},
    "worker-drift": {"drift": ("drift",)},
    "worker-actions": {"actions": ("actions",)},
}

#: Roles that serve HTTP. Only these may run migrations.
API_ROLES = frozenset({"all", "api"})


def parse_roles(value: str | None) -> tuple[str, ...]:
    """Split a ``--role`` / ``LABDOG_ROLE`` value into known roles.

    Empty means ``all``. ``all`` cannot be combined with anything, since
    it already runs every process; two schedulers against one RedBeat
    keyspace would double-fire every periodic task.
    """
    roles = tuple(dict.fromkeys(r.strip() for r in (value or "all").split(",") if r.strip()))
    if not roles:
        return ("all",)
    unknown = [r for r in roles if r not in ROLE_PROCESSES]
    if unknown:
        raise ValueError(
            f"unknown role(s) {', '.join(unknown)}; expected any of {', '.join(ROLE_PROCESSES)}"
        )
    if "all" in roles and len(roles) > 1:
        raise ValueError("role 'all' already runs every process and cannot be combined")
    return roles


def processes_for(roles: tuple[str, ...]) -> dict[str, tuple[str, ...] | None]:
    """The Celery processes a container running *roles* supervises."""
    out: dict[str, tuple[str, ...] | None] = {}
    for role in roles:
        out.update(ROLE_PROCESSES[role])
    return out


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
    QUEUES: tuple[str, ...] = tuple(
        dict.fromkeys(
            q
            for procs in ROLE_PROCESSES.values()
            for queues in procs.values()
            if queues
            for q in queues
        )
    )

    def __init__(
        self,
        processes: dict[str, tuple[str, ...] | None] | None = None,
        *,
        roles: tuple[str, ...] = ("all",),
    ) -> None:
        #: Worker name -> queues (``None`` = beat) this manager spawns.
        #: Defaults to the ``all`` role.
        self._plan = dict(processes) if processes is not None else processes_for(("all",))
        self._roles = roles
        self._processes: dict[str, subprocess.Popen[bytes]] = {}

    def _beat_command(self) -> list[str]:
        """The standalone scheduler.

        No ``--pidfile``: the CLI defaults to none, and a stale pidfile
        after a crash would refuse the very restart the supervisor
        exists to make possible. RedBeat keeps its schedule in Redis, so
        the ``--schedule`` file the CLI would otherwise write is unused.
        """
        return [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "app.tasks",
            "beat",
            # Not the stock RedBeatScheduler: that one dies on the first
            # Redis restart. See app.tasks.scheduler.
            "--scheduler",
            "app.tasks.scheduler.ResilientRedBeatScheduler",
            f"--loglevel={settings.logging.level}",
        ]

    def _command(self, name: str, queues: tuple[str, ...]) -> list[str]:
        cmd = [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "app.tasks",
            "worker",
        ]
        concurrency = {
            "orchestrator": settings.celery.orchestrator_concurrency,
            "drift": settings.celery.drift_concurrency,
            "actions": settings.celery.actions_concurrency,
        }.get(name, settings.celery.concurrency)
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

    def _spawn(self, name: str, cmd: list[str]) -> None:
        import os  # noqa: PLC0415

        logger.info("Starting Celery %r: %s", name, " ".join(cmd))
        # The worker_ready handlers read LABDOG_ROLE to know which siblings
        # share this container (and so this disk): see app.tasks.
        env = {**os.environ, "LABDOG_ROLE": ",".join(self._roles)}
        self._processes[name] = subprocess.Popen(
            cmd,
            cwd=BACKEND_DIR,
            stderr=subprocess.STDOUT,
            env=env,
        )
        logger.info("Celery %r started (pid=%d)", name, self._processes[name].pid)

    def start(self) -> None:
        """Spawn the planned workers, plus the scheduler if the role has it.

        Exactly one beat: two schedulers against one RedBeat keyspace
        would double-fire every periodic task. It is a peer of the
        workers here rather than a child of one of them so that its
        death is a dead entry in ``_processes`` — visible to
        ``is_alive()`` and named by ``dead_workers()`` — instead of a
        silent stop inside a worker that reports itself healthy.
        """
        global _active_manager
        _active_manager = self
        for name, queues in self._plan.items():
            if queues is None:
                self._spawn(name, self._beat_command())
            else:
                self._spawn(name, self._command(name, queues))

    def stop(self, timeout: int = 60) -> None:
        """SIGTERM every subprocess, wait up to *timeout* seconds each, then SIGKILL.

        Terminate all of them first and only then wait: signalling serially
        would give the last worker `timeout × (n-1)` seconds less to drain.
        """
        global _active_manager
        if _active_manager is self:
            _active_manager = None
        live = [(n, p) for n, p in self._processes.items() if p.poll() is None]
        for name, proc in live:
            logger.info("Stopping Celery %r (pid=%d) ...", name, proc.pid)
            proc.terminate()
        for name, proc in live:
            try:
                proc.wait(timeout=timeout)
                logger.info("Celery %r exited gracefully", name)
            except subprocess.TimeoutExpired:
                logger.warning(
                    "Celery %r did not exit within %ds, sending SIGKILL",
                    name,
                    timeout,
                )
                proc.kill()
                proc.wait()
                logger.info("Celery %r killed", name)

    def is_alive(self) -> bool:
        """True only if every subprocess — both workers and beat — is running.

        All-or-nothing on purpose: a dead orchestrator worker means no
        action run ever starts, and a dead beat means no periodic job
        ever fires. Neither is a healthy process.
        """
        return bool(self._processes) and all(p.poll() is None for p in self._processes.values())

    def wait_for_exit(self, poll_seconds: float = 2.0) -> str:
        """Block until any subprocess exits; return its name.

        For the Celery-only roles, where nothing else keeps the main
        process alive. One child dying ends the container (and lets the
        orchestrator restart it) rather than leaving a half-alive one.
        """
        import time  # noqa: PLC0415

        while True:
            dead = self.dead_workers()
            if dead:
                return dead[0]
            time.sleep(poll_seconds)

    def dead_workers(self) -> list[str]:
        """Names of subprocesses that have exited. Empty when healthy."""
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
