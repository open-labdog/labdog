"""The action history tools: what LabDog ran on a host, and what it printed."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.ai.tools import TOOL_REGISTRY, tools_for_session
from app.ai.tools.base import ToolContext
from app.ai.tools.runs import GET_ACTION_RUN_OUTPUT, LIST_ACTION_RUNS
from app.models.action_run import ActionHostRun, ActionRun
from tests.conftest import create_host


def _ctx(db, host_ids: list[int]) -> ToolContext:
    return ToolContext(db=db, session_id=1, autonomy_level="read_only", target_host_ids=host_ids)


async def _run(db, host, *, key="linux-upgrade", status="failed", output="", age=0, error=None):
    run = ActionRun(
        action_key=key,
        action_version="1.0",
        host_id=host.id,
        parameters={},
        parallelism=1,
        status=status,
    )
    db.add(run)
    await db.flush()
    run.created_at = datetime.now(UTC) - timedelta(hours=age)
    hr = ActionHostRun(
        action_run_id=run.id,
        host_id=host.id,
        hostname=host.hostname,
        status=status,
        output=output,
        error_message=error,
    )
    db.add(hr)
    await db.flush()
    return run


def test_registered_and_read_only():
    for name in ("list_action_runs", "get_action_run_output"):
        assert TOOL_REGISTRY[name].classification == "read_only"
        assert name in {t.spec.name for t in tools_for_session("read_only")}


class TestListActionRuns:
    async def test_newest_first(self, db):
        host = await create_host(db)
        old = await _run(db, host, key="old-thing", status="succeeded", age=5)
        new = await _run(db, host, key="new-thing", error="verify failed")

        result = await LIST_ACTION_RUNS.run(_ctx(db, [host.id]), {"host_id": host.id})

        assert result.ok and result.target_host_id == host.id
        lines = result.content.splitlines()[1:]
        assert lines[0].startswith(f"- run_id={new.id} new-thing v1.0: host failed")
        assert "error: verify failed" in lines[0]
        assert lines[1].startswith(f"- run_id={old.id} old-thing")

    async def test_limit(self, db):
        host = await create_host(db)
        for i in range(4):
            await _run(db, host, age=i)
        result = await LIST_ACTION_RUNS.run(_ctx(db, [host.id]), {"host_id": host.id, "limit": 2})
        assert result.content.count("- run_id=") == 2

    async def test_out_of_scope(self, db):
        host, other = await create_host(db), await create_host(db)
        await _run(db, other)
        result = await LIST_ACTION_RUNS.run(_ctx(db, [host.id]), {"host_id": other.id})
        assert not result.ok and "not in scope" in result.content

    async def test_no_runs(self, db):
        host = await create_host(db)
        result = await LIST_ACTION_RUNS.run(_ctx(db, [host.id]), {"host_id": host.id})
        assert result.ok and "no action runs" in result.content


class TestGetActionRunOutput:
    LOG = "\n".join(f"line {i}" for i in range(1, 501))

    async def _call(self, db, host, run, **args):
        return await GET_ACTION_RUN_OUTPUT.run(
            _ctx(db, [host.id]), {"run_id": run.id, "host_id": host.id, **args}
        )

    async def test_pages(self, db):
        host = await create_host(db)
        run = await _run(db, host, output=self.LOG)

        first = await self._call(db, host, run)
        assert first.content.startswith(f"Run {run.id}, host {host.id}: 500 lines of output")
        assert "    1| line 1\n" in first.content and "  150| line 150\n" in first.content
        assert "line 151" not in first.content
        assert first.content.endswith("call again with start_line=151.")

        last = await self._call(db, host, run, start_line=451, max_lines=400)
        assert "  451| line 451" in last.content and "  500| line 500" in last.content
        assert last.content.endswith("That is the end of the log.")

    async def test_max_lines_is_capped(self, db):
        host = await create_host(db)
        run = await _run(db, host, output=self.LOG)
        result = await self._call(db, host, run, max_lines=10_000)
        assert result.content.count("| line ") == 400

    async def test_contains(self, db):
        host = await create_host(db)
        log = "TASK [a] ***\nok: [h]\nfatal: [h]: FAILED! one\nok: [h]\nFATAL: two"
        run = await _run(db, host, output=log)

        result = await self._call(db, host, run, contains="fatal")

        assert "    3| fatal: [h]: FAILED! one" in result.content
        assert "    5| FATAL: two" in result.content
        assert "ok: [h]" not in result.content
        assert result.content.endswith("No later line contains it.")

        none = await self._call(db, host, run, contains="nowhere")
        assert none.ok and "No line from 1 on contains 'nowhere'" in none.content

    async def test_numbers_lines_as_the_mission_does(self, db):
        """The mission's excerpt and this tool number the same text: a key
        that redaction folds into one placeholder moves every later line."""
        from app.ai import run_log

        host = await create_host(db)
        key = "-----BEGIN RSA PRIVATE KEY-----\nAAAA\nBBBB\n-----END RSA PRIVATE KEY-----"
        log = f"start\n{key}\nTASK [t] ***\nfatal: [h]: FAILED! boom"
        run = await _run(db, host, output=log)

        fatal = next(line for line in run_log.parse(run_log.readable(log)) if line.kind == "fatal")
        result = await self._call(db, host, run, contains="boom")
        assert f"{fatal.number:>5}| fatal: [h]: FAILED! boom" in result.content
        assert "AAAA" not in (await self._call(db, host, run)).content

    async def test_strips_terminal_codes(self, db):
        host = await create_host(db)
        run = await _run(db, host, output="\x1b[0;31mfatal: [h]\x1b[0m")
        assert "    1| fatal: [h]\n" in (await self._call(db, host, run)).content + "\n"

    async def test_out_of_scope(self, db):
        host, other = await create_host(db), await create_host(db)
        run = await _run(db, other, output="secret stuff")
        result = await GET_ACTION_RUN_OUTPUT.run(
            _ctx(db, [host.id]), {"run_id": run.id, "host_id": other.id}
        )
        assert not result.ok and "not in scope" in result.content

    async def test_a_run_the_host_was_not_in(self, db):
        host, other = await create_host(db), await create_host(db)
        run = await _run(db, other, output="x")
        result = await self._call(db, host, run)
        assert not result.ok and f"Run {run.id} has no part for host {host.id}" in result.content

    async def test_no_output(self, db):
        host = await create_host(db)
        run = await _run(db, host, output="")
        result = await self._call(db, host, run)
        assert result.ok and "No output was captured" in result.content
