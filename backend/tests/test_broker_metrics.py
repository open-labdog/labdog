"""``labdog_broker_queue_depth`` / ``labdog_broker_reachable``.

Everything else in the exporter comes from PostgreSQL, and a scrape that
reached the database has already proved the database is up. The broker is a
second failure domain in an unauthenticated request path, which is why it
was scoped out of the exporter's first release and why the timeout and the
unreachable behaviour are what these tests are about.

The load-bearing assertion is the negative one: when the broker does not
answer, the depth gauges must be **absent**, not zero. A zero reads as "the
queues are empty" — the opposite of what is known — and would silence the
"work is piling up" alert at exactly the moment it should fire.
"""

import asyncio

import pytest

from app.celery_manager import CeleryManager
from app.metrics import broker


class _FakePipeline:
    def __init__(self, lengths: list[int], delay: float = 0.0):
        self._lengths = lengths
        self._delay = delay
        self.calls: list[str] = []

    def llen(self, name: str) -> None:
        self.calls.append(name)

    async def execute(self) -> list[int]:
        if self._delay:
            await asyncio.sleep(self._delay)
        return self._lengths


class _FakeRedis:
    def __init__(self, lengths: list[int], delay: float = 0.0):
        self._lengths = lengths
        self._delay = delay
        self.closed = False
        self.pipe: _FakePipeline | None = None

    def pipeline(self) -> _FakePipeline:
        self.pipe = _FakePipeline(self._lengths, self._delay)
        return self.pipe

    async def aclose(self) -> None:
        self.closed = True


def _patch_redis(monkeypatch, fake: _FakeRedis) -> None:
    import redis.asyncio as redis_async

    monkeypatch.setattr(redis_async, "from_url", lambda *_a, **_k: fake)


class TestAHealthyBroker:
    async def test_it_reports_every_queue_a_worker_consumes(self, monkeypatch):
        fake = _FakeRedis([3, 0, 7])
        _patch_redis(monkeypatch, fake)

        snap = await broker.probe()

        assert snap.reachable is True
        assert set(snap.depths) == set(CeleryManager.QUEUES), (
            "a queue no worker consumes is a task that never runs; the depth "
            "families must cover the same tuple test_task_routing asserts against"
        )

    async def test_depths_line_up_with_the_queues_asked_for(self, monkeypatch):
        fake = _FakeRedis([3, 0, 7])
        _patch_redis(monkeypatch, fake)

        snap = await broker.probe()

        assert snap.depths == dict(zip(CeleryManager.QUEUES, [3, 0, 7], strict=True))
        assert fake.pipe is not None
        assert fake.pipe.calls == list(CeleryManager.QUEUES)

    async def test_it_uses_one_round_trip(self, monkeypatch):
        """Reading queues one at a time would multiply the timeout budget."""
        fake = _FakeRedis([1, 1, 1])
        _patch_redis(monkeypatch, fake)

        await broker.probe()

        assert fake.pipe is not None
        assert len(fake.pipe.calls) == len(CeleryManager.QUEUES)

    async def test_the_connection_is_closed(self, monkeypatch):
        fake = _FakeRedis([0, 0, 0])
        _patch_redis(monkeypatch, fake)

        await broker.probe()

        assert fake.closed is True


class TestAnUnreachableBroker:
    async def test_a_slow_broker_times_out_rather_than_blocking(self, monkeypatch):
        fake = _FakeRedis([1, 1, 1], delay=5.0)
        _patch_redis(monkeypatch, fake)

        snap = await asyncio.wait_for(broker.probe(timeout=0.05), timeout=2.0)

        assert snap.reachable is False
        assert snap.depths is None, "unknown is not zero"

    async def test_a_refused_connection_is_not_an_exception(self, monkeypatch):
        """``probe`` never raises — a scrape must not 500 because Redis is down."""

        def _boom(*_a, **_k):
            raise ConnectionError("connection refused")

        import redis.asyncio as redis_async

        monkeypatch.setattr(redis_async, "from_url", _boom)

        snap = await broker.probe()

        assert snap == broker.BrokerSnapshot(reachable=False, depths=None)

    async def test_the_connection_is_closed_even_when_the_read_fails(self, monkeypatch):
        fake = _FakeRedis([0, 0, 0])

        async def _boom():
            raise ConnectionError("gone mid-read")

        _patch_redis(monkeypatch, fake)
        original = fake.pipeline

        def _pipeline():
            pipe = original()
            pipe.execute = _boom
            return pipe

        fake.pipeline = _pipeline

        snap = await broker.probe()

        assert snap.reachable is False
        assert fake.closed is True, "a failed read must not leak the connection"


class TestTheExportedFamilies:
    @staticmethod
    def _families(snapshot: broker.BrokerSnapshot, monkeypatch):
        from app.metrics import collector

        async def _probe(*_a, **_k):
            return snapshot

        monkeypatch.setattr(collector.broker_probe, "probe", _probe)
        collector.reset_cache()

    async def test_a_healthy_broker_emits_both_families(self, db, monkeypatch):
        from app.metrics import collector

        self._families(
            broker.BrokerSnapshot(reachable=True, depths={"default": 4, "long_running": 0}),
            monkeypatch,
        )
        names = {f.name for f in await collector.collect(db)}

        assert "labdog_broker_reachable" in names
        assert "labdog_broker_queue_depth" in names

    async def test_an_unreachable_broker_omits_depth_but_keeps_reachable(self, db, monkeypatch):
        """The assertion this whole module exists for."""
        from app.metrics import collector

        self._families(broker.BrokerSnapshot(reachable=False, depths=None), monkeypatch)
        families = {f.name: f for f in await collector.collect(db)}

        assert "labdog_broker_queue_depth" not in families, (
            "zero-filling an unreachable broker would read as 'queues are empty' "
            "and silence the alert it should trigger"
        )
        reachable = families["labdog_broker_reachable"]
        assert [s.value for s in reachable.samples] == [0.0]

    async def test_reachable_is_one_when_it_answered(self, db, monkeypatch):
        from app.metrics import collector

        self._families(broker.BrokerSnapshot(reachable=True, depths={"default": 0}), monkeypatch)
        families = {f.name: f for f in await collector.collect(db)}

        assert [s.value for s in families["labdog_broker_reachable"].samples] == [1.0]


@pytest.fixture(autouse=True)
def _clear_metrics_cache():
    from app.metrics import collector

    collector.reset_cache()
    yield
    collector.reset_cache()
