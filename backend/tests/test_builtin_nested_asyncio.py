"""BUG-79: a built-in dispatcher must not run a Celery task in-process.

Every built-in dispatcher is ``asyncio.run(_something_async(...))``. Two
of them then reached for another Celery task and ran it *in-process* with
``.apply()`` — and those task bodies are themselves
``asyncio.run(...)``. Python refuses to start a second event loop inside
a running one, so both raised

    RuntimeError: asyncio.run() cannot be called from a running event loop

on every single invocation: `_builtin.collect_state` (via
``facts.collect_host_facts``) and `_builtin.sync` (via
``host_sync_orchestrator.run_host_sync``). ``.apply()`` swallows the
exception into an ``EagerResult``, so it surfaced as an ordinary failed
ActionHostRun with a confusing message rather than a traceback.

Both paths were unreachable until BUG-78 was fixed — the claim-or-defer
deferred every built-in behind its own parent run, so the body never
executed. Found together, on a live instance, the moment the first fix
let control through.

The fix is not to stop running in-process — that is the intent, one
worker slot, no fan-out grandchild — it is to await the coroutine the
task wraps instead of the task.
"""

import ast
import inspect
from pathlib import Path

import pytest

import app.tasks.builtin_dispatchers as dispatchers

pytestmark = pytest.mark.integration


def _module_source(mod) -> str:
    return Path(inspect.getfile(mod)).read_text()


class TestNoCeleryTaskIsRunInProcess:
    """A source-level check, deliberately.

    The runtime failure needs a reachable host and a real event loop to
    reproduce, and it is a whole class of mistake rather than one line:
    any future ``.apply()`` added to this module reintroduces it. Naming
    the pattern is what keeps it out.
    """

    def test_builtin_dispatchers_never_calls_apply(self):
        tree = ast.parse(_module_source(dispatchers))
        offenders = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "apply"
        ]
        assert not offenders, (
            f"builtin_dispatchers.py calls .apply() at line(s) {offenders}. "
            "Running a Celery task in-process executes its asyncio.run() "
            "wrapper inside the loop this module already runs under, which "
            "raises RuntimeError every time. Await the underlying coroutine "
            "instead."
        )

    def test_the_dispatchers_are_still_the_asyncio_run_shape(self):
        """The check above only matters while this is true; if the
        dispatchers stop owning the loop, revisit rather than delete."""
        tree = ast.parse(_module_source(dispatchers))
        wrappers = [
            n.name
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name.startswith("run_builtin_")
        ]
        assert wrappers, "no run_builtin_* task wrappers found"
        src = _module_source(dispatchers)
        assert "asyncio.run(" in src


class TestTheCoroutinesAreImportable:
    """The fix depends on private coroutines staying where they are; a
    rename would otherwise fail only at runtime, on a real host."""

    def test_the_facts_coroutine_exists(self):
        from app.tasks.facts import _collect_host_facts_async

        assert inspect.iscoroutinefunction(_collect_host_facts_async)

    def test_the_sync_orchestrator_coroutine_and_workspace_helpers_exist(self):
        from app.tasks.host_sync_orchestrator import (
            _async_run,
            _cleanup_tmpfs,
            _make_tmpfs_workspace,
        )

        assert inspect.iscoroutinefunction(_async_run)
        assert callable(_make_tmpfs_workspace)
        assert callable(_cleanup_tmpfs)
