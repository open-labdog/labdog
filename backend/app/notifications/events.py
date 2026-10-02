"""The events a user can subscribe to.

Kept in one table so the API, the subscription check and the UI all list
the same set. Adding an event means adding it here and calling
:func:`app.notifications.service.notify` where it happens; nobody is
subscribed to it until they choose to be.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EventType:
    key: str
    label: str
    #: Shown beside the checkbox. Says when it fires, which is what a
    #: person deciding whether to want it needs to know.
    description: str


EVENT_TYPES: tuple[EventType, ...] = (
    EventType(
        "alert_fired",
        "Alert fired",
        "A firing alert arrives from Grafana or Alertmanager.",
    ),
    EventType(
        "approval_requested",
        "Approval requested",
        "The assistant wants to change a host and is waiting for someone to approve it.",
    ),
    EventType(
        "approval_expiring",
        "Approval about to expire",
        "A change is still waiting for a decision a few hours before its request expires.",
    ),
    EventType(
        "approval_expired",
        "Approval expired",
        "Nobody decided in time, so the session finished without the change.",
    ),
    EventType(
        "alert_remediation",
        "Automatic fix made",
        "A full-auto alert investigation changed a host: what ran, and the snapshot taken.",
    ),
)

EVENT_KEYS = frozenset(e.key for e in EVENT_TYPES)
