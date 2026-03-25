"""Manages Celery worker+beat as a subprocess of the main Barricade process."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from types import TracebackType

from app.config import settings

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[1]


class CeleryManager:
    """Spawn and manage a Celery worker+beat subprocess."""

    def __init__(self) -> None:
        self._process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        """Spawn the Celery worker+beat subprocess."""
        cmd = [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "app.tasks",
            "worker",
            "--beat",
            "--scheduler",
            "redbeat.RedBeatScheduler",
            f"--max-tasks-per-child={settings.celery.max_tasks_per_child}",
            f"--concurrency={settings.celery.concurrency}",
            "-Q",
            "default,long_running",
            f"--loglevel={settings.logging.level}",
        ]
        logger.info("Starting Celery worker+beat: %s", " ".join(cmd))
        self._process = subprocess.Popen(
            cmd,
            cwd=BACKEND_DIR,
            stderr=subprocess.STDOUT,
        )
        logger.info("Celery subprocess started (pid=%d)", self._process.pid)

    def stop(self, timeout: int = 60) -> None:
        """Send SIGTERM to celery, wait up to *timeout* seconds, then SIGKILL."""
        if self._process is None or self._process.poll() is not None:
            return
        pid = self._process.pid
        logger.info("Stopping Celery subprocess (pid=%d) ...", pid)
        self._process.terminate()
        try:
            self._process.wait(timeout=timeout)
            logger.info("Celery subprocess exited gracefully")
        except subprocess.TimeoutExpired:
            logger.warning(
                "Celery subprocess did not exit within %ds, sending SIGKILL", timeout
            )
            self._process.kill()
            self._process.wait()
            logger.info("Celery subprocess killed")

    def is_alive(self) -> bool:
        """Return True if the celery subprocess is still running."""
        if self._process is None:
            return False
        return self._process.poll() is None

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
