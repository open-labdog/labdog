"""Which alerts may run at full auto: the labdog_automate label, or rules.

The parser and the matching need no database; the host and group
conditions, the name check on save and the per-host view do.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select

from app.ai.alert_autonomy import MIN_WEBHOOK_TOKEN_CHARS, resolve
from app.ai.alerts import resolve_host
from app.ai.full_auto_rules import first_match, parse_rules, validate_rules
from app.ai.models import AlertEvent
from app.config import settings
from app.models.app_setting import AppSetting
from app.models.host import Host
from app.settings_service import get_all_settings, invalidate_cache, update_setting
from tests.conftest import create_group, create_host


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


async def _rules(db, text: str) -> None:
    await _set(db, "ai.alert_full_auto_rules_enabled", "1")
    await _set(db, "ai.alert_full_auto_rules", text)


async def _alert(db, host_id: int | None, alertname: str = "NginxDown", **labels) -> AlertEvent:
    event = AlertEvent(
        source="grafana_webhook",
        fingerprint=uuid.uuid4().hex,
        alertname=alertname,
        severity="critical",
        status="firing",
        labels={"alertname": alertname, **labels},
        annotations={},
        starts_at=datetime.now(UTC),
        host_id=host_id,
    )
    db.add(event)
    await db.flush()
    return event


async def _matched(db, event) -> str | None:
    rule = await first_match(db, event)
    return rule.text if rule else None


# ---------------------------------------------------------------------------
# Reading the rules
# ---------------------------------------------------------------------------


class TestParsing:
    def test_conditions_of_each_kind(self) -> None:
        (rule,) = parse_rules(
            'High CPU usage, severity!=info, team="a, b", host:Web-1|web-2, !group:legacy'
        )
        kinds = [(c.kind, c.key, c.values, c.negate) for c in rule.conditions]
        assert kinds == [
            ("alertname", "", ("High CPU usage",), False),
            ("label", "severity", ("info",), True),
            ("label", "team", ("a, b",), False),
            ("host", "", ("web-1", "web-2"), False),
            ("group", "", ("legacy",), True),
        ]

    def test_blank_lines_and_comments_are_skipped(self) -> None:
        rules = parse_rules("\n# tester only\nNginxDown, host:tester\n\n")
        assert [r.text for r in rules] == ["NginxDown, host:tester"]

    def test_saving_trims_each_line(self) -> None:
        assert validate_rules("  NginxDown ,host:tester  \n\n") == "NginxDown ,host:tester"

    def test_an_empty_list_is_allowed(self) -> None:
        assert parse_rules("") == []

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("Nginx*", "wildcards are not supported"),
            ("!severity=info", "! goes only before host: or group:"),
            ("NginxDown, DiskFull", "one alert name per rule"),
            ("NginxDown,, host:a", "an empty condition"),
            ("NginxDown,", "an empty condition"),
            ('team="a, b', "a quote is not closed"),
            ("1team=a", "is not a label name"),
            ("host:", "host: needs a name"),
            ("group:web|", "group: needs a name"),
        ],
    )
    def test_a_bad_line_is_refused_with_its_number(self, text: str, message: str) -> None:
        with pytest.raises(ValueError, match=rf"line 2: .*{message}"):
            parse_rules(f"NginxDown\n{text}")


# ---------------------------------------------------------------------------
# Matching, without a database
# ---------------------------------------------------------------------------


class TestMatching:
    #: Two alerts both hosts can raise, each allowed on one host only.
    RULES = parse_rules("NginxDown, host:web-1\nDiskFull, host:db-1")

    @pytest.mark.parametrize(
        ("alertname", "host", "expected"),
        [
            ("NginxDown", "web-1", "NginxDown, host:web-1"),
            ("DiskFull", "web-1", None),
            ("NginxDown", "db-1", None),
            ("DiskFull", "db-1", "DiskFull, host:db-1"),
        ],
    )
    def test_each_host_gets_full_auto_for_its_own_alert(self, alertname, host, expected) -> None:
        hit = next((r.text for r in self.RULES if r.matches(alertname, {}, host, set())), None)
        assert hit == expected

    def test_a_group_with_an_exception(self) -> None:
        (rule,) = parse_rules("DiskFull, group:db, !host:db-legacy")
        assert rule.matches("DiskFull", {}, "db-1", {"db"})
        assert not rule.matches("DiskFull", {}, "db-legacy", {"db"})
        assert not rule.matches("DiskFull", {}, "web-1", {"web"})

    def test_host_names_ignore_case_labels_do_not(self) -> None:
        (rule,) = parse_rules("labdog_automate=true, host:WEB-1")
        assert rule.matches("x", {"labdog_automate": "true"}, "web-1", set())
        assert not rule.matches("x", {"labdog_automate": "True"}, "web-1", set())

    def test_not_equal_holds_when_the_label_is_missing(self) -> None:
        (rule,) = parse_rules("severity!=info")
        assert rule.matches("x", {}, None, set())
        assert not rule.matches("x", {"severity": "info"}, None, set())

    def test_a_host_condition_never_holds_without_a_host(self) -> None:
        (rule,) = parse_rules("NginxDown, host:web-1")
        assert not rule.matches("NginxDown", {}, None, set())


# ---------------------------------------------------------------------------
# The two modes, against the database
# ---------------------------------------------------------------------------


class TestModes:
    async def test_by_default_the_label_decides(self, db) -> None:
        host = await create_host(db)
        assert await _matched(db, await _alert(db, host.id, labdog_automate="true")) == (
            "labdog_automate=true"
        )
        assert await _matched(db, await _alert(db, host.id, labdog_automate="false")) is None
        assert await _matched(db, await _alert(db, host.id)) is None

    async def test_turning_the_rules_on_starts_from_the_label_rule(self, db) -> None:
        await _set(db, "ai.alert_full_auto_rules_enabled", "1")
        host = await create_host(db)
        assert await _matched(db, await _alert(db, host.id, labdog_automate="true")) == (
            "labdog_automate=true"
        )

    async def test_with_the_rules_on_the_label_counts_only_if_a_rule_says_so(self, db) -> None:
        await _rules(db, "DiskFull")
        host = await create_host(db)
        assert await _matched(db, await _alert(db, host.id, labdog_automate="true")) is None

    async def test_an_empty_list_lets_nothing_through(self, db) -> None:
        await _rules(db, "")
        host = await create_host(db)
        assert await _matched(db, await _alert(db, host.id, labdog_automate="true")) is None

    async def test_hosts_and_groups_are_labdogs(self, db) -> None:
        db_group = await create_group(db, name="db")
        db1 = await create_host(db, hostname="db-1", group_ids=[db_group.id])
        legacy = await create_host(db, hostname="db-legacy", group_ids=[db_group.id])
        web1 = await create_host(db, hostname="web-1")
        await _rules(db, "NginxDown, host:web-1\nDiskFull, group:DB, !host:db-legacy")

        assert await _matched(db, await _alert(db, web1.id, "NginxDown")) == "NginxDown, host:web-1"
        assert await _matched(db, await _alert(db, web1.id, "DiskFull")) is None
        assert await _matched(db, await _alert(db, db1.id, "NginxDown")) is None
        assert await _matched(db, await _alert(db, db1.id, "DiskFull")) == (
            "DiskFull, group:DB, !host:db-legacy"
        )
        assert await _matched(db, await _alert(db, legacy.id, "DiskFull")) is None

    async def test_the_alerts_page_quotes_the_rule(self, db, monkeypatch) -> None:
        monkeypatch.setattr(settings.alerts, "webhook_token", "t" * MIN_WEBHOOK_TOKEN_CHARS)
        await _set(db, "ai.alert_full_auto_requires_snapshot", "0")
        host = await create_host(db)
        decided = await resolve(db, await _alert(db, host.id, labdog_automate="true"))
        assert decided.level == "full_auto"
        assert decided.note == "Full auto: matched labdog_automate=true."


# ---------------------------------------------------------------------------
# Hosts and groups the rules name
# ---------------------------------------------------------------------------


class TestNames:
    async def test_saving_a_rule_for_a_host_labdog_does_not_have_is_refused(self, db) -> None:
        await create_host(db, hostname="web-1")
        with pytest.raises(ValueError, match="no host named 'web-9'; no group named 'nope'"):
            await update_setting(
                "ai.alert_full_auto_rules", "NginxDown, host:web-1|web-9, group:nope", None, db
            )
        assert await update_setting("ai.alert_full_auto_rules", "NginxDown, host:WEB-1", None, db)

    async def test_a_host_deleted_since_is_flagged_beside_the_list(self, db) -> None:
        host = await create_host(db, hostname="web-1")
        await update_setting("ai.alert_full_auto_rules", "NginxDown, host:web-1", None, db)
        await db.execute(delete(Host).where(Host.id == host.id))
        await db.flush()

        rows = {s["key"]: s for s in await get_all_settings(db)}
        assert rows["ai.alert_full_auto_rules"]["warnings"] == [
            "no host named 'web-1': rules naming it match nothing"
        ]
        assert rows["ai.alert_full_auto_rules_enabled"]["warnings"] is None


# ---------------------------------------------------------------------------
# Which host an alert is about
# ---------------------------------------------------------------------------


class TestResolveHost:
    async def test_labdog_host_id_wins(self, db) -> None:
        named = await create_host(db, hostname="web-1", ip="10.0.0.5")
        target = await create_host(db, hostname="web-2", ip="10.0.0.6")
        labels = {"labdog_host_id": str(target.id), "instance": "web-1:9100"}
        assert await resolve_host(db, labels) == target.id
        assert named.id != target.id

    async def test_a_deleted_hosts_id_falls_back_to_the_names(self, db) -> None:
        host = await create_host(db, hostname="web-1")
        labels = {"labdog_host_id": "999999", "labdog_hostname": "web-1"}
        assert await resolve_host(db, labels) == host.id

    async def test_labdog_hostname_before_the_conventional_labels(self, db) -> None:
        mine = await create_host(db, hostname="web-1", ip="10.0.0.5")
        other = await create_host(db, hostname="exporter", ip="10.0.0.9")
        labels = {"labdog_hostname": "web-1", "instance": "10.0.0.9:9100"}
        assert await resolve_host(db, labels) == mine.id
        assert other.id != mine.id


# ---------------------------------------------------------------------------
# The host page's view
# ---------------------------------------------------------------------------


class TestHostView:
    async def test_the_rules_that_can_apply_and_what_holds_every_alert_back(
        self, regular_user_client, db, monkeypatch
    ) -> None:
        monkeypatch.setattr(settings.alerts, "webhook_token", "short")
        web = await create_group(db, name="web")
        host = await create_host(db, hostname="web-1", group_ids=[web.id])
        await create_host(db, hostname="db-1")
        await _rules(db, "NginxDown, group:web\nDiskFull, host:db-1\nlabdog_automate=true")
        await db.commit()

        data = (await regular_user_client.get(f"/api/ai/full-auto/hosts/{host.id}")).json()
        assert data["ai_enabled"] is False
        assert data["rules_enabled"] is True
        assert data["rules"] == ["NginxDown, group:web", "labdog_automate=true"]
        blockers = " | ".join(data["blockers"])
        assert "alerts.intake_enabled" in blockers
        assert f"shorter than {MIN_WEBHOOK_TOKEN_CHARS}" in blockers
        assert "no rollback point" in blockers

    async def test_a_missing_host_is_404(self, regular_user_client) -> None:
        assert (await regular_user_client.get("/api/ai/full-auto/hosts/999999")).status_code == 404
