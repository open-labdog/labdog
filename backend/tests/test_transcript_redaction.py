"""SEC-22: the terminal transcript must not capture secrets.

Everything typed at a `sudo` prompt, a `mysql -p`, an `openssl` passphrase
or any pasted token was stored verbatim in
``ssh_session_transcripts.command_text``, readable through
``GET /api/audit-log/ssh-sessions/{id}``. The host suppressing its own echo
never protected any of it: the keystrokes travel over the WebSocket whether
or not the host echoes them back.

Two mechanisms, because neither is sufficient alone:

* Host output is watched for a password prompt, so the line that answers
  one is replaced with a placeholder. This is what catches a bare secret —
  ``hunter2`` has nothing about it that marks it as one.
* Every row is passed through ``app.ai.redaction.redact`` on the way to the
  database, which catches secrets that arrive as ordinary text —
  ``export TOKEN=…``, a pasted PEM, a URL with credentials.

The tests assert on what reaches ``_write_row``, since that is the boundary
the column sits behind.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.ssh_terminal.transcript import _SUPPRESSED, TranscriptWriter


@pytest.fixture
def writer():
    return TranscriptWriter(session_id="s-1", host_id=1, user_id=1)


async def _rows_from(writer, *, output: bytes | None = None, typed: bytes = b"") -> list[str]:
    """Drive the writer and return the texts that reached the database.

    Fakes the *session*, not ``_write_row`` — redaction happens inside
    ``_write_row``, so patching it would bypass half of what these tests
    exist to check. Everything below the WebSocket runs for real.
    """
    written: list[str] = []

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def add(self, row):
            written.append(row.command_text)

        async def commit(self):
            return None

    with patch("app.db.AsyncSessionLocal", lambda: _FakeSession()):
        if output is not None:
            writer.observe_output(output)
        writer.feed(typed)
        await writer.stop()
        # feed() dispatches via create_task; let them run.
        import asyncio

        await asyncio.sleep(0)
        await asyncio.sleep(0)
    return written


class TestAPasswordTypedAtAPromptIsNotStored:
    @pytest.mark.parametrize(
        "prompt",
        [
            b"[sudo] password for dennis: ",
            b"Password: ",
            b"Enter passphrase for key '/root/.ssh/id_ed25519': ",
            b"\x1b[1;32mPassword:\x1b[0m ",
            b"Verification code: ",
        ],
    )
    async def test_the_answer_is_replaced_with_a_placeholder(self, writer, prompt):
        rows = await _rows_from(writer, output=prompt, typed=b"hunter2\r")
        assert _SUPPRESSED in rows
        assert not any("hunter2" in r for r in rows)

    async def test_the_fact_that_a_secret_was_entered_is_still_recorded(self, writer):
        """Dropping the row entirely would lose audit signal — the point is
        to keep the event and discard the value."""
        rows = await _rows_from(writer, output=b"Password: ", typed=b"hunter2\r")
        assert rows == [_SUPPRESSED]

    async def test_suppression_ends_with_the_newline_that_submits_it(self, writer):
        rows = await _rows_from(writer, output=b"Password: ", typed=b"hunter2\rwhoami\r")
        assert _SUPPRESSED in rows
        assert "whoami" in rows, "the command after the password must still be recorded"
        assert not any("hunter2" in r for r in rows)

    async def test_a_secret_left_half_typed_at_session_close_is_suppressed(self, writer):
        rows = await _rows_from(writer, output=b"Password: ", typed=b"hunter2")
        assert rows == [_SUPPRESSED]


class TestOrdinaryCommandsAreStillRecorded:
    """The regression half. A transcript that records nothing useful is one
    nobody keeps, and this is an audit surface."""

    async def test_a_normal_command_is_stored_verbatim(self, writer):
        rows = await _rows_from(writer, output=b"dennis@web-01:~$ ", typed=b"ls -la /etc\r")
        assert "ls -la /etc" in rows

    @pytest.mark.parametrize(
        "output",
        [
            b"dennis@web-01:~$ ",
            b"the password was changed successfully\n",
            b"Password saved to /etc/foo\n",
            b"total 48\n",
        ],
    )
    async def test_output_that_merely_mentions_a_password_does_not_suppress(self, writer, output):
        rows = await _rows_from(writer, output=output, typed=b"whoami\r")
        assert "whoami" in rows
        assert _SUPPRESSED not in rows

    async def test_no_output_seen_at_all_still_records(self, writer):
        rows = await _rows_from(writer, typed=b"uptime\r")
        assert "uptime" in rows


class TestRedactionCatchesSecretsTypedAsText:
    """The second mechanism: no prompt involved, so suppression cannot help."""

    async def test_an_inline_credential_is_redacted(self, writer):
        rows = await _rows_from(writer, typed=b"export API_KEY=sk-ant-api03-abcdefghijklmnop\r")
        joined = " ".join(rows)
        assert "sk-ant-api03-abcdefghijklmnop" not in joined

    async def test_a_url_with_credentials_is_redacted(self, writer):
        rows = await _rows_from(writer, typed=b"git clone https://user:hunter2@example.com/r\r")
        assert not any("hunter2" in r for r in rows)

    async def test_a_multi_line_pasted_private_key_has_its_body_suppressed(self, writer):
        """The third mechanism, and the reason it has to exist.

        Neither of the others covers this. The redactor's PEM rule needs
        BEGIN and END in one string and a transcript stores one row per
        line; its long-blob rule wants 120+ characters and a PEM body line
        is 64. So `cat > id_rsa <<EOF` went in line by line, in the clear.
        """
        from app.ssh_terminal.transcript import _SUPPRESSED_KEY

        pasted = (
            b"cat > /root/.ssh/id_ed25519 <<'EOF'\r"
            b"-----BEGIN OPENSSH PRIVATE KEY-----\r"
            b"NOTAREALKEYBODYAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\r"
            b"NOTAREALKEYBODYBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB\r"
            b"-----END OPENSSH PRIVATE KEY-----\r"
            b"EOF\r"
        )
        rows = await _rows_from(writer, typed=pasted)

        assert not any("NOTAREALKEYBODY" in r for r in rows), "key body was stored"
        assert _SUPPRESSED_KEY in rows
        # The markers stay: "a key was pasted here" is what an audit reader
        # needs, and a marker is not a secret.
        assert any("BEGIN OPENSSH PRIVATE KEY" in r for r in rows)
        # And the surrounding commands are still legible.
        assert any("cat > /root/.ssh/id_ed25519" in r for r in rows)
        assert "EOF" in rows

    async def test_suppression_ends_at_the_pem_end_marker(self, writer):
        pasted = b"-----BEGIN RSA PRIVATE KEY-----\rMIIEow\r-----END RSA PRIVATE KEY-----\rls -la\r"
        rows = await _rows_from(writer, typed=pasted)
        assert "ls -la" in rows, "commands after the key must be recorded again"


class TestTheWriteRowBoundaryRedacts:
    async def test_write_row_redacts_before_persisting(self, writer):
        """The real _write_row, with the DB session faked out."""
        captured = {}

        class _FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            def add(self, row):
                captured["text"] = row.command_text

            async def commit(self):
                return None

        with patch("app.db.AsyncSessionLocal", return_value=_FakeSession()):
            await writer._write_row("export TOKEN=sk-ant-api03-abcdefghijklmnop", 40)

        assert "sk-ant-api03-abcdefghijklmnop" not in captured["text"]


class TestOutputIsNeverStored:
    async def test_observe_output_writes_nothing(self, writer):
        write = AsyncMock()
        with patch.object(writer, "_write_row", write):
            writer.observe_output(b"Password: ")
            writer.observe_output(b"root:x:0:0:root:/root:/bin/bash\n")
        write.assert_not_awaited()
