"""Which alerts may run at full auto: the label, or the rules.

Two settings decide it:

* ``ai.alert_full_auto_rules_enabled`` off (the default): an alert may run
  at full auto when it carries the label ``labdog_ai_automate="true"``. The
  label is set on the alert rule in Grafana, where a template can make it
  depend on the host (``$labels.labdog_hostname``, which the alloy-install
  action stamps on every series).
* On: ``ai.alert_full_auto_rules`` decides, one rule per line. Its default
  is the single rule ``labdog_ai_automate=true``, so switching the rules on
  changes nothing until the list is edited, and an empty list lets no
  alert run at full auto.

A rule is conditions separated by commas, all of which must hold; an alert
matches when any rule does. There is no order and no deny line, so no rule
can override another. Conditions:

* ``NginxDown`` — the alert's name. Names may contain spaces.
* ``key=value``, ``key!=value`` — a label of the alert (``!=`` also holds
  when the label is missing). Quote a value that contains a comma.
* ``host:web-1``, ``host:web-1|web-2`` — the LabDog host the alert
  resolved to, by hostname.
* ``group:web`` — a LabDog group that host is a member of.
* ``!host:…``, ``!group:…`` — the host is not one of these, or in none of
  these groups.

Matching only makes an alert *eligible*. Every safeguard in
:mod:`app.ai.alert_autonomy` still applies.

The settings layer imports the validator from here, so, as in
:mod:`app.ai.alert_autonomy`, nothing at module level may import
:mod:`app.settings_service`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

#: The label that marks an alert for full auto when the rules are off.
AUTOMATE_LABEL = "labdog_ai_automate"
DEFAULT_RULES = f"{AUTOMATE_LABEL}=true"

_LABEL_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_VALUE = 255


@dataclass(frozen=True)
class Condition:
    kind: str  # "alertname" | "label" | "host" | "group"
    #: The label name, for kind "label".
    key: str = ""
    #: One value for an alert name or a label; the alternatives for a host
    #: or group, lowercased (LabDog compares host and group names without
    #: case).
    values: tuple[str, ...] = ()
    negate: bool = False

    def holds(
        self, alertname: str, labels: Mapping[str, Any], host: str | None, groups: set[str]
    ) -> bool:
        if self.kind == "alertname":
            return alertname == self.values[0]
        if self.kind == "label":
            actual = labels.get(self.key)
            equal = actual is not None and str(actual) == self.values[0]
            return equal != self.negate
        if self.kind == "host":
            hit = host is not None and host.lower() in self.values
        else:
            hit = bool(groups.intersection(self.values))
        return hit != self.negate

    @property
    def about_host(self) -> bool:
        return self.kind in ("host", "group")


@dataclass(frozen=True)
class Rule:
    #: The line as written, trimmed. What the Alerts page quotes.
    text: str
    conditions: tuple[Condition, ...]

    def matches(
        self, alertname: str, labels: Mapping[str, Any], host: str | None, groups: set[str]
    ) -> bool:
        return all(c.holds(alertname, labels, host, groups) for c in self.conditions)

    def applies_to_host(self, host: str, groups: set[str]) -> bool:
        """Whether the rule's host and group conditions allow ``host``,
        ignoring the alert name and labels it also asks for."""
        return all(c.holds("", {}, host, groups) for c in self.conditions if c.about_host)


def _split(line: str) -> list[str]:
    """Split on commas outside double quotes."""
    parts, buf, quoted = [], [], False
    for ch in line:
        if ch == '"':
            quoted = not quoted
            buf.append(ch)
        elif ch == "," and not quoted:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if quoted:
        raise ValueError("a quote is not closed")
    parts.append("".join(buf))
    return [p.strip() for p in parts]


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def _names(kind: str, raw: str) -> tuple[str, ...]:
    names = tuple(n.strip().lower() for n in raw.split("|"))
    if not all(names):
        raise ValueError(f"{kind}: needs a name, or names separated by |")
    return names


def _condition(part: str) -> Condition:
    if not part:
        raise ValueError("an empty condition (two commas in a row, or one at the end)")
    negate = part.startswith("!")
    body = part[1:].strip() if negate else part
    for kind in ("host", "group"):
        if body.startswith(f"{kind}:"):
            return Condition(kind, values=_names(kind, body[len(kind) + 1 :]), negate=negate)
    if negate:
        raise ValueError(
            f"{part!r}: ! goes only before host: or group:; for a label, write key!=value"
        )
    if "=" in part:
        ne = "!=" in part
        key, value = part.split("!=" if ne else "=", 1)
        key, value = key.strip(), _unquote(value)
        if not _LABEL_KEY.match(key):
            raise ValueError(
                f"{key!r} is not a label name: letters, digits and _, not starting with a digit"
            )
        if len(value) > _MAX_VALUE:
            raise ValueError(f"{key}: a value is at most {_MAX_VALUE} characters")
        return Condition("label", key=key, values=(value,), negate=ne)
    if "*" in part or "?" in part:
        raise ValueError(
            f"{part!r}: alert names are matched exactly and wildcards are not supported; "
            f"to cover several alerts, give them a label in Grafana and match that"
        )
    name = _unquote(part)
    if len(name) > _MAX_VALUE:
        raise ValueError(f"{name[:40]!r}…: an alert name is at most {_MAX_VALUE} characters")
    return Condition("alertname", values=(name,))


def parse_rules(raw: str | None) -> list[Rule]:
    """The rules in ``raw``, or ``ValueError`` naming the first bad line.

    Blank lines and lines starting with ``#`` are skipped.
    """
    rules: list[Rule] = []
    for number, line in enumerate((raw or "").splitlines(), start=1):
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        try:
            conditions = tuple(_condition(p) for p in _split(text))
        except ValueError as exc:
            raise ValueError(f"line {number}: {exc}") from exc
        if sum(c.kind == "alertname" for c in conditions) > 1:
            raise ValueError(f"line {number}: one alert name per rule; put each on its own line")
        rules.append(Rule(text, conditions))
    return rules


def validate_rules(raw: str) -> str:
    """Normalise the list for saving: each rule trimmed, comments kept."""
    parse_rules(raw)
    return "\n".join(line.strip() for line in raw.strip().splitlines())


async def unknown_names(db: AsyncSession, rules: list[Rule]) -> list[str]:
    """Hosts and groups the rules name that LabDog does not have.

    Checked when the list is saved, and shown beside it afterwards: a
    rule naming a host that was renamed or deleted silently matches
    nothing, which would read as a safeguard holding an alert back.
    """
    from app.models.host import Host
    from app.models.host_group import HostGroup

    wanted = {kind: set() for kind in ("host", "group")}
    for rule in rules:
        for c in rule.conditions:
            if c.about_host:
                wanted[c.kind].update(c.values)
    problems: list[str] = []
    for kind, column in (("host", Host.hostname), ("group", HostGroup.name)):
        if not wanted[kind]:
            continue
        lowered = func.lower(column)
        found = await db.execute(select(lowered).where(lowered.in_(wanted[kind])))
        missing = sorted(wanted[kind] - set(found.scalars().all()))
        problems += [f"no {kind} named {name!r}" for name in missing]
    return problems


async def check_rules(raw: str, db: AsyncSession) -> None:
    """Refuse a list that names a host or group LabDog does not have."""
    problems = await unknown_names(db, parse_rules(raw))
    if problems:
        raise ValueError("; ".join(problems))


async def rules_warnings(raw: str, db: AsyncSession) -> list[str]:
    """What is wrong with the saved list now, for the settings page."""
    try:
        rules = parse_rules(raw)
    except ValueError as exc:
        return [str(exc)]
    return [f"{p}: rules naming it match nothing" for p in await unknown_names(db, rules)]


async def active_rules(db: AsyncSession) -> tuple[bool, list[Rule]]:
    """``(rules_enabled, rules)``: the label rule alone while the rules are
    off, the saved list while they are on."""
    from app.settings_service import get_setting_typed

    enabled = bool(int(await get_setting_typed("ai.alert_full_auto_rules_enabled", db)))
    if not enabled:
        return False, parse_rules(DEFAULT_RULES)
    try:
        return True, parse_rules(str(await get_setting_typed("ai.alert_full_auto_rules", db)))
    except ValueError:
        # Validated on save, so only a hand-edited row reaches here. Fail
        # towards matching nothing.
        return True, []


async def host_context(db: AsyncSession, host_id: int | None) -> tuple[str | None, set[str]]:
    """The hostname and lowercased group names a rule is matched against."""
    if host_id is None:
        return None, set()
    from app.models.host import Host, HostGroupMembership
    from app.models.host_group import HostGroup

    host = await db.get(Host, host_id)
    if host is None:
        return None, set()
    groups = await db.execute(
        select(func.lower(HostGroup.name))
        .join(HostGroupMembership, HostGroupMembership.c.group_id == HostGroup.id)
        .where(HostGroupMembership.c.host_id == host_id)
    )
    return host.hostname, set(groups.scalars().all())


async def first_match(db: AsyncSession, event: Any) -> Rule | None:
    """The first rule ``event`` matches, or ``None``."""
    _, rules = await active_rules(db)
    if not rules:
        return None
    host, groups = await host_context(db, event.host_id)
    labels = event.labels or {}
    for rule in rules:
        if rule.matches(event.alertname, labels, host, groups):
            return rule
    return None
