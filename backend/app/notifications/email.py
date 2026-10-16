"""SMTP transport, on the standard library.

``smtplib`` is blocking; callers run it in a thread (``asyncio.to_thread``)
so a slow server cannot stall an event loop. Every failure is turned into
the server's own words by :func:`describe` — "535 5.7.8 Username and
Password not accepted" tells an operator what to fix, where "send failed"
sends them to the logs.
"""

from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

#: Per-operation socket timeout. Generous for a relay across the
#: internet, short enough that a dead server fails one drain rather than
#: holding a worker for minutes.
TIMEOUT_SECONDS = 20

#: Longest subject sent. Mail clients cut far shorter; the rest is noise.
MAX_SUBJECT_CHARS = 200


class SMTPSendError(Exception):
    """The connection, TLS handshake or login failed — nothing was sent."""


@dataclass(frozen=True)
class SMTPConfig:
    host: str
    port: int
    tls_mode: str
    username: str | None
    password: str | None
    from_address: str


@dataclass(frozen=True)
class OutgoingEmail:
    to: str
    subject: str
    body: str


def describe(exc: BaseException) -> str:
    """The server's own error text, or the socket's, without a traceback."""
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        refused = "; ".join(
            f"{addr}: {code} {_text(msg)}" for addr, (code, msg) in exc.recipients.items()
        )
        return f"recipient refused — {refused}"
    if isinstance(exc, smtplib.SMTPResponseException):
        return f"{exc.smtp_code} {_text(exc.smtp_error)}"
    if isinstance(exc, ssl.SSLError):
        return f"TLS error: {exc.reason or exc}"
    if isinstance(exc, TimeoutError):
        return f"timed out after {TIMEOUT_SECONDS}s"
    if isinstance(exc, OSError):
        return f"could not connect: {exc.strerror or exc}"
    return str(exc) or exc.__class__.__name__


def _text(raw: bytes | str) -> str:
    return raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)


def _subject(subject: str) -> str:
    """One line, bounded. A newline in a header is how header injection
    works, and the subject is built from alert names someone else chose."""
    flat = " ".join(subject.split())
    if len(flat) > MAX_SUBJECT_CHARS:
        flat = flat[: MAX_SUBJECT_CHARS - 1] + "…"
    return flat


def build(config: SMTPConfig, email: OutgoingEmail) -> EmailMessage:
    message = EmailMessage()
    message["From"] = config.from_address
    message["To"] = email.to
    message["Subject"] = _subject(email.subject)
    message["Date"] = formatdate(localtime=False)
    domain = config.from_address.rpartition("@")[2] or None
    message["Message-ID"] = make_msgid(domain=domain)
    message["Auto-Submitted"] = "auto-generated"
    message.set_content(email.body)
    return message


def _connect(config: SMTPConfig) -> smtplib.SMTP:
    context = ssl.create_default_context()
    if config.tls_mode == "tls":
        client: smtplib.SMTP = smtplib.SMTP_SSL(
            config.host, config.port, timeout=TIMEOUT_SECONDS, context=context
        )
    else:
        client = smtplib.SMTP(config.host, config.port, timeout=TIMEOUT_SECONDS)
        if config.tls_mode == "starttls":
            client.starttls(context=context)
    if config.username:
        client.login(config.username, config.password or "")
    return client


def send(config: SMTPConfig, emails: list[OutgoingEmail]) -> list[str | None]:
    """Send ``emails`` over one connection.

    Returns one entry per email: ``None`` if the server accepted it, else
    why it did not. Raises :class:`SMTPSendError` when nothing could be
    sent at all — the server is unreachable, TLS failed, the login was
    refused — because then every email failed for the same reason.
    """
    try:
        client = _connect(config)
    except Exception as exc:
        raise SMTPSendError(describe(exc)) from exc

    results: list[str | None] = []
    try:
        for email in emails:
            try:
                client.send_message(build(config, email))
                results.append(None)
            except (smtplib.SMTPException, OSError, ValueError) as exc:
                results.append(describe(exc))
                # SMTPException subclasses OSError, so "the socket is gone"
                # has to exclude it explicitly: a refused recipient leaves
                # the connection perfectly usable for the next email.
                gone = isinstance(exc, smtplib.SMTPServerDisconnected) or (
                    isinstance(exc, OSError) and not isinstance(exc, smtplib.SMTPException)
                )
                if gone:
                    results.extend(describe(exc) for _ in emails[len(results) :])
                    break
    finally:
        try:
            client.quit()
        except Exception:  # noqa: BLE001 - closing a connection that may already be dead
            client.close()
    return results
