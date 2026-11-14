"""Characterisation tests for the AI SSH tool's exits the suite leaves dark.

``test_ssh_tool_shell`` pins SEC-45 and the remediation tests drive the
happy path. These pin every other way ``run_ssh_command`` answers: bad
arguments, the scope and autonomy gates (an approved call included), a
missing host or key, an unusable key, each connection failure, and how the
output is assembled, redacted and truncated, so a restructuring of
``app.ai.tools.ssh`` has something to answer to.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import asyncssh
import pytest

from app.ai.tools import ssh as ssh_tool
from app.ai.tools.base import ToolContext, ToolResult
from app.ssh_utils import HostKeyMismatchError
from tests.conftest import create_host, create_ssh_key

REFUSED_TAIL = (
    "\n\nThe command was not run. If you need this change, explain what it would do "
    "and why, and stop — the operator will decide."
)


class _Conn:
    def __init__(self, result=None, error: BaseException | None = None):
        self.result = result or SimpleNamespace(stdout="", stderr="", exit_status=0)
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    async def run(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if self.error is not None:
            raise self.error
        return self.result


class _Env:
    def __init__(self, conn: _Conn | None = None):
        self.conn = conn or _Conn()
        self.connect_error: BaseException | None = None
        self.decrypt_error: BaseException | None = None
        self.connects = 0

    @asynccontextmanager
    async def _connect(self, host, db, client_keys):  # noqa: ARG002
        self.connects += 1
        if self.connect_error is not None:
            raise self.connect_error
        yield self.conn

    def _decrypt(self, *_args):
        if self.decrypt_error is not None:
            raise self.decrypt_error
        return "pem"


async def _call(db, args, *, env=None, autonomy="read_only", targets=None, preapproved=False):
    env = env or _Env()
    ctx = ToolContext(
        db=db,
        session_id=7,
        autonomy_level=autonomy,
        target_host_ids=targets if targets is not None else [args.get("host_id")],
        preapproved=preapproved,
    )
    with (
        patch.object(ssh_tool, "ssh_connect_host", new=env._connect),
        patch.object(ssh_tool, "decrypt_ssh_key", side_effect=env._decrypt),
        patch.object(ssh_tool.asyncssh, "import_private_key", return_value=object()),
    ):
        return await ssh_tool._run_ssh_command.run(ctx, args)


async def _keyed_host(db, hostname="ssh-paths.test"):
    key = await create_ssh_key(db)
    return await create_host(db, hostname=hostname, ssh_key_id=key.id)


@pytest.mark.parametrize(
    ("args", "content"),
    [
        ({"host_id": "1", "command": "uptime"}, "host_id must be an integer."),
        ({"command": "uptime"}, "host_id must be an integer."),
        ({"host_id": 1, "command": "   "}, "command must not be empty."),
        ({"host_id": 1, "command": None}, "command must not be empty."),
        ({"host_id": 1}, "command must not be empty."),
    ],
)
async def test_bad_arguments_are_refused(db, args, content):
    result = await _call(db, args, targets=[1])
    assert result == ToolResult(content, ok=False, classification="read_only")


async def test_a_host_out_of_scope_is_refused(db):
    result = await _call(db, {"host_id": 5, "command": "uptime"}, targets=[6])
    assert result == ToolResult(
        "Host 5 is not in scope for this session. You may only run commands on hosts "
        "returned by list_hosts.",
        ok=False,
        classification="denied",
        summary="out of scope",
    )


async def test_a_session_with_no_targets_is_refused(db):
    result = await _call(db, {"host_id": 5, "command": "uptime"}, targets=[])
    assert result == ToolResult(
        "This session has no target hosts, so no command can be run.",
        ok=False,
        classification="denied",
        summary="no target hosts",
    )


async def test_a_write_in_a_read_only_session_is_refused_without_connecting(db, caplog):
    host = await _keyed_host(db)
    env = _Env()
    with caplog.at_level(logging.INFO, logger="app.ai.tools.ssh"):
        result = await _call(
            db, {"host_id": host.id, "command": "systemctl restart nginx"}, env=env
        )

    reason = (
        "Refused: this session is read-only, and unbork cannot tell that this command only "
        "reads ('systemctl' is read-only only on its own, and 'restart' makes it something "
        "else, so it is treated as a write)"
    )
    assert result == ToolResult(
        reason + REFUSED_TAIL,
        ok=False,
        target_host_id=host.id,
        classification="mutating",
        summary=reason[:200],
    )
    assert f"ai session 7: refused command on host {host.id} (mutating)" in caplog.text
    assert env.connects == 0


async def test_an_approved_write_runs_but_a_denied_command_never_does(db):
    host = await _keyed_host(db)
    env = _Env()
    ran = await _call(
        db, {"host_id": host.id, "command": "systemctl restart nginx"}, env=env, preapproved=True
    )
    assert ran.ok and ran.classification == "mutating"

    refused = await _call(
        db, {"host_id": host.id, "command": "rm -rf /"}, env=env, preapproved=True
    )
    assert refused == ToolResult(
        "Blocked: recursive delete of /" + REFUSED_TAIL,
        ok=False,
        target_host_id=host.id,
        classification="denied",
        summary="Blocked: recursive delete of /",
    )
    assert env.connects == 1


async def test_a_missing_host_is_reported(db):
    result = await _call(db, {"host_id": 987654, "command": "uptime"})
    assert result == ToolResult("No host with id 987654.", ok=False, classification="read_only")


async def test_a_host_without_a_key_is_reported(db):
    host = await create_host(db, hostname="keyless.test")
    result = await _call(db, {"host_id": host.id, "command": "uptime"})
    assert result == ToolResult(
        "Host keyless.test has no SSH key assigned in unbork.",
        ok=False,
        target_host_id=host.id,
        classification="read_only",
    )


async def test_an_unusable_key_is_reported_without_connecting(db, caplog):
    host = await _keyed_host(db)
    env = _Env()
    env.decrypt_error = ValueError("bad tag")
    with caplog.at_level(logging.WARNING, logger="app.ai.tools.ssh"):
        result = await _call(db, {"host_id": host.id, "command": "uptime"}, env=env)
    assert result == ToolResult(
        "Could not load the SSH key for ssh-paths.test.",
        ok=False,
        target_host_id=host.id,
        classification="read_only",
    )
    assert "ai session 7: SSH key unusable: bad tag" in caplog.text
    assert env.connects == 0


@pytest.mark.parametrize(
    ("error", "content", "summary"),
    [
        (
            HostKeyMismatchError("fingerprint changed"),
            "SSH host key mismatch for ssh-paths.test — refusing to connect. This may "
            "indicate a man-in-the-middle, or the host was rebuilt. An operator must "
            "resolve it. (fingerprint changed)",
            "host key mismatch",
        ),
        (
            OSError("no route to host"),
            "Could not run the command on ssh-paths.test: no route to host",
            "ssh error",
        ),
        (
            asyncssh.DisconnectError(11, "bye"),
            "Could not run the command on ssh-paths.test: bye",
            "ssh error",
        ),
    ],
)
async def test_connection_failures_are_reported(db, error, content, summary):
    host = await _keyed_host(db)
    env = _Env()
    env.connect_error = error
    result = await _call(db, {"host_id": host.id, "command": "uptime"}, env=env)
    assert result == ToolResult(
        content, ok=False, target_host_id=host.id, classification="read_only", summary=summary
    )


async def test_a_command_that_runs_too_long_is_reported(db, monkeypatch):
    host = await _keyed_host(db)
    env = _Env(_Conn(error=TimeoutError()))
    result = await _call(db, {"host_id": host.id, "command": "uptime"}, env=env)
    assert result == ToolResult(
        "The command timed out after 120s on ssh-paths.test. Try a bounded variant.",
        ok=False,
        target_host_id=host.id,
        classification="read_only",
        summary="timed out",
    )


@pytest.mark.parametrize(
    ("stdout", "stderr", "status", "content"),
    [
        (" up 3 days \n", "", 0, "exit status: 0\n\nstdout:\nup 3 days"),
        ("", " warn ", 2, "exit status: 2\n\nstderr:\nwarn"),
        ("out", "err", 1, "exit status: 1\n\nstdout:\nout\n\nstderr:\nerr"),
        (None, None, 0, "exit status: 0\n\n(no output)"),
        ("", "bash: nosuch: command not found", 127, None),
        ("", "sh: 1: /bin/bash: not found", 127, "NO_BASH"),
    ],
)
async def test_output_is_assembled_from_status_stdout_and_stderr(
    db, stdout, stderr, status, content
):
    host = await _keyed_host(db)
    env = _Env(_Conn(SimpleNamespace(stdout=stdout, stderr=stderr, exit_status=status)))
    result = await _call(db, {"host_id": host.id, "command": "uptime"}, env=env)

    if content == "NO_BASH":
        assert result == ToolResult(
            "ssh-paths.test has no /bin/bash. The AI tools only run commands under bash, "
            "the shell the safety classifier understands.",
            ok=False,
            target_host_id=host.id,
            classification="read_only",
            summary="no bash on host",
        )
        return
    if content is None:
        content = "exit status: 127\n\nstderr:\nbash: nosuch: command not found"
    assert result == ToolResult(
        content,
        ok=status == 0,
        target_host_id=host.id,
        classification="read_only",
        summary=f"exit {status}",
    )


async def test_output_is_redacted_then_truncated(db, monkeypatch):
    monkeypatch.setattr(ssh_tool, "MAX_OUTPUT_CHARS", 200)
    host = await _keyed_host(db)
    secret_line = "password=hunter2-very-secret"
    stdout = secret_line + "\n" + "a line of ordinary output\n" * 30
    env = _Env(_Conn(SimpleNamespace(stdout=stdout, stderr="", exit_status=0)))
    result = await _call(db, {"host_id": host.id, "command": "uptime"}, env=env)

    assert "hunter2" not in result.content
    assert "characters omitted by unbork" in result.content
    expected_body = ssh_tool.redact(f"exit status: 0\n\nstdout:\n{stdout.strip()}")
    assert result.content == ssh_tool._truncate(expected_body)
    assert result.content.startswith(expected_body[:100])
    assert result.content.endswith(expected_body[-100:])
