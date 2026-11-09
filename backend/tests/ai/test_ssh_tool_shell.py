"""SEC-45: the AI SSH tool must hand commands to bash, not the login shell."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

from app.ai.tools import ssh as ssh_tool
from app.ai.tools.base import ToolContext
from tests.conftest import create_host, create_ssh_key


class _Conn:
    def __init__(self, result):
        self.result = result
        self.calls: list[tuple[str, dict]] = []

    async def run(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return self.result


async def _run(db, conn, command="uptime"):
    key = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=key.id)

    @asynccontextmanager
    async def fake_connect(*_a, **_kw):
        yield conn

    ctx = ToolContext(db=db, session_id=1, autonomy_level="read_only", target_host_ids=[host.id])
    with (
        patch.object(ssh_tool, "ssh_connect_host", new=fake_connect),
        patch.object(ssh_tool, "decrypt_ssh_key", return_value="pem"),
        patch.object(ssh_tool.asyncssh, "import_private_key", return_value=object()),
    ):
        return await ssh_tool._run_ssh_command.run(ctx, {"host_id": host.id, "command": command})


async def test_the_command_reaches_bash_on_stdin(db):
    conn = _Conn(SimpleNamespace(stdout="up", stderr="", exit_status=0))
    result = await _run(db, conn, "uptime")
    assert result.ok
    command, kwargs = conn.calls[0]
    assert command == ssh_tool.BASH_WRAPPER
    assert "uptime" not in command
    assert kwargs["input"] == "uptime"


async def test_a_host_without_bash_is_refused(db):
    conn = _Conn(
        SimpleNamespace(
            stdout="", stderr="zsh:1: no such file or directory: /bin/bash", exit_status=127
        )
    )
    result = await _run(db, conn)
    assert not result.ok
    assert "no /bin/bash" in result.content
