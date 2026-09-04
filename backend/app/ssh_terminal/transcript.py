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
import re

from app.ai.redaction import redact

logger = logging.getLogger(__name__)

# Per-session stdin capture cap (1 MiB).
SESSION_CAP_BYTES: int = 1 * 1024 * 1024

# Flush after accumulating this many bytes even without a newline.
SIZE_FLUSH_THRESHOLD: int = 8 * 1024  # 8 KiB

_TRUNCATION_SENTINEL: str = "[transcript truncated — per-session 1 MiB cap exceeded]"

#: Written in place of whatever was typed at a password prompt.
_SUPPRESSED: str = "[password input suppressed]"

#: Host output that means "the next thing typed is a secret".
#:
#: SEC-22: everything the operator typed was stored verbatim, so a sudo
#: password, a ``mysql -p``, an ``openssl`` passphrase or a pasted token
#: landed in plaintext in ``ssh_session_transcripts.command_text``. The
#: host suppressing its own echo never protected any of it — the keystrokes
#: travel over the WebSocket whether or not the host echoes them back.
#:
#: Matched against the *tail* of the most recent output chunk, because a
#: prompt is the last thing written before the host waits for input. The
#: trailing ``\s*$`` allows the space that almost every prompt ends with.
_PASSWORD_PROMPT = re.compile(
    r"(?:"
    r"password(?:\s+for\s+[^:]{0,64})?"  # sudo, su, login
    r"|passphrase(?:\s+for\s+[^:]{0,64})?"  # ssh-add, openssl, gpg
    r"|enter\s+(?:the\s+)?(?:password|passphrase|pin)"
    r"|verification\s+code"  # 2FA
    r"|otp"
    r")"
    r"\s*:\s*$",
    re.IGNORECASE,
)

#: Escape sequences, stripped before matching so a coloured or
#: cursor-positioned prompt still looks like a prompt.
_ANSI = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x07\x1B]*(?:\x07|\x1B\\))")

#: A private key pasted into the terminal, which neither other mechanism
#: catches. The redactor's PEM rule needs the BEGIN and END markers in one
#: string, and a transcript stores one row per line; its long-blob rule
#: wants 120+ characters, and a PEM body line is 64. So a
#: ``cat > id_rsa <<EOF`` paste would have gone in line by line, in the
#: clear. Suppression starts at the BEGIN marker and ends at END.
_PEM_BEGIN = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
_PEM_END = re.compile(r"-----END [A-Z0-9 ]*PRIVATE KEY-----")

#: Written in place of the body of a pasted private key.
_SUPPRESSED_KEY: str = "[private key input suppressed]"

#: How much of an output chunk to test. A prompt is short and sits at the
#: end; scanning the whole of a 64 KiB screen redraw would be wasteful and
#: would match a prompt scrolling past in, say, a log file.
_PROMPT_TAIL_BYTES: int = 256


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
        #: Set when host output ends in a password prompt; cleared by the
        #: newline that submits the secret.
        self._awaiting_secret: bool = False
        #: Set between a typed PEM BEGIN marker and its END.
        self._in_pem: bool = False

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
        if self._awaiting_secret and text:
            # Session closed with a secret half-typed.
            text = _SUPPRESSED
        if text:
            await self._insert_row(text, byte_count)

    def observe_output(self, chunk: bytes) -> None:
        """Watch host output for a password prompt. Never stored.

        The transcript records only what the operator typed; this exists
        solely to know when the *next* thing typed is a secret. Called from
        the ssh→ws loop, so it must stay cheap and must never raise into
        the stream.
        """
        if self._truncated or not chunk:
            return
        try:
            tail = chunk[-_PROMPT_TAIL_BYTES:].decode("utf-8", errors="replace")
            # Strip ANSI so a coloured prompt still matches.
            tail = _ANSI.sub("", tail).rstrip("\x00")
            self._awaiting_secret = bool(_PASSWORD_PROMPT.search(tail))
        except Exception:  # pragma: no cover - defensive
            logger.debug("transcript: prompt scan failed", exc_info=True)

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
            #
            # SEC-22: if the host just printed a password prompt, this line is
            # the answer to it. Record that a secret was entered — the audit
            # trail should not lose the fact — but not the secret. The newline
            # that submits it also ends the suppression.
            if self._awaiting_secret:
                self._awaiting_secret = False
                asyncio.create_task(self._insert_row_safe(_SUPPRESSED, len(line_bytes)))
            else:
                asyncio.create_task(self._insert_row_safe(self._filter_pem(text), len(line_bytes)))

        # Size-threshold flush for lines without newlines.
        if len(self._buf) >= SIZE_FLUSH_THRESHOLD:
            buf_bytes = bytes(self._buf)
            self._buf = bytearray()
            text = buf_bytes.decode("utf-8", errors="replace")
            if self._awaiting_secret:
                # Still mid-secret: an 8 KiB "password" is not a password, but
                # emitting it because it got long would defeat the point.
                text = _SUPPRESSED
            asyncio.create_task(self._insert_row_safe(text, len(buf_bytes)))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _filter_pem(self, text: str) -> str:
        """Collapse the body of a pasted private key to a placeholder.

        The markers themselves are kept: "a key was pasted here" is exactly
        the sort of thing an audit reader needs to see, and the marker is
        not the secret.
        """
        if self._in_pem:
            if _PEM_END.search(text):
                self._in_pem = False
                return text
            return _SUPPRESSED_KEY
        if _PEM_BEGIN.search(text) and not _PEM_END.search(text):
            # A one-line paste containing both markers is handled by the
            # redactor on the write path; only a multi-line one needs state.
            self._in_pem = True
        return text

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

        # Second line of defence. The prompt heuristic catches a secret typed
        # at a prompt; this catches the ones that arrive as ordinary text —
        # `export TOKEN=…`, a pasted PEM block, `mysql -pSECRET`, a URL with
        # credentials in it. Neither alone is sufficient and neither is
        # exact; between them the common shapes stop reaching the column.
        row = SSHSessionTranscript(
            session_id=self._session_id,
            host_id=self._host_id,
            user_id=self._user_id,
            command_text=redact(text),
        )
        async with AsyncSessionLocal() as db:
            db.add(row)
            await db.commit()
