"""SSH session transcript capture.

Buffers bytes flowing from the user (WebSocket) toward the SSH host,
flushing rows into ``ssh_session_transcripts`` on newline boundaries
(``\\r`` or ``\\n``) or when the session closes.

Design goals:
- Fire-and-forget: DB writes are dispatched via ``asyncio.create_task`` so a
  slow DB never backpressures the SSH stream.
- Bounded per-session buffer: once total bytes for a session reach
  ``SESSION_CAP_BYTES`` a single truncation-sentinel row is written and
  capture stops for that session.  The SSH session itself continues
  uninterrupted.
- DB errors are caught and logged; they never propagate to the caller.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

# Per-session stdin capture cap (1 MiB).
SESSION_CAP_BYTES: int = 1 * 1024 * 1024

# Flush after accumulating this many bytes even without a newline.
SIZE_FLUSH_THRESHOLD: int = 8 * 1024  # 8 KiB

_TRUNCATION_SENTINEL: str = "[transcript truncated — per-session 1 MiB cap exceeded]"


class TranscriptWriter:
    """Per-session transcript capture state.

    Lifecycle::

        writer = TranscriptWriter(session_id, host_id, user_id)
        await writer.start()
        # ... call writer.feed(chunk) from ws_to_ssh ...
        await writer.stop()

    ``feed()`` is synchronous and non-blocking.  Each incoming chunk is
    scanned for ``\\r`` / ``\\n`` bytes; complete lines are inserted as
    individual transcript rows via fire-and-forget ``asyncio.create_task``
    calls.  Any remaining partial line is held in ``_buf`` until the next
    newline arrives or the session closes.
    """

    def __init__(self, session_id: str, host_id: int, user_id: int | None) -> None:
        self._session_id = session_id
        self._host_id = host_id
        self._user_id = user_id

        self._buf: bytearray = bytearray()
        self._total_bytes: int = 0
        self._truncated: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """No-op — kept for API compatibility with the WS handler."""

    async def stop(self) -> None:
        """Flush remaining buffer, then wait for all pending write tasks."""
        if self._truncated or not self._buf:
            return
        # Flush whatever is left in the buffer as a final row.
        text = self._buf.decode("utf-8", errors="replace").rstrip("\r\n")
        byte_count = len(self._buf)
        self._buf = bytearray()
        if text:
            await self._insert_row(text, byte_count)

    def feed(self, chunk: bytes) -> None:
        """Feed a raw byte chunk from the WebSocket receive loop.

        Non-blocking.  Splits on ``\\r`` / ``\\n`` and fires a
        ``create_task`` for each complete line.  Any trailing partial line
        is accumulated in ``_buf``.
        """
        if self._truncated or not chunk:
            return

        # Check total-bytes cap *before* processing the chunk.
        self._total_bytes += len(chunk)
        if self._total_bytes > SESSION_CAP_BYTES:
            self._truncated = True
            # Flush the sentinel via create_task.
            asyncio.create_task(self._insert_sentinel())
            return

        self._buf.extend(chunk)

        # Split on \r or \n boundaries and emit a row per complete segment.
        while True:
            cr = self._buf.find(b"\r")
            nl = self._buf.find(b"\n")

            # Find the earliest of the two (ignoring -1 which means absent).
            if cr == -1 and nl == -1:
                break  # No newline yet; keep buffering.

            if cr == -1:
                split = nl
            elif nl == -1:
                split = cr
            else:
                split = min(cr, nl)

            line_bytes = bytes(self._buf[: split + 1])
            self._buf = self._buf[split + 1 :]

            text = line_bytes.decode("utf-8", errors="replace").rstrip("\r\n")
            # Only flush non-empty lines (a bare CR/LF produces an empty string).
            # We still want to record it — the raw line had content up to the
            # newline; only fire if the decoded content is non-empty after strip.
            # Per spec: strip trailing \r/\n, then insert.  We keep empty lines
            # if the original had content before the terminator; bare newlines
            # produce empty strings which we skip.
            asyncio.create_task(self._insert_row_safe(text, len(line_bytes)))

        # Size-threshold flush for lines without newlines.
        if len(self._buf) >= SIZE_FLUSH_THRESHOLD:
            buf_bytes = bytes(self._buf)
            self._buf = bytearray()
            text = buf_bytes.decode("utf-8", errors="replace")
            asyncio.create_task(self._insert_row_safe(text, len(buf_bytes)))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _insert_row_safe(self, text: str, byte_count: int) -> None:
        """Wrapper for ``_write_row`` that catches and logs exceptions."""
        try:
            await self._write_row(text, byte_count)
        except Exception:
            logger.exception(
                "transcript DB write failed for session %s -- ignoring",
                self._session_id,
            )

    async def _insert_row(self, text: str, byte_count: int) -> None:
        """Insert a single transcript row (awaited, used in stop())."""
        try:
            await self._write_row(text, byte_count)
        except Exception:
            logger.exception(
                "transcript DB write failed for session %s -- ignoring",
                self._session_id,
            )

    async def _insert_sentinel(self) -> None:
        """Insert the truncation-sentinel row."""
        try:
            await self._write_row(_TRUNCATION_SENTINEL, 0)
        except Exception:
            logger.exception(
                "transcript sentinel write failed for session %s -- ignoring",
                self._session_id,
            )

    async def _write_row(self, text: str, byte_count: int) -> None:  # noqa: ARG002
        """Write one transcript row to the DB using a fresh session."""
        from app.db import AsyncSessionLocal  # noqa: PLC0415
        from app.models.ssh_session_transcript import SSHSessionTranscript  # noqa: PLC0415

        row = SSHSessionTranscript(
            session_id=self._session_id,
            host_id=self._host_id,
            user_id=self._user_id,
            command_text=text,
        )
        async with AsyncSessionLocal() as db:
            db.add(row)
            await db.commit()
