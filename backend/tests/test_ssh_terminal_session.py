"""Characterisation tests for the open SSH terminal session.

``tests/test_ssh_terminal.py`` only checks that the handshake refuses a
bad peer. These drive ``ssh_terminal_ws`` directly with a fake socket and
a fake shell, so the piping, the control messages, the three watchdog
closes, the close codes before the shell opens and the session audit rows
are pinned before the endpoint is restructured.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import WebSocketDisconnect
from sqlalchemy import select

from app.api import ssh_terminal
from app.models.audit_log import AuditLog
from app.models.user import User
from app.ssh_terminal.session_registry import registry
from app.ssh_terminal.ssh_connect import HostNotFoundError, NoSSHKeyError, SSHConnectionError
from tests.conftest import create_host

_real_sleep = asyncio.sleep
SESSION_ID = "sess-characterise"


class _FakeWebSocket:
    def __init__(self, events: list):
        self.events = events
        self.accepted = False
        self.closed: list[tuple] = []
        self.sent_text: list[str] = []
        self.inbox: asyncio.Queue = asyncio.Queue()

    async def accept(self):
        self.accepted = True

    async def close(self, code: int = 1000, reason: str | None = None):
        self.closed.append((code, reason))

    async def receive(self):
        msg = await self.inbox.get()
        if isinstance(msg, BaseException):
            raise msg
        return msg

    async def send_bytes(self, data):
        self.events.append(("send", data))

    async def send_text(self, text):
        self.sent_text.append(text)


class _FakeStdout:
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.eof = False

    def at_eof(self):
        return self.eof

    async def read(self, _n):
        item = await self.queue.get()
        if item is None:
            self.eof = True
            return b""
        return item


class _FakeProcess:
    def __init__(self):
        self.stdout = _FakeStdout()
        self.stdin = MagicMock()
        self.stdin.drain = AsyncMock()
        self.sizes: list[tuple[int, int]] = []
        self.closed = False

    def change_terminal_size(self, cols, rows):
        self.sizes.append((cols, rows))

    def close(self):
        self.closed = True


class _FakeTranscript:
    def __init__(self, events: list, created: list):
        self.events = events
        created.append(self)
        self.started = self.stopped = False
        self.fed: list[bytes] = []

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    def observe_output(self, data):
        self.events.append(("observe", data))

    def feed(self, chunk):
        self.fed.append(chunk)


class _Session:
    """One terminal session under test, with its fakes."""

    def __init__(self, db, user, host_id):
        self.db = db
        self.user = user
        self.host_id = host_id
        self.events: list = []
        self.ws = _FakeWebSocket(self.events)
        self.process = _FakeProcess()
        self.conn = MagicMock()
        self.transcripts: list[_FakeTranscript] = []
        self.task: asyncio.Task | None = None

    def start(self, **patches):
        self.task = asyncio.create_task(self._run(**patches))

    async def _run(self, *, open_shell=None, origin_ok=True, get_user=None):
        @asynccontextmanager
        async def _same_db():
            yield self.db

        with (
            patch.object(ssh_terminal, "AsyncSessionLocal", new=_same_db),
            patch.object(ssh_terminal, "check_ws_origin", new=AsyncMock(return_value=origin_ok)),
            patch.object(
                ssh_terminal, "get_ws_user", new=get_user or AsyncMock(return_value=self.user)
            ),
            patch.object(
                ssh_terminal,
                "open_ssh_shell",
                new=open_shell or AsyncMock(return_value=(self.conn, self.process)),
            ),
            patch.object(
                ssh_terminal,
                "TranscriptWriter",
                new=lambda **kw: _FakeTranscript(self.events, self.transcripts),
            ),
            patch.object(registry, "generate_session_id", return_value=SESSION_ID),
        ):
            await ssh_terminal.ssh_terminal_ws(self.ws, self.host_id)

    async def finish(self):
        await asyncio.wait_for(self.task, timeout=5)

    async def audit(self, action: str) -> list[AuditLog]:
        rows = (
            (
                await self.db.execute(
                    select(AuditLog).where(
                        AuditLog.action == action, AuditLog.entity_type == "ssh_session"
                    )
                )
            )
            .scalars()
            .all()
        )
        return [r for r in rows if (r.after_state or {}).get("session_id") == SESSION_ID]


async def _until(predicate, timeout: float = 2.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not reached")
        await _real_sleep(0.005)


@pytest.fixture
async def user(db):
    u = User(
        email=f"term-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        is_active=True,
        is_superuser=True,
        is_verified=True,
        token_version=3,
    )
    db.add(u)
    await db.flush()
    return u


@pytest.fixture(autouse=True)
async def clean_registry():
    await registry.deregister(SESSION_ID)
    yield
    await registry.deregister(SESSION_ID)


@pytest.fixture
def settings_values():
    values = {"ssh.idle_timeout_seconds": 900, "ssh.max_session_seconds": 3600}
    with patch("app.settings_service.get_setting_cached_typed", side_effect=values.__getitem__):
        yield values


async def test_a_session_pipes_both_ways_and_answers_control_messages(db, user):
    host = await create_host(db, hostname="term-a.test")
    host.ssh_user = "admin"
    await db.flush()
    s = _Session(db, user, host.id)
    s.start()

    await _until(lambda: s.transcripts and s.transcripts[0].started)
    info = registry._sessions[SESSION_ID]
    assert (info.websocket, info.ssh_process, info.ssh_conn) == (s.ws, s.process, s.conn)
    assert s.ws.accepted

    s.process.stdout.queue.put_nowait(b"login banner")
    await _until(lambda: ("send", b"login banner") in s.events)
    assert s.events.index(("observe", b"login banner")) < s.events.index(("send", b"login banner"))

    s.ws.inbox.put_nowait({"type": "websocket.receive", "bytes": b"ls\n"})
    s.ws.inbox.put_nowait({"type": "websocket.receive", "text": '{"type": "resize"}'})
    s.ws.inbox.put_nowait(
        {"type": "websocket.receive", "text": '{"type": "resize", "cols": 120, "rows": 40}'}
    )
    s.ws.inbox.put_nowait({"type": "websocket.receive", "text": '{"type": "ping"}'})
    s.ws.inbox.put_nowait({"type": "websocket.receive", "text": "not json"})
    s.ws.inbox.put_nowait({"type": "websocket.receive", "bytes": b""})
    s.ws.inbox.put_nowait({"type": "websocket.disconnect"})
    await s.finish()

    s.process.stdin.write.assert_called_once_with(b"ls\n")
    s.process.stdin.drain.assert_awaited_once()
    assert s.transcripts[0].fed == [b"ls\n"]
    assert s.process.sizes == [(80, 24), (120, 40)]
    assert s.ws.sent_text == [json.dumps({"type": "pong"})]

    assert s.transcripts[0].stopped
    assert s.process.closed
    s.conn.close.assert_called_once()
    assert SESSION_ID not in registry._sessions
    assert s.ws.closed == [(1000, None)]

    (start,) = await s.audit("session_start")
    assert start.entity_id == host.id
    assert start.user_id == user.id
    assert start.after_state == {
        "host_id": host.id,
        "hostname": "term-a.test",
        "ssh_user": "admin",
        "session_id": SESSION_ID,
    }
    (end,) = await s.audit("session_end")
    assert end.user_id == user.id
    assert end.after_state["disconnect_reason"] == "client_disconnect"
    assert end.after_state["host_id"] == host.id
    assert isinstance(end.after_state["duration_seconds"], int)


async def test_the_shell_closing_ends_the_session(db, user):
    host = await create_host(db)
    s = _Session(db, user, host.id)
    s.start()
    await _until(lambda: s.transcripts and s.transcripts[0].started)

    s.process.stdout.queue.put_nowait(None)
    await s.finish()

    (end,) = await s.audit("session_end")
    assert end.after_state["disconnect_reason"] == "client_disconnect"
    assert s.process.closed


@pytest.mark.parametrize(
    ("error", "reason"),
    [(WebSocketDisconnect(), "client_disconnect"), (RuntimeError("socket broke"), "error")],
)
async def test_a_receive_failure_ends_the_session(db, user, error, reason):
    host = await create_host(db)
    s = _Session(db, user, host.id)
    s.start()
    await _until(lambda: s.transcripts and s.transcripts[0].started)

    s.ws.inbox.put_nowait(error)
    await s.finish()

    (end,) = await s.audit("session_end")
    assert end.after_state["disconnect_reason"] == reason


@pytest.mark.parametrize(
    ("setup", "close", "reason"),
    [
        ("idle", (4408, "Idle timeout"), "idle_timeout"),
        ("aged", (4408, "Session time limit reached"), "max_session_age"),
        ("revoked", (4401, "Session no longer valid"), "session_revoked"),
    ],
)
async def test_the_watchdog_closes_an_idle_aged_or_revoked_session(
    db, user, settings_values, setup, close, reason
):
    host = await create_host(db)
    s = _Session(db, user, host.id)
    checked: list[tuple] = []

    async def _still_valid(user_id, generation):
        checked.append((user_id, generation))
        return setup != "revoked"

    if setup == "idle":
        settings_values["ssh.idle_timeout_seconds"] = -1
    if setup == "aged":
        settings_values["ssh.max_session_seconds"] = 0

    async def _fast_sleep(_delay, result=None):
        await _real_sleep(0)
        return result

    with (
        patch.object(ssh_terminal, "_session_still_valid", new=_still_valid),
        patch("asyncio.sleep", new=_fast_sleep),
    ):
        s.start()
        await s.finish()

    assert s.ws.closed[0] == close
    assert s.ws.closed[-1] == (1000, None)
    (end,) = await s.audit("session_end")
    assert end.after_state["disconnect_reason"] == reason
    if setup == "revoked":
        assert checked == [(user.id, 3)]
    else:
        assert checked == []


async def test_an_origin_not_allowed_is_closed_before_the_handshake(db, user):
    s = _Session(db, user, 1)
    s.start(origin_ok=False)
    await s.finish()
    assert s.ws.closed == [(4403, "Origin not allowed")]
    assert not s.ws.accepted


async def test_an_unauthenticated_peer_is_never_accepted(db, user):
    s = _Session(db, user, 1)
    s.start(get_user=AsyncMock(side_effect=RuntimeError("closed 4401")))
    await s.finish()
    assert s.ws.closed == []
    assert not s.ws.accepted


async def test_the_session_limit_closes_with_4429(db, user):
    s = _Session(db, user, 1)
    with patch.object(registry, "register", new=AsyncMock(return_value=False)):
        s.start()
        await s.finish()
    assert s.ws.accepted
    assert s.ws.closed == [(4429, "Session limit exceeded")]


@pytest.mark.parametrize(
    ("error", "close"),
    [
        (HostNotFoundError("nope"), (4404, "Host not found")),
        (NoSSHKeyError("none"), (4400, "Host has no SSH key")),
        (SSHConnectionError("x" * 200), (4502, "x" * 120)),
    ],
)
async def test_a_shell_that_will_not_open_closes_with_its_code(db, user, error, close):
    s = _Session(db, user, 1)
    s.start(open_shell=AsyncMock(side_effect=error))
    await s.finish()
    assert s.ws.closed == [close]
    assert SESSION_ID not in registry._sessions
    assert s.transcripts == []


async def test_a_failed_session_end_audit_is_logged_and_the_socket_still_closes(db, user, caplog):
    host = await create_host(db)
    s = _Session(db, user, host.id)
    s.start()
    await _until(lambda: s.transcripts and s.transcripts[0].started)

    with patch.object(ssh_terminal, "log_action", new=AsyncMock(side_effect=RuntimeError("db"))):
        s.ws.inbox.put_nowait({"type": "websocket.disconnect"})
        await s.finish()

    assert "Failed to log session_end audit entry" in caplog.text
    assert s.ws.closed == [(1000, None)]
    assert SESSION_ID not in registry._sessions
