import asyncio
import json
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.ws_auth import get_ws_user
from app.db import AsyncSessionLocal
from app.ssh_terminal.session_registry import registry
from app.ssh_terminal.ssh_connect import (
    open_ssh_shell,
    HostNotFoundError,
    NoSSHKeyError,
    SSHConnectionError,
)
from app.config import settings
from app.audit.logger import log_action
from app.models.host import Host
from sqlalchemy import select

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ssh-terminal", tags=["ssh-terminal"])


@router.websocket("/ws/{host_id}")
async def ssh_terminal_ws(websocket: WebSocket, host_id: int):
    await websocket.accept()

    async with AsyncSessionLocal() as db:
        try:
            user = await get_ws_user(websocket, db)
        except RuntimeError:
            return

        session_id = registry.generate_session_id()
        can_register = await registry.register(
            session_id=session_id,
            user_id=user.id,
            host_id=host_id,
        )
        if not can_register:
            try:
                await websocket.close(code=4429, reason="Session limit exceeded")
            except Exception:
                pass
            return

        try:
            conn, process = await open_ssh_shell(host_id, db)
        except HostNotFoundError:
            await registry.deregister(session_id)
            try:
                await websocket.close(code=4404, reason="Host not found")
            except Exception:
                pass
            return
        except NoSSHKeyError:
            await registry.deregister(session_id)
            try:
                await websocket.close(code=4400, reason="Host has no SSH key")
            except Exception:
                pass
            return
        except SSHConnectionError as e:
            await registry.deregister(session_id)
            try:
                await websocket.close(code=4502, reason=str(e)[:120])
            except Exception:
                pass
            return

        host_result = await db.execute(select(Host).where(Host.id == host_id))
        host = host_result.scalar_one_or_none()
        await log_action(
            db,
            action="session_start",
            entity_type="ssh_session",
            entity_id=host_id,
            user_id=user.id,
            after_state={
                "host_id": host_id,
                "hostname": host.hostname if host else "unknown",
                "ssh_user": host.ssh_user if host else "root",
                "session_id": session_id,
            },
        )
        await db.commit()

    user_id_for_audit = user.id

    async with registry._lock:
        if session_id in registry._sessions:
            registry._sessions[session_id].websocket = websocket
            registry._sessions[session_id].ssh_process = process
            registry._sessions[session_id].ssh_conn = conn

    start_time = time.time()
    disconnect_reason = "client_disconnect"

    async def ssh_to_ws():
        try:
            while not process.stdout.at_eof():
                data = await process.stdout.read(65536)
                if data:
                    await websocket.send_bytes(data.encode() if isinstance(data, str) else data)
        except Exception:
            pass

    async def ws_to_ssh():
        nonlocal disconnect_reason
        try:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                if "bytes" in message and message["bytes"]:
                    process.stdin.write(message["bytes"])
                    await registry.touch(session_id)
                elif "text" in message and message["text"]:
                    try:
                        ctrl = json.loads(message["text"])
                        if ctrl.get("type") == "resize":
                            cols = ctrl.get("cols", 80)
                            rows = ctrl.get("rows", 24)
                            process.change_terminal_size(cols, rows)
                        elif ctrl.get("type") == "ping":
                            await websocket.send_text(json.dumps({"type": "pong"}))
                    except (json.JSONDecodeError, ValueError):
                        pass
        except WebSocketDisconnect:
            disconnect_reason = "client_disconnect"
        except Exception:
            disconnect_reason = "error"

    async def idle_checker():
        nonlocal disconnect_reason
        while True:
            await asyncio.sleep(60)
            idle = registry.get_idle_sessions(settings.ssh.idle_timeout_seconds)
            if session_id in idle:
                disconnect_reason = "idle_timeout"
                try:
                    await websocket.close(code=4408, reason="Idle timeout")
                except Exception:
                    pass
                return

    reader = asyncio.create_task(ssh_to_ws())
    writer = asyncio.create_task(ws_to_ssh())
    checker = asyncio.create_task(idle_checker())

    try:
        done, pending = await asyncio.wait(
            [reader, writer, checker],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
    finally:
        try:
            process.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
        await registry.deregister(session_id)
        duration = int(time.time() - start_time)
        try:
            async with AsyncSessionLocal() as audit_db:
                await log_action(
                    audit_db,
                    action="session_end",
                    entity_type="ssh_session",
                    entity_id=host_id,
                    user_id=user_id_for_audit,
                    after_state={
                        "host_id": host_id,
                        "duration_seconds": duration,
                        "disconnect_reason": disconnect_reason,
                        "session_id": session_id,
                    },
                )
                await audit_db.commit()
        except Exception:
            logger.exception("Failed to log session_end audit entry")
        try:
            await websocket.close()
        except Exception:
            pass
