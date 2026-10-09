"""Outbound notifications — starting with email.

LabDog used to send nothing. An alert that fired at three in the morning,
or a change parked waiting for someone's approval, reached nobody unless
they happened to have the UI open; a parked request then expired unseen.

Layout:

- ``models``   — the SMTP settings row, per-user subscriptions, and the
  outbox every notification is written to
- ``events``   — the event types a user can subscribe to
- ``service``  — :func:`notify`, which writes outbox rows in the caller's
  transaction, and the builders for each event's text
- ``email``    — SMTP transport (stdlib ``smtplib``; no new dependency)
- ``delivery`` — the periodic drain: coalesce per recipient, send, retry

Delivery never happens inline. :func:`service.notify` only writes rows;
``app.tasks.notifications`` sends them. A slow or dead mail server
therefore cannot hold up an alert webhook, an approval, or an AI session.
"""
