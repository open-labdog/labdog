"""
LabDog application entrypoint.

Usage:
    python -m app              # start API server
    python -m app --workers 4  # start with multiple workers

All server settings (host, port, TLS) are read from labdog.toml.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from pathlib import Path
from types import FrameType

import uvicorn

from app.config import settings

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _run_migrations() -> None:
    """Run Alembic migrations up to 'head'."""
    from alembic.command import upgrade
    from alembic.config import Config

    alembic_ini = BACKEND_DIR / "alembic.ini"
    logger.info("Running database migrations (alembic upgrade head) ...")
    try:
        cfg = Config(str(alembic_ini))
        upgrade(cfg, "head")
        logger.info("Database migrations completed successfully")
    except Exception:
        logger.exception("Database migration failed")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="LabDog API Server")
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of uvicorn worker processes (default: 1)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload (development only)",
    )
    parser.add_argument(
        "--skip-migrate",
        action="store_true",
        help="Skip automatic database migrations on startup",
    )
    parser.add_argument(
        "--no-celery",
        action="store_true",
        help="Do not start Celery worker+beat subprocess (development only)",
    )
    args = parser.parse_args()

    # -- T4: Startup migrations -------------------------------------------
    if not args.skip_migrate:
        _run_migrations()

    # -- T5: Celery subprocess manager ------------------------------------
    celery_mgr = None
    if not args.no_celery:
        from app.celery_manager import CeleryManager

        celery_mgr = CeleryManager()
        celery_mgr.start()

    # Install signal handlers that stop celery before the process exits.
    def _shutdown(signum: int, _frame: FrameType | None) -> None:
        sig_name = signal.Signals(signum).name
        logger.info("Received %s, shutting down ...", sig_name)
        if celery_mgr is not None:
            celery_mgr.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    # -- Uvicorn ----------------------------------------------------------
    kwargs: dict = {
        "app": "app.main:app",
        "host": settings.server.host,
        "port": settings.server.port,
        "forwarded_allow_ips": settings.server.forwarded_allow_ips,
        "log_level": settings.logging.level,
        "workers": args.workers,
        "reload": args.reload,
        "ws": "wsproto",
    }

    # TLS — only if both cert and key are provided
    if settings.tls.cert_file and settings.tls.key_file:
        kwargs["ssl_certfile"] = settings.tls.cert_file
        kwargs["ssl_keyfile"] = settings.tls.key_file

    try:
        uvicorn.run(**kwargs)
    finally:
        if celery_mgr is not None:
            celery_mgr.stop()


if __name__ == "__main__":
    main()
