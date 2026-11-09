import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.audit.logger import log_action
from app.auth.ws_auth import check_ws_origin, get_ws_user
from app.db import AsyncSessionLocal
from app.models.host import Host
from app.models.user import User
from app.ssh_terminal.session_registry import registry
from app.ssh_terminal.ssh_connect import (
    HostNotFoundError,
    NoSSHKeyError,
    SSHConnectionError,
    open_ssh_shell,
)
from app.ssh_terminal.transcript import TranscriptWriter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ssh-terminal", tags=["ssh-terminal"])


async def _session_still_valid(user_id: int, generation: int) -> bool:
    """Whether the user is still active and still on the same session generation."""
    try:
        async with AsyncSessionLocal() as db:
            row = (
                await db.execute(
                    select(User.is_active, User.token_version).where(User.id == user_id)
                )
            ).first()
    except Exception:
        # A database blip must not drop every open terminal; the next check
        # a minute later decides.
        logger.warning("ssh terminal: could not re-check user %s", user_id, exc_info=True)
        return True
    if row is None:
        return False
    is_active, token_version = row
    return bool(is_active) and (token_version or 0) == generation


@router.websocket("/ws/{host_id}")
async def ssh_terminal_ws(websocket: WebSocket, host_id: int):
    # SEC-34: authenticate before completing the handshake. Accepting
    # first meant an unauthenticated peer got an open socket, however
    # briefly, on the endpoint that hands out a root shell. A close
    # before accept is answered as an HTTP rejection, so the browser
    # reports a plain connection failure rather than a coded close —
    # that is the intended trade.
    if not await check_ws_origin(websocket):
        await websocket.close(code=4403, reason="Origin not allowed")
        return

    session = await _open_session(websocket, host_id)
    if session is None:
        return
    await session.run()


async def _open_session(websocket: WebSocket, host_id: int) -> "_TerminalSession | None":
    """Authenticate, accept, take a registry slot, open the shell and log
    ``session_start``. Returns None, with the socket closed, when a step
    refuses."""
    async with AsyncSessionLocal() as db:
        try:
            user = await get_ws_user(websocket, db)
        except RuntimeError:
            return None

        await websocket.accept()

        session_id = registry.generate_session_id()
        can_register = await registry.register(
            session_id=session_id,
            user_id=user.id,
            host_id=host_id,
        )
        if not can_register:
            await _close_quietly(websocket, code=4429, reason="Session limit exceeded")
            return None

        try:
            conn, process = await open_ssh_shell(host_id, db)
        except HostNotFoundError:
            await _refuse(websocket, session_id, 4404, "Host not found")
            return None
        except NoSSHKeyError:
            await _refuse(websocket, session_id, 4400, "Host has no SSH key")
            return None
        except SSHConnectionError as e:
            await _refuse(websocket, session_id, 4502, str(e)[:120])
            return None

        await _log_session_start(db, host_id=host_id, user_id=user.id, session_id=session_id)
        await db.commit()

    return _TerminalSession(
        websocket=websocket,
        host_id=host_id,
        session_id=session_id,
        user_id=user.id,
        # SEC-44: the handshake is the only authentication a WebSocket gets, so
        # remember the session generation it proved and re-check it below.
        generation=user.token_version or 0,
        conn=conn,
        process=process,
    )


async def _refuse(websocket: WebSocket, session_id: str, code: int, reason: str) -> None:
    """Give back the registry slot and close the socket with ``code``."""
    await registry.deregister(session_id)
    await _close_quietly(websocket, code=code, reason=reason)


async def _close_quietly(websocket: WebSocket, **close_kwargs) -> None:
    """Close the socket, ignoring a peer that is already gone."""
    with contextlib.suppress(Exception):
        await websocket.close(**close_kwargs)


async def _log_session_start(db, *, host_id: int, user_id: int, session_id: str) -> None:
    host_result = await db.execute(select(Host).where(Host.id == host_id))
    host = host_result.scalar_one_or_none()
    await log_action(
        db,
        action="session_start",
        entity_type="ssh_session",
        entity_id=host_id,
        user_id=user_id,
        after_state={
            "host_id": host_id,
            "hostname": host.hostname if host else "unknown",
            "ssh_user": host.ssh_user if host else "root",
            "session_id": session_id,
        },
    )


@dataclass
class _TerminalSession:
    """One open terminal: the socket, the shell, and the state its three
    tasks share until the first of them ends the session."""

    websocket: WebSocket
    host_id: int
    session_id: str
    user_id: int
    generation: int
    conn: Any
    process: Any
    transcript: TranscriptWriter | None = None
    start_time: float = 0.0
    disconnect_reason: str = "client_disconnect"

    async def run(self) -> None:
        """Pipe the shell and the socket until one side ends, then clean up."""
        await self._attach_to_registry()

        self.start_time = time.time()
        self.disconnect_reason = "client_disconnect"

        self.transcript = TranscriptWriter(
            session_id=self.session_id,
            host_id=self.host_id,
            user_id=self.user_id,
        )
        await self.transcript.start()

        reader = asyncio.create_task(self._forward_shell_output())
        writer = asyncio.create_task(self._forward_client_input())
        checker = asyncio.create_task(self._watchdog())

        try:
            done, pending = await asyncio.wait(
                [reader, writer, checker],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
        finally:
            await self._close()

    async def _attach_to_registry(self) -> None:
        async with registry._lock:
            if self.session_id in registry._sessions:
                registry._sessions[self.session_id].websocket = self.websocket
                registry._sessions[self.session_id].ssh_process = self.process
                registry._sessions[self.session_id].ssh_conn = self.conn

    async def _forward_shell_output(self) -> None:
        try:
            while not self.process.stdout.at_eof():
                data = await self.process.stdout.read(65536)
                if data:
                    # SEC-22: host output is never stored. The writer reads it
                    # only to notice a password prompt, so the keystrokes that
                    # answer one are recorded as a placeholder rather than as
                    # the secret. Before the user sees the data, so a slow
                    # WebSocket cannot let the answer arrive first.
                    self.transcript.observe_output(data)
                    await self.websocket.send_bytes(data)
        except Exception:
            logger.exception("ssh_to_ws error for session %s", self.session_id)

    async def _forward_client_input(self) -> None:
        try:
            while True:
                message = await self.websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                if "bytes" in message and message["bytes"]:
                    chunk = message["bytes"]
                    self.process.stdin.write(chunk)
                    await self.process.stdin.drain()
                    self.transcript.feed(chunk)
                    await registry.touch(self.session_id)
                elif "text" in message and message["text"]:
                    await self._handle_control_message(message["text"])
        except WebSocketDisconnect:
            self.disconnect_reason = "client_disconnect"
        except Exception:
            logger.exception("ws_to_ssh error for session %s", self.session_id)
            self.disconnect_reason = "error"

    async def _handle_control_message(self, text: str) -> None:
        """A JSON control message from the browser: resize or ping."""
        try:
            ctrl = json.loads(text)
            if ctrl.get("type") == "resize":
                cols = ctrl.get("cols", 80)
                rows = ctrl.get("rows", 24)
                self.process.change_terminal_size(cols, rows)
            elif ctrl.get("type") == "ping":
                await self.websocket.send_text(json.dumps({"type": "pong"}))
        except (json.JSONDecodeError, ValueError):
            pass

    async def _watchdog(self) -> None:
        """Once a minute, close the session if it should no longer be open."""
        while True:
            await asyncio.sleep(60)
            verdict = await self._reason_to_close()
            if verdict is not None:
                await self._close_with(*verdict)
                return

    async def _reason_to_close(self) -> tuple[str, int, str] | None:
        """``(reason_key, code, reason)`` for a session that has to end, else None."""
        from app.settings_service import get_setting_cached_typed

        idle_timeout = int(get_setting_cached_typed("ssh.idle_timeout_seconds"))
        idle = registry.get_idle_sessions(idle_timeout)
        if self.session_id in idle:
            return "idle_timeout", 4408, "Idle timeout"
        # SEC-44: keystrokes reset the idle timer, so without a cap a
        # shell could stay open indefinitely.
        max_age = int(get_setting_cached_typed("ssh.max_session_seconds"))
        if time.time() - self.start_time >= max_age:
            return "max_session_age", 4408, "Session time limit reached"
        # SEC-44: deactivation, a password change or "log out
        # everywhere" (SEC-30) must reach an open terminal too.
        if not await _session_still_valid(self.user_id, self.generation):
            return "session_revoked", 4401, "Session no longer valid"
        return None

    async def _close_with(self, reason_key: str, code: int, reason: str) -> None:
        self.disconnect_reason = reason_key
        await _close_quietly(self.websocket, code=code, reason=reason)

    async def _close(self) -> None:
        """Stop the transcript, close the shell, free the slot, log
        ``session_end`` and close the socket. Each step survives the last."""
        await self.transcript.stop()
        with contextlib.suppress(Exception):
            self.process.close()
        with contextlib.suppress(Exception):
            self.conn.close()
        await registry.deregister(self.session_id)
        duration = int(time.time() - self.start_time)
        try:
            async with AsyncSessionLocal() as audit_db:
                await log_action(
                    audit_db,
                    action="session_end",
                    entity_type="ssh_session",
                    entity_id=self.host_id,
                    user_id=self.user_id,
                    after_state={
                        "host_id": self.host_id,
                        "duration_seconds": duration,
                        "disconnect_reason": self.disconnect_reason,
                        "session_id": self.session_id,
                    },
                )
                await audit_db.commit()
        except Exception:
            logger.exception("Failed to log session_end audit entry")
        await _close_quietly(self.websocket)
