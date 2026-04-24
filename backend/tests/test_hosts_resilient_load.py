"""Regression tests for the defensive host-loading path in
``app.api.hosts._load_hosts_resilient``.

Background: a single corrupt row (e.g. an enum value that's no longer
in the Python enum after a schema simplification) made
``select(Host)`` → ``scalars().all()`` raise ``LookupError``, which
500'd the UI's hosts-summary endpoint — so 16 rows in the DB but
zero visible in the UI on the 10.10.101.5 deployment. The helper
falls back to per-row materialisation when the bulk path fails,
skips and logs rows that can't be loaded, and returns the survivors.
"""

from __future__ import annotations

import logging

import pytest
from sqlalchemy.exc import NoResultFound

from app.api.hosts import _load_hosts_resilient


class _FakeResult:
    """Async-compat stub of ``sqlalchemy.ext.asyncio.AsyncResult``
    with only the methods the helper touches."""

    def __init__(
        self,
        *,
        scalar_items: list | None = None,
        all_rows: list | None = None,
        raise_on_scalars: Exception | None = None,
        raise_on_scalar_one: Exception | None = None,
    ):
        self._scalar_items = scalar_items or []
        self._all_rows = all_rows or []
        self._raise_scalars = raise_on_scalars
        self._raise_one = raise_on_scalar_one

    def scalars(self):
        if self._raise_scalars is not None:
            raise self._raise_scalars

        class _Bag:
            def __init__(self, items):
                self._items = items

            def all(self):
                return self._items

        return _Bag(self._scalar_items)

    def scalar_one(self):
        if self._raise_one is not None:
            raise self._raise_one
        if not self._scalar_items:
            raise NoResultFound
        return self._scalar_items[0]

    def all(self):
        return self._all_rows


class _FakeDB:
    """Scriptable stand-in for the ``AsyncSession`` that the helper
    uses. Each call to ``execute()`` pops the next queued response."""

    def __init__(self, responses: list[_FakeResult]):
        self._responses = list(responses)
        self.calls = 0

    async def execute(self, _query):
        self.calls += 1
        if not self._responses:
            raise RuntimeError(f"FakeDB got an unexpected execute() call #{self.calls}")
        return self._responses.pop(0)


class _FakeHost:
    """Minimal stand-in for an ORM Host — the helper just accumulates
    whatever ``scalar_one()`` returns; no attribute access here."""

    def __init__(self, host_id: int, hostname: str):
        self.id = host_id
        self.hostname = hostname


@pytest.mark.asyncio
async def test_happy_path_single_bulk_query():
    """When bulk load succeeds, the helper returns the rows directly
    and performs exactly one query — no fallback."""
    hosts = [_FakeHost(1, "alpha"), _FakeHost(2, "beta")]
    db = _FakeDB([_FakeResult(scalar_items=hosts)])

    result = await _load_hosts_resilient(db)

    assert [h.hostname for h in result] == ["alpha", "beta"]
    assert db.calls == 1


@pytest.mark.asyncio
async def test_bulk_failure_falls_back_per_row(caplog):
    """When bulk scalars().all() raises, each row is loaded
    individually; the bulk failure is logged once."""
    rows = [_FakeHost(1, "alpha"), _FakeHost(2, "beta")]
    db = _FakeDB(
        [
            # Call 1: bulk select(Host) — fail with a LookupError
            # mimicking a stale enum value
            _FakeResult(raise_on_scalars=LookupError("packaged enum mismatch")),
            # Call 2: select(Host.id).order_by(Host.id) — returns id rows
            _FakeResult(all_rows=[(1,), (2,)]),
            # Calls 3 & 4: per-row select(Host).where(...)
            _FakeResult(scalar_items=[rows[0]]),
            _FakeResult(scalar_items=[rows[1]]),
        ]
    )

    with caplog.at_level(logging.ERROR, logger="app.api.hosts"):
        result = await _load_hosts_resilient(db)

    assert [h.hostname for h in result] == ["alpha", "beta"]
    assert db.calls == 4
    assert any("bulk load failed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_bad_rows_are_skipped_and_logged(caplog):
    """In the fallback path, an individual row that can't be loaded
    is logged with its id and skipped; the good rows are returned."""
    good_a = _FakeHost(1, "alpha")
    good_c = _FakeHost(3, "gamma")
    db = _FakeDB(
        [
            _FakeResult(raise_on_scalars=LookupError("simulated bulk failure")),
            _FakeResult(all_rows=[(1,), (2,), (3,)]),
            _FakeResult(scalar_items=[good_a]),
            _FakeResult(raise_on_scalar_one=LookupError("bad enum on id=2")),
            _FakeResult(scalar_items=[good_c]),
        ]
    )

    with caplog.at_level(logging.ERROR, logger="app.api.hosts"):
        result = await _load_hosts_resilient(db)

    assert [h.id for h in result] == [1, 3]
    assert any(
        "skipping unloadable host id=2" in r.message for r in caplog.records
    )


@pytest.mark.asyncio
async def test_empty_db_returns_empty_list():
    db = _FakeDB([_FakeResult(scalar_items=[])])
    result = await _load_hosts_resilient(db)
    assert result == []
    assert db.calls == 1
