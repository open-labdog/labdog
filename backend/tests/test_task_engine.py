"""BUG-156: a task's ``task_session()`` blocks share one engine.

``task_session()`` used to build, connect and dispose a whole engine per
block: 28 ms against 0.6 ms, and a new Postgres backend each time. Inside
``with_task_engine`` — which every Celery task wrapper now runs its body
under — the blocks take pooled connections from one engine opened for that
task's event loop.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from app import db as app_db
from app.db import task_session, with_task_engine


async def _backend_pid() -> int:
    async with task_session() as session:
        return await session.scalar(text("SELECT pg_backend_pid()"))


async def test_blocks_in_one_task_reuse_one_connection(pg_url):
    async def body():
        return [await _backend_pid() for _ in range(3)]

    pids = await with_task_engine(body())

    assert len(set(pids)) == 1


async def test_blocks_outside_a_task_each_get_their_own_engine(pg_url):
    first, second = await _backend_pid(), await _backend_pid()

    assert first != second


async def test_nested_blocks_get_separate_connections(pg_url):
    async def body():
        async with task_session() as outer:
            outer_pid = await outer.scalar(text("SELECT pg_backend_pid()"))
            async with task_session() as inner:
                inner_pid = await inner.scalar(text("SELECT pg_backend_pid()"))
        return outer_pid, inner_pid

    outer_pid, inner_pid = await with_task_engine(body())

    assert outer_pid != inner_pid


async def test_the_engine_is_gone_once_the_task_ends(pg_url):
    seen = []

    async def body():
        seen.append(app_db._shared_task_engine())
        return "done"

    assert await with_task_engine(body()) == "done"
    assert seen[0] is not None
    assert app_db._shared_task_engine() is None


async def test_a_nested_with_task_engine_keeps_the_outer_engine(pg_url):
    async def inner():
        return app_db._shared_task_engine()

    async def outer():
        mine = app_db._shared_task_engine()
        return mine, await with_task_engine(inner())

    mine, theirs = await with_task_engine(outer())

    assert mine is theirs


async def test_another_loop_never_gets_this_loops_engine(pg_url):
    """A context variable is copied into ``asyncio.to_thread``; an engine
    whose connections belong to this loop must not be used from another."""

    def in_a_thread():
        async def body():
            return app_db._shared_task_engine(), await _backend_pid()

        return asyncio.run(body())

    async def body():
        return await asyncio.to_thread(in_a_thread)

    engine_seen, pid = await with_task_engine(body())

    assert engine_seen is None
    assert isinstance(pid, int)


def test_every_task_body_runs_under_with_task_engine():
    """A new task that calls ``asyncio.run(body())`` directly would go back
    to an engine per block without anything failing."""
    import ast
    from pathlib import Path

    offenders = []
    for path in sorted((Path(app_db.__file__).parent / "tasks").glob("*.py")):
        if path.name == "__init__.py":  # worker_ready uses the API session factory
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "run"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in ("asyncio", "_asyncio")
            ):
                arg = node.args[0] if node.args else None
                wrapped = (
                    isinstance(arg, ast.Call)
                    and isinstance(arg.func, ast.Name)
                    and arg.func.id == "with_task_engine"
                )
                if not wrapped:
                    offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, f"asyncio.run without with_task_engine at {offenders}"
