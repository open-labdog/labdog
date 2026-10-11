"""GET /api/ai/alert-intake: what the Settings › Integrations card shows.

The webhook token lives in the config file, so this endpoint is the only
way the UI learns whether one is set. It must say so without ever
returning it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.ai.models import AlertEvent
from app.config import settings
from app.models.app_setting import AppSetting
from app.settings_service import invalidate_cache

STRONG_TOKEN = "t" * 40


async def _set(db, key: str, value: str) -> None:
    existing = (
        await db.execute(select(AppSetting).where(AppSetting.key == key))
    ).scalar_one_or_none()
    if existing:
        existing.value = value
    else:
        db.add(AppSetting(key=key, value=value))
    await db.commit()
    invalidate_cache(key)


@pytest.fixture(autouse=True)
async def _clear_settings_cache():
    invalidate_cache()
    yield
    invalidate_cache()


def _event(source: str, created_at: datetime, fingerprint: str) -> AlertEvent:
    return AlertEvent(
        source=source,
        fingerprint=fingerprint,
        alertname="HostDown",
        status="firing",
        starts_at=created_at,
        created_at=created_at,
    )


async def test_off_by_default_and_refusing_without_a_token(regular_user_client, monkeypatch):
    monkeypatch.setattr(settings.alerts, "webhook_token", "")
    data = (await regular_user_client.get("/api/ai/alert-intake")).json()
    assert data == {
        "intake_enabled": False,
        "webhook_path": "/api/webhooks/grafana-alerts",
        "token_set": False,
        "token_long_enough_for_full_auto": False,
        "full_auto_alertnames": 0,
        "poll_minutes": 0,
        "last_webhook_alert_at": None,
        "last_poll_alert_at": None,
    }


async def test_reports_the_token_without_returning_it(regular_user_client, db, monkeypatch):
    monkeypatch.setattr(settings.alerts, "webhook_token", STRONG_TOKEN)
    await _set(db, "ai.alert_intake_enabled", "1")
    await _set(db, "ai.alertmanager_poll_minutes", "5")
    await _set(db, "ai.alert_full_auto_alertnames", "HostDown\nDiskFull\n")

    resp = await regular_user_client.get("/api/ai/alert-intake")
    assert STRONG_TOKEN not in resp.text
    data = resp.json()
    assert data["intake_enabled"] is True
    assert data["token_set"] is True
    assert data["token_long_enough_for_full_auto"] is True
    assert data["full_auto_alertnames"] == 2
    assert data["poll_minutes"] == 5


async def test_a_short_token_is_set_but_too_weak_for_full_auto(regular_user_client, monkeypatch):
    monkeypatch.setattr(settings.alerts, "webhook_token", "short")
    data = (await regular_user_client.get("/api/ai/alert-intake")).json()
    assert data["token_set"] is True
    assert data["token_long_enough_for_full_auto"] is False


async def test_the_newest_arrival_per_route(regular_user_client, db):
    early = datetime(2026, 10, 10, 8, 0, tzinfo=UTC)
    late = datetime(2026, 10, 11, 9, 30, tzinfo=UTC)
    poll = datetime(2026, 10, 11, 6, 0, tzinfo=UTC)
    db.add_all(
        [
            _event("grafana_webhook", early, "fp-a"),
            _event("grafana_webhook", late, "fp-b"),
            _event("alertmanager_poll", poll, "fp-c"),
        ]
    )
    await db.commit()

    data = (await regular_user_client.get("/api/ai/alert-intake")).json()
    assert datetime.fromisoformat(data["last_webhook_alert_at"]) == late
    assert datetime.fromisoformat(data["last_poll_alert_at"]) == poll
