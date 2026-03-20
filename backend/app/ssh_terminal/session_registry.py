import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import uuid


@dataclass
class SessionInfo:
    session_id: str
    user_id: int
    host_id: int
    started_at: datetime
    last_activity: datetime
    websocket: Any = None
    ssh_process: Any = None
    ssh_conn: Any = None


class SessionRegistry:
    def __init__(self, max_per_user: int = 5, max_total: int = 50):
        self._sessions: dict[str, SessionInfo] = {}
        self._lock = asyncio.Lock()
        self._max_per_user = max_per_user
        self._max_total = max_total

    def generate_session_id(self) -> str:
        return str(uuid.uuid4())

    async def register(
        self,
        session_id: str,
        user_id: int,
        host_id: int,
        websocket: Any = None,
        ssh_process: Any = None,
        ssh_conn: Any = None,
    ) -> bool:
        async with self._lock:
            if len(self._sessions) >= self._max_total:
                return False
            user_count = sum(
                1 for s in self._sessions.values() if s.user_id == user_id
            )
            if user_count >= self._max_per_user:
                return False
            now = datetime.now(timezone.utc)
            self._sessions[session_id] = SessionInfo(
                session_id=session_id,
                user_id=user_id,
                host_id=host_id,
                started_at=now,
                last_activity=now,
                websocket=websocket,
                ssh_process=ssh_process,
                ssh_conn=ssh_conn,
            )
            return True

    async def deregister(self, session_id: str) -> SessionInfo | None:
        async with self._lock:
            return self._sessions.pop(session_id, None)

    async def touch(self, session_id: str) -> None:
        async with self._lock:
            if session_id in self._sessions:
                self._sessions[session_id].last_activity = datetime.now(timezone.utc)

    def get_user_session_count(self, user_id: int) -> int:
        return sum(1 for s in self._sessions.values() if s.user_id == user_id)

    def get_total_session_count(self) -> int:
        return len(self._sessions)

    def get_idle_sessions(self, timeout_seconds: int) -> list[str]:
        now = datetime.now(timezone.utc)
        return [
            sid
            for sid, info in self._sessions.items()
            if (now - info.last_activity).total_seconds() > timeout_seconds
        ]

    async def cleanup_session(self, session_id: str) -> None:
        info = await self.deregister(session_id)
        if info is None:
            return
        if info.ssh_process is not None:
            try:
                info.ssh_process.close()
            except Exception:
                pass
        if info.ssh_conn is not None:
            try:
                info.ssh_conn.close()
            except Exception:
                pass
        if info.websocket is not None:
            try:
                await info.websocket.close(code=1000)
            except Exception:
                pass


# Singleton instance — uses settings from config
def _create_registry() -> SessionRegistry:
    from app.config import settings
    return SessionRegistry(
        max_per_user=settings.ssh.max_sessions_per_user,
        max_total=settings.ssh.max_total_sessions,
    )


registry = _create_registry()
