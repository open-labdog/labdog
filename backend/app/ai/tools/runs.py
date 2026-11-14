"""Action history tools — what unbork ran on a host, and what it printed.

Both read unbork's own database and never connect to a host. Both are
scoped to the session's target allowlist the way ``get_host_facts`` is:
a session with targets sees only their runs.

``get_action_run_output`` is what an investigation of a run pages through:
its mission carries only an excerpt of each host's log
(:mod:`app.ai.run_mission`), numbered the way this tool numbers lines.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.ai import run_log
from app.ai.tools.base import ToolContext, ToolResult, tool
from app.models.action_run import ActionHostRun, ActionRun

_DEFAULT_RUNS = 10
_MAX_RUNS = 25
_DEFAULT_LINES = 150
_MAX_LINES = 400
_MAX_LINE_CHARS = 500


def _out_of_scope(ctx: ToolContext, host_id: Any) -> ToolResult | None:
    if not isinstance(host_id, int) or isinstance(host_id, bool):
        return ToolResult("host_id must be an integer.", ok=False)
    if ctx.target_host_ids and host_id not in ctx.target_host_ids:
        return ToolResult(
            f"Host {host_id} is not in scope for this session.",
            ok=False,
            summary="out of scope",
        )
    return None


def _bounded(value: Any, default: int, low: int, high: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        return default
    return max(low, min(value, high))


def _utc(moment: datetime | None) -> str:
    if moment is None:
        return "-"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


@tool(
    name="list_action_runs",
    description=(
        "List the action runs unbork recently ran against one host, newest "
        "first: run id, action, how the host's part ended, when, and its "
        "error. Use it to see what changed on a host lately (an upgrade, a "
        "config push) and to find run ids for get_action_run_output."
    ),
    parameters={
        "type": "object",
        "properties": {
            "host_id": {"type": "integer", "description": "Host id from list_hosts."},
            "limit": {
                "type": "integer",
                "description": f"How many runs, {_DEFAULT_RUNS} by default, at most {_MAX_RUNS}.",
            },
        },
        "required": ["host_id"],
        "additionalProperties": False,
    },
    classification="read_only",
)
async def _list_action_runs(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    host_id = args.get("host_id")
    if (refusal := _out_of_scope(ctx, host_id)) is not None:
        return refusal
    limit = _bounded(args.get("limit"), _DEFAULT_RUNS, 1, _MAX_RUNS)

    rows = (
        await ctx.db.execute(
            select(ActionHostRun, ActionRun)
            .join(ActionRun, ActionRun.id == ActionHostRun.action_run_id)
            .where(ActionHostRun.host_id == host_id)
            .order_by(ActionRun.created_at.desc(), ActionRun.id.desc())
            .limit(limit)
        )
    ).all()
    if not rows:
        return ToolResult(
            f"unbork has no action runs recorded for host {host_id}.",
            target_host_id=host_id,
            summary="no runs",
        )

    lines = []
    for hr, run in rows:
        when = _utc(hr.started_at or run.started_at or run.created_at)
        line = (
            f"- run_id={run.id} {run.action_key} v{run.action_version}: host {hr.status}"
            f" (run {run.status}), started {when}, finished {_utc(hr.finished_at)}"
        )
        if run.scheduled_action_id is not None:
            line += ", scheduled"
        if run.alert_event_id is not None:
            line += ", started by an alert"
        if hr.error_message:
            line += f"; error: {hr.error_message[:200]}"
        lines.append(line)
    return ToolResult(
        f"Action runs on host {host_id}, newest first:\n" + "\n".join(lines),
        target_host_id=host_id,
        summary=f"{len(rows)} run(s)",
    )


@tool(
    name="get_action_run_output",
    description=(
        "Read the Ansible output unbork captured for one host in one action "
        "run, with line numbers. Returns up to max_lines lines from "
        "start_line; with contains, only the lines that include that text "
        "(case-insensitive). Page with start_line rather than asking for "
        "everything: each result is read back on every later turn."
    ),
    parameters={
        "type": "object",
        "properties": {
            "run_id": {"type": "integer", "description": "Action run id."},
            "host_id": {"type": "integer", "description": "Host id from list_hosts."},
            "start_line": {"type": "integer", "description": "First line to return, from 1."},
            "max_lines": {
                "type": "integer",
                "description": (
                    f"How many lines, {_DEFAULT_LINES} by default, at most {_MAX_LINES}."
                ),
            },
            "contains": {
                "type": "string",
                "description": "Only lines containing this text, e.g. 'fatal' or a task name.",
            },
        },
        "required": ["run_id", "host_id"],
        "additionalProperties": False,
    },
    classification="read_only",
)
async def _get_action_run_output(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    run_id = args.get("run_id")
    host_id = args.get("host_id")
    if not isinstance(run_id, int) or isinstance(run_id, bool):
        return ToolResult("run_id must be an integer.", ok=False)
    if (refusal := _out_of_scope(ctx, host_id)) is not None:
        return refusal

    host_run = await ctx.db.scalar(
        select(ActionHostRun).where(
            ActionHostRun.action_run_id == run_id, ActionHostRun.host_id == host_id
        )
    )
    if host_run is None:
        return ToolResult(f"Run {run_id} has no part for host {host_id}.", ok=False)

    text = run_log.readable(host_run.output)
    if not text.strip():
        return ToolResult(
            f"No output was captured for host {host_id} in run {run_id}.",
            target_host_id=host_id,
            summary="no output",
        )
    all_lines = text.split("\n")
    total = len(all_lines)
    start = _bounded(args.get("start_line"), 1, 1, total)
    limit = _bounded(args.get("max_lines"), _DEFAULT_LINES, 1, _MAX_LINES)
    numbered = list(enumerate(all_lines, start=1))[start - 1 :]

    contains = args.get("contains")
    if isinstance(contains, str) and contains.strip():
        needle = contains.strip().lower()
        numbered = [(n, line) for n, line in numbered if needle in line.lower()]
        if not numbered:
            return ToolResult(
                f"No line from {start} on contains {contains.strip()!r} "
                f"(the log has {total} lines).",
                target_host_id=host_id,
                summary="no match",
            )

    shown = numbered[:limit]
    body = "\n".join(
        f"{n:>5}| {line if len(line) <= _MAX_LINE_CHARS else line[:_MAX_LINE_CHARS] + ' …'}"
        for n, line in shown
    )
    last = shown[-1][0]
    if len(numbered) > len(shown):
        footer = f"More follows: call again with start_line={last + 1}."
    elif isinstance(contains, str) and contains.strip():
        footer = "No later line contains it."
    else:
        footer = "That is the end of the log." if last == total else ""
    header = f"Run {run_id}, host {host_id}: {total} lines of output, status {host_run.status}."
    return ToolResult(
        "\n".join(part for part in (header, body, footer) if part),
        target_host_id=host_id,
        summary=f"run {run_id} lines {shown[0][0]}-{last}",
    )


LIST_ACTION_RUNS = _list_action_runs
GET_ACTION_RUN_OUTPUT = _get_action_run_output
