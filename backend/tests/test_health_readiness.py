"""BUG-72: /health said "ok" no matter what was broken.

It returned ``{"status": "ok"}`` unconditionally — it never touched the
database, Redis, or the Celery children. ``CeleryManager.is_alive()``
existed and was called from nowhere. So when the Celery subprocess died,
the container stayed healthy forever while nothing executed a single
task: no sync, no drift check, no scheduled action, and no signal that
anything was wrong. The failure is silent by construction, which is the
worst property a health check can have.

Liveness and readiness are now separate questions. ``/health`` and
``/health/live`` stay constant — "is the process answering" is what a
restart policy should act on. ``/health/ready`` asks whether this
instance can actually do its job, and says which component cannot.
"""

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.integration


@contextmanager
def _redis_up():
    """Pretend Redis answers.

    The suite runs without a Redis, so a test about the database or the
    workers would otherwise be reading a 503 caused by something it is not
    testing. Tests that are *about* Redis do not use this.
    """
    client = MagicMock()
    client.ping = AsyncMock(return_value=True)
    client.aclose = AsyncMock()
    with patch("redis.asyncio.from_url", return_value=client):
        yield


@contextmanager
def _celery_up():
    manager = MagicMock()
    manager.is_alive.return_value = True
    with patch("app.celery_manager.active_manager", return_value=manager):
        yield


class TestLivenessStaysCheap:
    @pytest.mark.parametrize("path", ["/health", "/health/live"])
    async def test_it_is_a_constant(self, client, path):
        resp = await client.get(path)
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    async def test_the_original_path_still_works(self, client):
        """Anything already pointed at /health must not break."""
        assert (await client.get("/health")).status_code == 200


class TestReadinessChecksRealComponents:
    async def test_a_healthy_instance_is_ready(self, client):
        resp = await client.get("/health/ready")
        body = resp.json()
        assert body["components"]["database"] == "ok", body
        # Redis and Celery depend on the environment the suite runs in;
        # the database is the one this test can rely on being up.

    async def test_it_names_every_component_it_checked(self, client):
        body = (await client.get("/health/ready")).json()
        assert set(body["components"]) == {"database", "redis", "celery"}

    async def test_a_dead_celery_worker_makes_it_not_ready(self, client):
        """The whole point. This is the state that used to report healthy
        forever while nothing ran."""
        manager = MagicMock()
        manager.is_alive.return_value = False
        manager.dead_workers.return_value = ["orchestrator"]

        with _redis_up(), patch("app.celery_manager.active_manager", return_value=manager):
            resp = await client.get("/health/ready")

        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "not_ready"
        assert "orchestrator" in body["components"]["celery"]

    async def test_a_live_celery_worker_is_ok(self, client):
        manager = MagicMock()
        manager.is_alive.return_value = True

        with _redis_up(), patch("app.celery_manager.active_manager", return_value=manager):
            resp = await client.get("/health/ready")

        body = resp.json()
        assert body["components"]["celery"] == "ok"
        assert resp.status_code == 200, body
        assert body["status"] == "ready"

    async def test_an_unsupervised_process_says_so_rather_than_failing(self, client):
        """`--no-celery` is a development choice, not a fault, and a forked
        uvicorn worker does not own the subprocesses. Reporting "down"
        there would fail the probe on a healthy dev run."""
        with _redis_up(), patch("app.celery_manager.active_manager", return_value=None):
            resp = await client.get("/health/ready")

        assert resp.status_code == 200
        assert resp.json()["components"]["celery"] == "not_supervised_here"

    async def test_a_broken_database_makes_it_not_ready(self, client):
        def _boom(*a, **kw):
            raise RuntimeError("no database")

        with (
            _redis_up(),
            _celery_up(),
            patch("sqlalchemy.ext.asyncio.create_async_engine", side_effect=_boom),
        ):
            resp = await client.get("/health/ready")

        assert resp.status_code == 503
        body = resp.json()
        assert body["components"]["database"] == "error"
        assert body["components"]["redis"] == "ok", (
            "a failing component must not drag the others down with it"
        )

    async def test_a_broken_redis_makes_it_not_ready(self, client):
        client_mock = MagicMock()
        client_mock.ping = AsyncMock(side_effect=RuntimeError("no redis"))
        client_mock.aclose = AsyncMock()

        with _celery_up(), patch("redis.asyncio.from_url", return_value=client_mock):
            resp = await client.get("/health/ready")

        assert resp.status_code == 503
        assert resp.json()["components"]["redis"] == "error"


class TestTheManagerRegistersItself:
    def test_start_publishes_the_manager_and_stop_clears_it(self):
        import app.celery_manager as cm
        from app.celery_manager import CeleryManager, active_manager

        cm._active_manager = None  # the registry is process-global
        mgr = CeleryManager()
        assert active_manager() is None

        with patch("app.celery_manager.subprocess.Popen") as popen:
            popen.return_value.pid = 1
            popen.return_value.poll.return_value = None
            mgr.start()
            assert active_manager() is mgr
            mgr.stop(timeout=0)

        assert active_manager() is None
