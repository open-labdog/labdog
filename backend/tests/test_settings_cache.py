"""BUG-61: settings read from synchronous code must reflect the database.

Ten operator-visible settings silently did nothing in every Celery task and
SSH path. ``get_setting_sync`` built its own connection URL with
``.replace("+asyncpg", "+psycopg2").replace("postgresql+psycopg2",
"postgresql")`` — the second replace undoing the first — leaving a bare
``postgresql://`` that needs psycopg2, a driver this project does not depend
on. The ``ModuleNotFoundError`` was swallowed by a blanket ``except
Exception`` that returned the hardcoded default. Nothing warmed the shared
cache in a worker either, so it could not save them.

The affected settings were: ``ssh.connect_timeout`` (twice),
``ssh.idle_timeout_seconds``, ``discovery.max_concurrent``,
``ansible.playbook_timeout`` (three call sites),
``logging.audit_retention_days``, ``actions.preflight_enabled`` and
``ai.wall_clock_seconds``.

The replacement splits the two halves: an async refresh that fills the cache
from a session the caller already has, and a synchronous reader that consults
only the cache. These tests are the contract between them.
"""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from app import settings_service as ss


@pytest.fixture(autouse=True)
def clean_cache():
    ss.invalidate_cache()
    yield
    ss.invalidate_cache()


def _db_returning(rows):
    """A session whose one SELECT yields ``rows`` of (key, value)."""
    db = MagicMock()
    result = MagicMock()
    result.all.return_value = rows
    db.execute = AsyncMock(return_value=result)
    return db


class TestTheSyncReaderNeverTouchesTheDatabase:
    """The property that makes the reader safe to call from async code.

    ``build_ssh_common_args`` and the asyncssh connect path are both reached
    from coroutines. A reader that opened its own connection would block the
    event loop on every playbook and every SSH connect — which is the other
    reason the old implementation had to go, beyond simply not working.
    """

    def test_a_cold_read_falls_back_to_the_default_without_connecting(self, caplog):
        assert ss.get_setting_cached("ssh.connect_timeout") == ss.get_default("ssh.connect_timeout")
        assert "cold" in caplog.text.lower()

    async def test_a_warm_read_returns_the_stored_value(self):
        await ss.refresh_settings_cache(_db_returning([("ssh.connect_timeout", "42")]))
        assert ss.get_setting_cached("ssh.connect_timeout") == "42"
        assert ss.get_setting_cached_typed("ssh.connect_timeout") == 42


class TestRefreshPopulatesEveryKnownKey:
    async def test_keys_without_a_row_are_cached_as_their_default(self):
        await ss.refresh_settings_cache(_db_returning([]))
        for key in ss.SETTING_DEFINITIONS:
            assert key in ss._cache, f"{key} missing from the cache after a refresh"
            assert ss.get_setting_cached(key) == ss.get_default(key)

    async def test_stored_values_win_over_defaults(self):
        await ss.refresh_settings_cache(_db_returning([("ansible.playbook_timeout", "3600")]))
        assert ss.get_setting_cached_typed("ansible.playbook_timeout") == 3600
        assert ss.get_setting_cached_typed("ssh.connect_timeout") == int(
            ss.get_default("ssh.connect_timeout")
        )

    async def test_one_query_regardless_of_how_many_settings_exist(self):
        db = _db_returning([])
        await ss.refresh_settings_cache(db)
        assert db.execute.await_count == 1


class TestEnsureIsTtlGated:
    """``ensure_settings_cache`` sits on the session-open path, so it has to
    be cheap: one query per TTL per process, not one per session."""

    async def test_a_cold_cache_is_refreshed(self):
        db = _db_returning([])
        await ss.ensure_settings_cache(db)
        assert db.execute.await_count == 1

    async def test_a_warm_cache_is_left_alone(self):
        await ss.refresh_settings_cache(_db_returning([]))
        db = _db_returning([])
        await ss.ensure_settings_cache(db)
        assert db.execute.await_count == 0, "refreshed a cache that was still fresh"

    async def test_an_expired_cache_is_refreshed(self, monkeypatch):
        await ss.refresh_settings_cache(_db_returning([("ssh.connect_timeout", "42")]))
        # Age every entry past the TTL rather than sleeping.
        monkeypatch.setattr(
            ss, "_cache", {k: (v, ts - ss._CACHE_TTL - 1) for k, (v, ts) in ss._cache.items()}
        )
        assert ss.settings_cache_is_stale()

        db = _db_returning([("ssh.connect_timeout", "99")])
        await ss.ensure_settings_cache(db)
        assert db.execute.await_count == 1
        assert ss.get_setting_cached("ssh.connect_timeout") == "99"


class TestTheDeadSyncReaderIsGone:
    """A source-level assertion, because the failure it caused was silence.

    Re-introducing a synchronous getter that opens its own connection would
    reproduce BUG-61 exactly, and nothing about the resulting behaviour looks
    like a bug from the outside — the settings simply stop working.
    """

    def test_no_module_defines_or_calls_a_sync_settings_getter(self):
        import pathlib

        root = pathlib.Path(ss.__file__).parent
        offenders = [
            str(p.relative_to(root))
            for p in root.rglob("*.py")
            if "get_setting_sync" in p.read_text()
        ]
        assert offenders == [], f"get_setting_sync is back in: {offenders}"

    async def test_the_cached_reader_never_opens_a_connection(self, monkeypatch):
        """Asserted behaviourally rather than by grepping the source: what
        matters is that no engine is built, not how the code reads."""
        import sqlalchemy

        def _explode(*_a, **_kw):
            raise AssertionError("get_setting_cached tried to open a database connection")

        monkeypatch.setattr(sqlalchemy, "create_engine", _explode)

        await ss.refresh_settings_cache(_db_returning([("ssh.connect_timeout", "42")]))
        assert ss.get_setting_cached("ssh.connect_timeout") == "42"
        # And on the cold path, which is the one that used to connect.
        ss.invalidate_cache()
        assert ss.get_setting_cached("ssh.connect_timeout") == ss.get_default("ssh.connect_timeout")


class TestCacheTimestampsAreFresh:
    async def test_refresh_stamps_entries_with_now(self):
        before = time.time()
        await ss.refresh_settings_cache(_db_returning([]))
        for _value, ts in ss._cache.values():
            assert ts >= before
