"""Email notifications: queueing, the events that queue, and delivery.

Nothing here talks to a mail server. Delivery is tested through
``drain``'s injectable ``send``, and the transport through a stand-in for
``smtplib.SMTP`` — what matters is what LabDog asks the server to do and
what it records when the server says no.
"""

from __future__ import annotations

import smtplib
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.ai import approvals
from app.ai.models import AISession, AIToolCall, AlertEvent
from app.ai.safety import classify_command
from app.config import settings
from app.crypto import decrypt_ssh_key, get_master_key
from app.models.app_setting import AppSetting
from app.models.user import User
from app.notifications import email as smtp
from app.notifications.delivery import FULL_ITEMS, MAX_ATTEMPTS, NO_URL_ERROR, compose, drain
from app.notifications.models import Notification, NotificationSubscription, SMTPSettings
from app.notifications.schemas import EmailSettingsUpdate
from app.notifications.service import (
    notify,
    notify_alert_fired,
    notify_remediation,
    scan_expiring_approvals,
)
from app.notifications.urls import validate_public_url
from app.settings_service import invalidate_cache
from tests.conftest import create_host

URL = "https://labdog.example.com"


async def _set(db, key: str, value: str) -> None:
    existing = (
        await db.execute(select(AppSetting).where(AppSetting.key == key))
    ).scalar_one_or_none()
    if existing:
        existing.value = value
    else:
        db.add(AppSetting(key=key, value=value))
    await db.flush()
    invalidate_cache(key)


@pytest.fixture(autouse=True)
async def _clear_settings_cache():
    invalidate_cache()
    yield
    invalidate_cache()


@pytest.fixture
async def smtp_on(db):
    row = SMTPSettings(
        id=1,
        enabled=True,
        host="mail.home.arpa",
        port=587,
        tls_mode="starttls",
        from_address="labdog@home.arpa",
    )
    db.add(row)
    await db.flush()
    await _set(db, "notifications.public_url", URL)
    return row


async def _user(db, *, events: tuple[str, ...] = (), active: bool = True) -> User:
    user = User(
        email=f"u{uuid.uuid4().hex[:8]}@home.arpa",
        hashed_password="x",
        is_active=active,
        is_superuser=False,
        is_verified=True,
    )
    db.add(user)
    await db.flush()
    for event in events:
        db.add(NotificationSubscription(user_id=user.id, event_type=event, channel="email"))
    await db.flush()
    return user


async def _queued(db, event_type: str | None = None) -> list[Notification]:
    stmt = select(Notification).order_by(Notification.id)
    if event_type:
        stmt = stmt.where(Notification.event_type == event_type)
    return list((await db.execute(stmt)).scalars().all())


async def _alert(db, host_id: int | None = None, **over) -> AlertEvent:
    values = {
        "source": "grafana_webhook",
        "fingerprint": uuid.uuid4().hex,
        "alertname": "NginxDown",
        "severity": "critical",
        "status": "firing",
        "labels": {"alertname": "NginxDown", "instance": "web1:9100"},
        "annotations": {"summary": "nginx is not answering"},
        "starts_at": datetime.now(UTC),
        "host_id": host_id,
    }
    values.update(over)
    event = AlertEvent(**values)
    db.add(event)
    await db.flush()
    return event


def _session_cm(db):
    @asynccontextmanager
    async def _cm():
        yield db

    return _cm


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestPublicURL:
    def test_it_is_normalised(self) -> None:
        assert validate_public_url(" https://labdog.example.com/ ") == URL

    def test_empty_means_unset(self) -> None:
        assert validate_public_url("") == ""

    @pytest.mark.parametrize("bad", ["labdog.example.com", "ftp://x", "https://x/?a=1", "https://"])
    def test_anything_else_is_refused(self, bad: str) -> None:
        with pytest.raises(ValueError):
            validate_public_url(bad)


class TestAddresses:
    def test_a_home_network_domain_is_accepted(self) -> None:
        """RFC 8375's home.arpa is the domain a homelab relay is likeliest to
        use, and pydantic's EmailStr refuses it as special-use."""
        body = EmailSettingsUpdate(enabled=True, host="mail", from_address="labdog@home.arpa")
        assert body.from_address == "labdog@home.arpa"

    @pytest.mark.parametrize("bad", ["LabDog <labdog@x.org>", "labdog", "@x.org", "a b@x.org"])
    def test_anything_but_a_bare_address_is_refused(self, bad: str) -> None:
        with pytest.raises(ValueError):
            EmailSettingsUpdate(from_address=bad)

    def test_switching_on_without_a_server_is_refused(self) -> None:
        with pytest.raises(ValueError, match="server and a From address"):
            EmailSettingsUpdate(enabled=True, host="", from_address="labdog@home.arpa")


# ---------------------------------------------------------------------------
# Queueing
# ---------------------------------------------------------------------------


class TestNotify:
    async def test_one_row_per_subscriber(self, db, smtp_on) -> None:
        a = await _user(db, events=("alert_fired",))
        b = await _user(db, events=("alert_fired", "approval_requested"))
        await _user(db, events=("approval_requested",))

        assert await notify(db, "alert_fired", subject="s", body="b", link_path="/alerts") == 2
        rows = await _queued(db)
        assert {r.user_id for r in rows} == {a.id, b.id}
        assert rows[0].subject == "[LabDog] s"
        assert rows[0].status == "pending"

    async def test_nothing_is_queued_while_email_is_off(self, db) -> None:
        """Queuing anyway would deliver a backlog of stale alerts the moment
        someone switched email on."""
        await _user(db, events=("alert_fired",))
        assert await notify(db, "alert_fired", subject="s", body="b") == 0
        assert await _queued(db) == []

    async def test_a_deactivated_user_gets_nothing(self, db, smtp_on) -> None:
        await _user(db, events=("alert_fired",), active=False)
        assert await notify(db, "alert_fired", subject="s", body="b") == 0

    async def test_a_dedupe_key_makes_a_repeat_free(self, db, smtp_on) -> None:
        await _user(db, events=("approval_expiring",))
        for _ in range(3):
            await notify(db, "approval_expiring", subject="s", body="b", dedupe_key="k:1")
        assert len(await _queued(db)) == 1

    async def test_an_unknown_event_is_a_programming_error(self, db, smtp_on) -> None:
        with pytest.raises(ValueError, match="unknown notification event"):
            await notify(db, "made_up", subject="s", body="b")

    async def test_a_failure_cannot_break_the_callers_transaction(self, db, smtp_on) -> None:
        """notify runs inside alert intake and the approval gate. Whatever
        goes wrong in it must leave their work committable."""
        host = await create_host(db)

        async def boom(_db):
            raise RuntimeError("database hiccup")

        with patch("app.notifications.service.load_smtp", boom):
            assert await notify(db, "alert_fired", subject="s", body="b") == 0
        event = await _alert(db, host.id)
        await db.commit()
        assert event.id is not None


# ---------------------------------------------------------------------------
# The events
# ---------------------------------------------------------------------------


class TestAlertFired:
    async def test_the_message_names_the_alert_and_host(self, db, smtp_on) -> None:
        await _user(db, events=("alert_fired",))
        host = await create_host(db, hostname="web1.home.arpa")
        event = await _alert(db, host.id)

        await notify_alert_fired(db, event)
        [row] = await _queued(db, "alert_fired")
        assert row.subject == "[LabDog] Firing: NginxDown (critical) on web1.home.arpa"
        assert "nginx is not answering" in row.body
        assert "instance: web1:9100" in row.body
        assert row.link_path == "/alerts"

    async def test_secrets_in_alert_text_are_redacted(self, db, smtp_on) -> None:
        await _user(db, events=("alert_fired",))
        event = await _alert(
            db,
            annotations={"summary": "login failed with password=hunter2hunter2 for admin"},
        )
        await notify_alert_fired(db, event)
        [row] = await _queued(db, "alert_fired")
        assert "hunter2hunter2" not in row.body

    async def test_the_webhook_queues_it(self, db, smtp_on, external_client, monkeypatch) -> None:
        await _user(db, events=("alert_fired",))
        await _set(db, "ai.alert_intake_enabled", "1")
        monkeypatch.setattr(settings.alerts, "webhook_token", "t" * 40)
        payload = {
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {"alertname": "DiskFull", "severity": "warning"},
                    "annotations": {},
                    "startsAt": datetime.now(UTC).isoformat(),
                    "fingerprint": uuid.uuid4().hex,
                }
            ],
        }
        with patch("app.api.webhooks.celery_app.send_task"):
            response = await external_client.post(
                "/api/webhooks/grafana-alerts",
                json=payload,
                headers={"Authorization": f"Bearer {'t' * 40}"},
            )
        assert response.status_code == 200, response.text
        [row] = await _queued(db, "alert_fired")
        assert "DiskFull" in row.subject


async def _parked(db, make_session, host) -> tuple[AISession, object]:
    session = await make_session(autonomy_level="approval", target_host_ids=[host.id])
    approval = await approvals.park(
        db,
        session,
        tool_name="run_ssh_command",
        arguments={
            "host_id": host.id,
            "command": "systemctl restart nginx",
            "purpose": "nginx is down",
        },
        verdict=classify_command("systemctl restart nginx"),
    )
    return session, approval


class TestApprovals:
    async def test_parking_queues_a_request(self, db, smtp_on, make_session) -> None:
        await _user(db, events=("approval_requested",))
        host = await create_host(db, hostname="web1.home.arpa")
        session, _ = await _parked(db, make_session, host)

        [row] = await _queued(db, "approval_requested")
        assert row.subject == (
            "[LabDog] Approval needed on web1.home.arpa: systemctl restart nginx"
        )
        assert "nginx is down" in row.body
        assert "Nothing in this email can approve it" in row.body
        assert row.link_path == f"/assistant?session={session.id}"

    async def test_an_approval_about_to_expire_is_warned_about_once(
        self, db, smtp_on, make_session
    ) -> None:
        await _user(db, events=("approval_expiring",))
        host = await create_host(db)
        _, approval = await _parked(db, make_session, host)
        approval.expires_at = datetime.now(UTC) + timedelta(minutes=90)
        await db.flush()

        assert await scan_expiring_approvals(db) == 1
        await scan_expiring_approvals(db)
        assert len(await _queued(db, "approval_expiring")) == 1

    async def test_one_further_out_is_not_warned_about_yet(self, db, smtp_on, make_session) -> None:
        await _user(db, events=("approval_expiring",))
        host = await create_host(db)
        await _parked(db, make_session, host)  # expires in 24h by default
        await scan_expiring_approvals(db)
        assert await _queued(db, "approval_expiring") == []

    async def test_zero_hours_turns_the_warning_off(self, db, smtp_on, make_session) -> None:
        await _set(db, "notifications.approval_expiry_warning_hours", "0")
        await _user(db, events=("approval_expiring",))
        host = await create_host(db)
        _, approval = await _parked(db, make_session, host)
        approval.expires_at = datetime.now(UTC) + timedelta(minutes=10)
        await db.flush()
        assert await scan_expiring_approvals(db) == 0

    async def test_the_reaper_queues_the_expiry(self, db, smtp_on, make_session) -> None:
        from app.tasks.ai_approvals import _expire_stale_approvals

        await _user(db, events=("approval_expired",))
        host = await create_host(db)
        _, approval = await _parked(db, make_session, host)
        approval.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await db.commit()

        with (
            patch("app.db.task_session", _session_cm(db)),
            patch("app.tasks.ai_approvals.celery_app.send_task"),
        ):
            await _expire_stale_approvals()

        [row] = await _queued(db, "approval_expired")
        assert "expired" in row.subject


class TestRemediation:
    async def _session(self, db, ai_provider, host, *, status="executed", snapshot=None):
        event = await _alert(db, host.id)
        session = AISession(
            provider_id=ai_provider.id,
            mode="alert_investigation",
            mission="fix it",
            autonomy_level="full_auto",
            status="succeeded",
            target_host_ids=[host.id],
            alert_event_id=event.id,
            report_markdown="nginx had crashed; restarting it brought it back.",
        )
        db.add(session)
        await db.flush()
        db.add(
            AIToolCall(
                session_id=session.id,
                tool_name="run_ssh_command",
                arguments={"host_id": host.id, "command": "systemctl restart nginx"},
                classification="mutating",
                target_host_id=host.id,
                status=status,
                result_summary="systemctl restart nginx (exit 0)",
                snapshot_name=snapshot,
            )
        )
        await db.flush()
        return session

    async def test_a_change_is_reported_with_its_snapshot(self, db, smtp_on, ai_provider) -> None:
        await _user(db, events=("alert_remediation",))
        host = await create_host(db, hostname="web1.home.arpa")
        session = await self._session(db, ai_provider, host, snapshot="labdog-ai-7-0001")

        assert await notify_remediation(db, session) == 1
        [row] = await _queued(db, "alert_remediation")
        assert row.subject == "[LabDog] Automatic fix on web1.home.arpa: NginxDown"
        assert "systemctl restart nginx" in row.body
        assert "labdog-ai-7-0001" in row.body
        assert "restarting it brought it back" in row.body

    async def test_nothing_is_sent_when_nothing_changed(self, db, smtp_on, ai_provider) -> None:
        await _user(db, events=("alert_remediation",))
        host = await create_host(db)
        session = await self._session(db, ai_provider, host, status="blocked")
        assert await notify_remediation(db, session) == 0

    async def test_the_session_task_reports_it(self, db, smtp_on, ai_provider) -> None:
        from app.tasks.ai_task import _report_remediation

        await _user(db, events=("alert_remediation",))
        host = await create_host(db)
        session = await self._session(db, ai_provider, host)
        await _report_remediation(db, session.id)
        assert len(await _queued(db, "alert_remediation")) == 1

    async def test_a_chat_session_is_not_reported(self, db, smtp_on, ai_provider) -> None:
        from app.tasks.ai_task import _report_remediation

        await _user(db, events=("alert_remediation",))
        host = await create_host(db)
        session = await self._session(db, ai_provider, host)
        session.mode = "chat"
        await db.flush()
        await _report_remediation(db, session.id)
        assert await _queued(db, "alert_remediation") == []


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


class FakeServer:
    """Stands in for ``email.send``; records what it was asked to send."""

    def __init__(self, results=None, raises: Exception | None = None) -> None:
        self.batches: list[list[smtp.OutgoingEmail]] = []
        self._results = results
        self._raises = raises

    def __call__(self, config, emails):
        self.batches.append(list(emails))
        if self._raises:
            raise self._raises
        return self._results or [None] * len(emails)


class TestDrain:
    async def test_everything_due_for_one_person_is_one_email(self, db, smtp_on) -> None:
        a = await _user(db, events=("alert_fired",))
        await _user(db, events=("alert_fired",))
        for n in range(3):
            await notify(
                db, "alert_fired", subject=f"alert {n}", body=f"body {n}", link_path="/alerts"
            )

        server = FakeServer()
        stats = await drain(db, send=server)

        assert stats == {"sent": 6, "failed": 0, "retrying": 0, "emails": 2}
        [batch] = server.batches
        mine = next(e for e in batch if e.to == a.email)
        assert mine.subject == "[LabDog] 3 notifications: 3 × alert fired"
        assert "body 0" in mine.body and "body 2" in mine.body
        assert f"{URL}/alerts" in mine.body
        assert all(r.status == "sent" and r.sent_at for r in await _queued(db))

    async def test_a_single_item_keeps_its_own_subject(self, db, smtp_on) -> None:
        await _user(db, events=("alert_fired",))
        await notify(db, "alert_fired", subject="Firing: X", body="b", link_path="/alerts")
        server = FakeServer()
        await drain(db, send=server)
        assert server.batches[0][0].subject == "[LabDog] Firing: X"

    async def test_a_refusal_is_recorded_and_retried_later(self, db, smtp_on) -> None:
        await _user(db, events=("alert_fired",))
        await notify(db, "alert_fired", subject="s", body="b", link_path="/alerts")
        now = datetime.now(UTC)

        stats = await drain(db, now=now, send=FakeServer(results=["550 mailbox unavailable"]))

        [row] = await _queued(db)
        assert stats["retrying"] == 1
        assert row.status == "pending"
        assert row.attempts == 1
        assert row.last_error == "550 mailbox unavailable"
        assert row.next_attempt_at == now + timedelta(minutes=1)
        # Not due yet: a drain a few seconds later leaves it alone.
        server = FakeServer()
        await drain(db, now=now + timedelta(seconds=5), send=server)
        assert server.batches == []

    async def test_an_unreachable_server_fails_every_email_alike(self, db, smtp_on) -> None:
        for _ in range(2):
            await _user(db, events=("alert_fired",))
        await notify(db, "alert_fired", subject="s", body="b", link_path="/alerts")
        error = smtp.SMTPSendError("could not connect: Connection refused")
        await drain(db, send=FakeServer(raises=error))
        rows = await _queued(db)
        assert {r.last_error for r in rows} == {"could not connect: Connection refused"}
        assert all(r.status == "pending" for r in rows)

    async def test_it_gives_up_after_the_last_attempt(self, db, smtp_on) -> None:
        await _user(db, events=("alert_fired",))
        await notify(db, "alert_fired", subject="s", body="b", link_path="/alerts")
        [row] = await _queued(db)
        row.attempts = MAX_ATTEMPTS - 1
        await db.flush()
        await drain(db, send=FakeServer(results=["451 try later"]))
        assert row.status == "failed"
        assert row.attempts == MAX_ATTEMPTS

    async def test_without_a_public_url_nothing_with_a_link_goes_out(self, db, smtp_on) -> None:
        await _set(db, "notifications.public_url", "")
        await _user(db, events=("alert_fired",))
        await notify(db, "alert_fired", subject="s", body="b", link_path="/alerts")
        server = FakeServer()
        await drain(db, send=server)
        [row] = await _queued(db)
        assert server.batches == []
        assert row.status == "failed"
        assert row.last_error == NO_URL_ERROR

    async def test_switching_email_off_drops_the_queue(self, db, smtp_on) -> None:
        await _user(db, events=("alert_fired",))
        await notify(db, "alert_fired", subject="s", body="b", link_path="/alerts")
        smtp_on.enabled = False
        await db.flush()
        server = FakeServer()
        await drain(db, send=server)
        [row] = await _queued(db)
        assert server.batches == []
        assert row.status == "failed"
        assert "switched off" in row.last_error

    def test_a_storm_lists_the_tail_by_subject(self) -> None:
        rows = [
            Notification(
                event_type="alert_fired",
                recipient="a@x",
                subject=f"[LabDog] Firing: A{n}",
                body=f"body-{n}",
                link_path="/alerts",
            )
            for n in range(FULL_ITEMS + 5)
        ]
        message = compose(rows, URL)
        assert f"body-{FULL_ITEMS - 1}" in message.body
        assert f"body-{FULL_ITEMS}" not in message.body
        assert f"Firing: A{FULL_ITEMS + 4}" in message.body
        assert "And 5 more" in message.body


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


class FakeSMTP:
    instances: list[FakeSMTP] = []

    def __init__(self, host, port, timeout=None, context=None) -> None:
        self.host, self.port = host, port
        self.calls: list[str] = []
        self.sent: list = []
        self.refuse: set[str] = set()
        FakeSMTP.instances.append(self)

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(f"login {user}")
        if password == "wrong":
            raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")

    def send_message(self, message):
        if message["To"] in self.refuse:
            raise smtplib.SMTPRecipientsRefused({message["To"]: (550, b"no such user")})
        self.sent.append(message)

    def quit(self):
        self.calls.append("quit")

    def close(self):
        pass


@pytest.fixture
def fake_smtp(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP


def _config(**over) -> smtp.SMTPConfig:
    values = dict(
        host="mail",
        port=587,
        tls_mode="starttls",
        username="labdog",
        password="pw",
        from_address="labdog@home.arpa",
    )
    values.update(over)
    return smtp.SMTPConfig(**values)


class TestTransport:
    def test_starttls_then_login_then_send(self, fake_smtp) -> None:
        results = smtp.send(_config(), [smtp.OutgoingEmail("a@x", "hi", "body")])
        [server] = fake_smtp.instances
        assert results == [None]
        assert server.calls == ["starttls", "login labdog", "quit"]
        assert server.sent[0]["From"] == "labdog@home.arpa"

    def test_a_refused_login_says_what_the_server_said(self, fake_smtp) -> None:
        with pytest.raises(
            smtp.SMTPSendError, match="535 5.7.8 Username and Password not accepted"
        ):
            smtp.send(_config(password="wrong"), [smtp.OutgoingEmail("a@x", "hi", "body")])

    def test_one_refused_recipient_does_not_stop_the_rest(self, fake_smtp, monkeypatch) -> None:
        original = FakeSMTP.__init__

        def init(self, *args, **kwargs):
            original(self, *args, **kwargs)
            self.refuse = {"gone@x"}

        monkeypatch.setattr(FakeSMTP, "__init__", init)
        results = smtp.send(
            _config(),
            [smtp.OutgoingEmail("gone@x", "s", "b"), smtp.OutgoingEmail("ok@x", "s", "b")],
        )
        assert results[0].startswith("recipient refused — gone@x: 550 no such user")
        assert results[1] is None

    def test_a_newline_in_the_subject_cannot_add_a_header(self, fake_smtp) -> None:
        smtp.send(_config(), [smtp.OutgoingEmail("a@x", "hi\r\nBcc: evil@x", "b")])
        message = fake_smtp.instances[0].sent[0]
        assert message["Bcc"] is None
        assert message["Subject"] == "hi Bcc: evil@x"

    def test_a_dead_port_is_a_connection_error(self) -> None:
        with pytest.raises(smtp.SMTPSendError, match="could not connect"):
            smtp.send(
                _config(host="127.0.0.1", port=1, tls_mode="none", username=None),
                [smtp.OutgoingEmail("a@x", "s", "b")],
            )


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


class TestAPI:
    async def test_the_password_is_write_only(self, db, superuser_client) -> None:
        response = await superuser_client.put(
            "/api/notifications/email",
            json={
                "enabled": True,
                "host": "mail.home.arpa",
                "port": 465,
                "tls_mode": "tls",
                "username": "labdog",
                "password": "s3cret-pass",
                "from_address": "labdog@home.arpa",
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["password_set"] is True
        assert "s3cret-pass" not in response.text
        assert body["ready"] is False  # no public URL yet

        row = await db.get(SMTPSettings, 1)
        assert decrypt_ssh_key(row.encrypted_password, get_master_key()) == "s3cret-pass"

    async def test_saving_without_a_password_keeps_it(self, db, superuser_client) -> None:
        base = {"enabled": True, "host": "mail", "from_address": "labdog@home.arpa"}
        await superuser_client.put("/api/notifications/email", json={**base, "password": "pw"})
        response = await superuser_client.put("/api/notifications/email", json=base)
        assert response.json()["password_set"] is True
        response = await superuser_client.put(
            "/api/notifications/email", json={**base, "clear_password": True}
        )
        assert response.json()["password_set"] is False

    async def test_the_test_button_reports_the_servers_words(self, db, superuser_client) -> None:
        draft = {"host": "mail", "from_address": "labdog@home.arpa"}
        with patch(
            "app.notifications.email.send",
            side_effect=smtp.SMTPSendError("535 5.7.8 Username and Password not accepted"),
        ):
            response = await superuser_client.post("/api/notifications/email/test", json=draft)
        assert response.json() == {
            "success": False,
            "message": "535 5.7.8 Username and Password not accepted",
        }

        with patch("app.notifications.email.send", return_value=[None]) as send:
            response = await superuser_client.post(
                "/api/notifications/email/test", json={**draft, "to": "me@home.arpa"}
            )
        assert response.json()["success"] is True
        assert send.call_args.args[1][0].to == "me@home.arpa"

    async def test_subscriptions_are_ones_own(self, db, superuser_client) -> None:
        response = await superuser_client.put(
            "/api/notifications/subscriptions",
            json={"event_types": ["alert_fired", "approval_requested", "alert_fired"]},
        )
        assert response.json() == {"event_types": ["alert_fired", "approval_requested"]}
        response = await superuser_client.get("/api/notifications/subscriptions")
        assert response.json() == {"event_types": ["alert_fired", "approval_requested"]}
        count = (
            await db.execute(select(func.count()).select_from(NotificationSubscription))
        ).scalar()
        assert count == 2

    async def test_an_unknown_event_is_refused(self, superuser_client) -> None:
        response = await superuser_client.put(
            "/api/notifications/subscriptions", json={"event_types": ["everything"]}
        )
        assert response.status_code == 422

    async def test_the_delivery_log_lists_failures(self, db, smtp_on, superuser_client) -> None:
        await _user(db, events=("alert_fired",))
        await notify(db, "alert_fired", subject="s", body="b", link_path="/alerts")
        await drain(db, send=FakeServer(results=["550 mailbox unavailable"]))
        response = await superuser_client.get("/api/notifications/deliveries")
        [row] = response.json()
        assert row["last_error"] == "550 mailbox unavailable"
        assert "body" not in row


class TestRetention:
    async def test_old_records_go_and_queued_ones_stay(self, db, smtp_on) -> None:
        from app.tasks.notifications import _prune

        await _user(db, events=("alert_fired",))
        for _ in range(3):
            await notify(db, "alert_fired", subject="s", body="b", link_path="/alerts")
        old, failed_old, pending_old = await _queued(db)
        long_ago = datetime.now(UTC) - timedelta(days=200)
        old.status, old.created_at = "sent", long_ago
        failed_old.status, failed_old.created_at = "failed", long_ago
        pending_old.created_at = long_ago  # still pending: live work
        await db.commit()

        with patch("app.db.task_session", _session_cm(db)):
            result = await _prune()

        assert result["deleted"] == 2
        [left] = await _queued(db)
        assert left.id == pending_old.id
