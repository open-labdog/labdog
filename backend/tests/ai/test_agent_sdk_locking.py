"""Every database touch in the SDK runner must hold the lock.

Observed in production. A session ran nine turns and one command, then
died, and what the operator saw was

    This session is in 'prepared' state; no further SQL can be emitted
    within this transaction.

which is the third-order symptom. The actual fault was two coroutines on
one ``AsyncSession``: the SDK dispatches tool callbacks concurrently with
the message stream, so ``_execute_tool`` and the driver loop interleaved.
The lock existed, but covered only the tool side. In order, the log shows
a tool's ``Session.add()`` landing mid-flush (a warning SQLAlchemy prints
before carrying on), then asyncpg's *another operation is in progress* on
the next query, and only then the message above, raised by the error
handler trying to record the failure on a transaction that was already
unusable.

Checked structurally rather than by exercising the race: a concurrency
test that passes proves the race did not happen *this time*, which is
exactly the guarantee a race does not offer.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

from app.ai.agent_sdk import runner as runner_module

#: Methods allowed to touch the database without holding the lock, and why.
#: ``run`` does its own work either side of ``_exchange``, where no client
#: exists and therefore no tool callback can fire.
UNLOCKED_BY_DESIGN = {"run"}


def _methods_touching_db() -> dict[str, ast.FunctionDef]:
    source = textwrap.dedent(inspect.getsource(runner_module.AgentSDKRunner))
    tree = ast.parse(source)
    class_def = tree.body[0]
    found: dict[str, ast.FunctionDef] = {}
    for node in class_def.body:
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        for sub in ast.walk(node):
            # self.db.<anything> — the only handle on the session here.
            if (
                isinstance(sub, ast.Attribute)
                and isinstance(sub.value, ast.Attribute)
                and sub.value.attr == "db"
                and isinstance(sub.value.value, ast.Name)
                and sub.value.value.id == "self"
            ):
                found[node.name] = node
                break
    return found


def _mentions_lock(node: ast.AST) -> bool:
    return any(isinstance(sub, ast.Attribute) and sub.attr == "_db_lock" for sub in ast.walk(node))


class TestLockDiscipline:
    def test_every_db_touching_method_takes_the_lock(self) -> None:
        offenders = [
            name
            for name, node in _methods_touching_db().items()
            if name not in UNLOCKED_BY_DESIGN and not _mentions_lock(node)
        ]
        assert not offenders, (
            "these methods use self.db without holding _db_lock; the SDK runs tool "
            f"callbacks concurrently with the message stream: {offenders}"
        )

    def test_the_driver_loop_is_covered(self) -> None:
        """Named explicitly because this is the one that was missed. The
        tool side was locked from the start; the driver's own writes were
        not."""
        methods = _methods_touching_db()
        assert "_exchange" in methods, "the driver loop should be writing to the database"
        assert _mentions_lock(methods["_exchange"])

    def test_the_tool_path_is_covered(self) -> None:
        methods = _methods_touching_db()
        assert _mentions_lock(methods["_execute_tool"])

    def test_the_allowlist_is_justified_not_a_dumping_ground(self) -> None:
        """A regression here would most likely arrive as a new name added
        to the allowlist to make this file pass."""
        assert UNLOCKED_BY_DESIGN == {"run"}

    def test_the_check_can_actually_fail(self) -> None:
        """Guards the detector itself: if the AST walk stopped matching
        ``self.db``, every test above would pass vacuously."""
        assert _methods_touching_db(), "no methods detected as touching self.db"
        fake = ast.parse("async def f(self):\n    await self.db.commit()\n").body[0]
        assert not _mentions_lock(fake)
