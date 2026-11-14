"""
LabDog application entrypoint.

Usage:
    python -m app                       # everything in one process tree (role "all")
    python -m app --workers 4           # same, with several uvicorn workers
    python -m app --role api            # API only; runs migrations
    python -m app --role worker         # Celery worker on default,long_running
    python -m app --role worker-drift   # Celery worker on drift
    python -m app --role orchestrator   # action-run orchestrator worker
    python -m app --role beat           # the scheduler; exactly one per deployment

``--role`` takes a comma list and can also be set as ``LABDOG_ROLE``.
See app.celery_manager.ROLE_PROCESSES.

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
    # Extensions keep their own version table; see app.plugins.
    from app.plugins import run_plugin_migrations  # noqa: PLC0415

    run_plugin_migrations()


def _wait_for_schema(poll_seconds: float = 5.0) -> None:
    """Block until the database is at this image's Alembic head.

    Celery-only roles never migrate (only ``api`` / ``all`` may), so a
    worker that starts before the API has upgraded the schema would run
    new code against old tables. Waiting is cheaper than diagnosing that.
    """
    import asyncio  # noqa: PLC0415
    import time  # noqa: PLC0415

    from alembic.config import Config  # noqa: PLC0415
    from alembic.script import ScriptDirectory  # noqa: PLC0415
    from sqlalchemy import text  # noqa: PLC0415
    from sqlalchemy.ext.asyncio import create_async_engine  # noqa: PLC0415

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    heads = set(ScriptDirectory.from_config(cfg).get_heads())

    async def _current() -> set[str]:
        engine = create_async_engine(settings.database.url, pool_size=1, max_overflow=0)
        try:
            async with engine.connect() as conn:
                rows = await conn.execute(text("SELECT version_num FROM alembic_version"))
                return {r[0] for r in rows}
        finally:
            await engine.dispose()

    logged = False
    while True:
        try:
            current = asyncio.run(_current())
        except Exception as exc:  # noqa: BLE001 — not reachable or not created yet
            current = set()
            reason = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        else:
            reason = f"database at {sorted(current) or 'nothing'}"
        if current == heads:
            if logged:
                logger.info("Database schema is at head; starting")
            return
        if not logged:
            logger.info("Waiting for the api role to migrate to %s (%s)", sorted(heads), reason)
            logged = True
        time.sleep(poll_seconds)


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
    parser.add_argument(
        "--role",
        default=None,
        help="Comma list of roles to run: all (default), api, beat, orchestrator, "
        "worker, worker-drift. Also LABDOG_ROLE.",
    )
    args = parser.parse_args()

    import os  # noqa: PLC0415

    from app.celery_manager import API_ROLES, CeleryManager, parse_roles, processes_for

    try:
        roles = parse_roles(args.role or os.environ.get("LABDOG_ROLE"))
    except ValueError as exc:
        parser.error(str(exc))
    serves_api = any(r in API_ROLES for r in roles)
    plan = {} if args.no_celery else processes_for(roles)

    # -- T4: Startup migrations -------------------------------------------
    # Only a role that serves the API migrates; the others wait for it.
    if not args.skip_migrate:
        if serves_api:
            _run_migrations()
        else:
            _wait_for_schema()

    # -- T5: Celery subprocess manager ------------------------------------
    celery_mgr = None
    if plan:
        celery_mgr = CeleryManager(plan, roles=roles)
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

    if not serves_api:
        # Celery-only container: nothing else holds the process open, and
        # a dead child should end the container so it gets restarted.
        if celery_mgr is None:
            parser.error("nothing to run: a non-api role with --no-celery")
        dead = celery_mgr.wait_for_exit()
        logger.error("Celery %r exited; stopping the rest of this container", dead)
        celery_mgr.stop()
        sys.exit(1)

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
