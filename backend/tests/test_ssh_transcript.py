"""Tests for SSH session transcript capture (SEC-09).

Covers:
- Per-line buffering (newline flush)
- Non-newline flush on size threshold
- Per-session cap + truncation sentinel
- DB write failure does not terminate the WS session
- GET /api/audit-log/ssh-sessions/{session_id}/transcript endpoint
"""

import asyncio
import logging

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# TranscriptWriter unit tests (no real DB)
# ---------------------------------------------------------------------------


class TestTranscriptWriterBuffering:
    """Unit tests using a mocked DB write so the focus is buffering logic."""

    def _make_writer(self, session_id="test-session"):
        from app.ssh_terminal.transcript import TranscriptWriter

        return TranscriptWriter(session_id=session_id, host_id=1, user_id=1)

    async def test_per_line_flush_produces_two_rows(self):
        """Bytes 'ls\\nwhoami\\n' should produce exactly two flushed rows."""
        written: list[str] = []

        writer = self._make_writer()

        async def fake_write_row(text, byte_count):
            written.append(text)

        writer._write_row = fake_write_row

        await writer.start()
        writer.feed(b"ls\nwhoami\n")
        # Give the consumer a moment to process.
        await asyncio.sleep(0.05)
        await writer.stop()

        assert len(written) == 2, f"Expected 2 rows, got {len(written)}: {written}"
        assert written[0] == "ls"
        assert written[1] == "whoami"

    async def test_cr_flush(self):
        """Carriage return also triggers a flush."""
        written: list[str] = []

        writer = self._make_writer()

        async def fake_write_row(text, byte_count):
            written.append(text)

        writer._write_row = fake_write_row

        await writer.start()
        writer.feed(b"cmd\r")
        await asyncio.sleep(0.05)
        await writer.stop()

        assert len(written) == 1
        assert written[0] == "cmd"

    async def test_size_threshold_flush(self):
        """Chunk larger than SIZE_FLUSH_THRESHOLD flushes without a newline."""
        from app.ssh_terminal.transcript import SIZE_FLUSH_THRESHOLD

        written: list[str] = []

        writer = self._make_writer()

        async def fake_write_row(text, byte_count):
            written.append(text)

        writer._write_row = fake_write_row

        big_chunk = b"x" * (SIZE_FLUSH_THRESHOLD + 1)

        await writer.start()
        writer.feed(big_chunk)
        # Allow tasks to run.
        await asyncio.sleep(0.05)
        await writer.stop()

        assert len(written) >= 1
        total_bytes = sum(len(t.encode()) for t in written)
        assert total_bytes == len(big_chunk)

    async def test_session_cap_produces_sentinel(self):
        """Exceeding SESSION_CAP_BYTES stops capture and writes the sentinel."""
        from app.ssh_terminal.transcript import _TRUNCATION_SENTINEL, SESSION_CAP_BYTES

        written: list[str] = []

        writer = self._make_writer()

        async def fake_write_row(text, byte_count):
            written.append(text)

        writer._write_row = fake_write_row

        # Feed newline-terminated 1 KiB chunks until well above cap.
        chunk = b"a" * 1023 + b"\n"  # 1024 bytes per chunk
        n_chunks = (SESSION_CAP_BYTES // len(chunk)) + 10  # well above cap

        await writer.start()
        for _ in range(n_chunks):
            if writer._truncated:
                break
            writer.feed(chunk)
            # Give the event loop a chance between feeds.
            await asyncio.sleep(0)

        await asyncio.sleep(0.1)
        await writer.stop()

        assert writer._truncated, "Writer should have set _truncated=True"
        assert _TRUNCATION_SENTINEL in written, (
            f"Sentinel not found in rows. Got: {written[-3:]}"
        )
        # Nothing should have been written after the sentinel.
        sentinel_idx = written.index(_TRUNCATION_SENTINEL)
        assert sentinel_idx == len(written) - 1, "Rows were written after the sentinel"

    async def test_db_error_does_not_raise(self):
        """A failing DB write must not propagate an exception."""
        writer = self._make_writer()

        async def exploding_write(text, byte_count):
            raise RuntimeError("simulated DB failure")

        writer._write_row = exploding_write

        await writer.start()
        writer.feed(b"echo hello\n")
        await asyncio.sleep(0.05)
        # stop() must complete cleanly.
        await writer.stop()
        # If we reach here without an exception, the test passes.

    async def test_db_error_logs_warning(self, caplog):
        """A failing DB write emits a warning/exception log."""
        writer = self._make_writer(session_id="err-session")

        async def exploding_write(text, byte_count):
            raise RuntimeError("db boom")

        writer._write_row = exploding_write

        with caplog.at_level(logging.WARNING, logger="app.ssh_terminal.transcript"):
            await writer.start()
            writer.feed(b"ls\n")
            await asyncio.sleep(0.1)
            await writer.stop()

        # _write_row raises; _insert_row_safe catches and logs.
        assert any(
            "transcript" in r.message.lower() or "err-session" in r.message
            for r in caplog.records
        ), f"Expected transcript log, got: {[r.message for r in caplog.records]}"

    async def test_no_keystrokes_produces_no_rows(self):
        """A session with no feed() calls produces zero rows."""
        written: list[str] = []

        writer = self._make_writer()

        async def fake_write_row(text, byte_count):
            written.append(text)

        writer._write_row = fake_write_row

        await writer.start()
        await writer.stop()

        assert written == [], f"Expected no rows, got: {written}"

    async def test_partial_buffer_flushed_on_stop(self):
        """Bytes without a trailing newline are flushed when stop() is called."""
        written: list[str] = []

        writer = self._make_writer()

        async def fake_write_row(text, byte_count):
            written.append(text)

        writer._write_row = fake_write_row

        await writer.start()
        writer.feed(b"partial command")  # no newline
        await asyncio.sleep(0.01)
        await writer.stop()

        # The partial buffer should be flushed on stop().
        assert len(written) == 1
        assert written[0] == "partial command"


# ---------------------------------------------------------------------------
# Integration test: DB write path
# ---------------------------------------------------------------------------


class TestTranscriptDBWrite:
    async def test_rows_written_to_db(self, db):
        """End-to-end: feed 2 lines -> 2 rows appear in ssh_session_transcripts."""
        from sqlalchemy import select

        from app.models.ssh_session_transcript import SSHSessionTranscript
        from app.ssh_terminal.transcript import TranscriptWriter

        session_id = "integ-test-session-001"

        writer = TranscriptWriter(session_id=session_id, host_id=None, user_id=None)
        # Monkeypatch _write_row to use the test session instead of AsyncSessionLocal.
        async def write_via_test_db(text, byte_count):
            row = SSHSessionTranscript(
                session_id=session_id,
                host_id=None,
                user_id=None,
                command_text=text,
            )
            db.add(row)
            await db.flush()

        writer._write_row = write_via_test_db

        await writer.start()
        writer.feed(b"ls\n")
        writer.feed(b"whoami\n")
        await asyncio.sleep(0.05)
        await writer.stop()

        result = await db.execute(
            select(SSHSessionTranscript)
            .where(SSHSessionTranscript.session_id == session_id)
            .order_by(SSHSessionTranscript.id.asc())
        )
        rows = result.scalars().all()
        assert len(rows) == 2
        assert rows[0].command_text == "ls"
        assert rows[1].command_text == "whoami"


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------


class TestTranscriptEndpoint:
    async def test_returns_rows_in_order(self, superuser_client, db):
        """GET /api/audit-log/ssh-sessions/{session_id}/transcript returns rows asc."""
        from app.models.ssh_session_transcript import SSHSessionTranscript

        session_id = "api-test-session-001"
        for line in ["ls", "pwd", "whoami"]:
            row = SSHSessionTranscript(
                session_id=session_id,
                host_id=None,
                user_id=None,
                command_text=line,
            )
            db.add(row)
        await db.flush()

        resp = await superuser_client.get(
            f"/api/audit-log/ssh-sessions/{session_id}/transcript"
        )
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        assert len(rows) == 3
        assert [r["command_text"] for r in rows] == ["ls", "pwd", "whoami"]

    async def test_unknown_session_returns_404(self, superuser_client, db):
        """Unknown session_id returns 404."""
        resp = await superuser_client.get(
            "/api/audit-log/ssh-sessions/nonexistent-session-xyz/transcript"
        )
        assert resp.status_code == 404, resp.text

    async def test_regular_user_can_access(self, regular_user_client, db):
        """Regular (non-superuser) authenticated user can access the endpoint."""
        from app.models.ssh_session_transcript import SSHSessionTranscript

        session_id = "regular-user-test-session-001"
        row = SSHSessionTranscript(
            session_id=session_id,
            host_id=None,
            user_id=None,
            command_text="ls",
        )
        db.add(row)
        await db.flush()

        resp = await regular_user_client.get(
            f"/api/audit-log/ssh-sessions/{session_id}/transcript"
        )
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        assert len(rows) == 1
        assert rows[0]["command_text"] == "ls"

    async def test_empty_session_returns_404(self, superuser_client, db):
        """Unknown session_id returns 404 (no transcript rows exist)."""
        resp = await superuser_client.get(
            "/api/audit-log/ssh-sessions/nonexistent-session-no-rows/transcript"
        )
        assert resp.status_code == 404
