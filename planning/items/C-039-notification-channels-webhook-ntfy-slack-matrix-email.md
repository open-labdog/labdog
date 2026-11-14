# C-039: Notification channels: webhook, ntfy, Slack, Matrix; email follow-ups

| | |
|---|---|
| Edition | community |
| Source | roadmap, board item "Notification channels and email follow-ups: webhook, ntfy, Slack, Matrix, shared address, HTML" |
| Tier / score | P3 / 4.0 |
| Stage | parked |
| Effort | M |
| Autonomy | B |
| Depends on | C-013 |

## Goal
A user can receive notifications on a channel other than personal email (a webhook, ntfy, Slack or Matrix), and email gains a shared team address, a link from an alert email to its investigation, and HTML bodies.

## Why it is parked
The board rates it P3, and email covers the need for now. The event list is still growing (C-012 and C-013 add events), and channels are cheaper to add once that list is settled.

## What would un-park it
- C-013 is merged, so the events exist.
- A named first channel. The generic webhook is the natural first, because ntfy, Slack and Matrix can all be reached through it.

## Context
- The schema is ready: `NotificationSubscription.channel` and `Notification.channel` (`backend/app/notifications/models.py`, lines 96 and 115) default to `"email"`, and the unique constraint covers user, event type and channel.
- The code is email-only: `notify()` in `backend/app/notifications/service.py` (line 102) selects subscriptions with `channel == "email"` (line 137) and writes `"channel": "email"` (line 153). `drain()` in `backend/app/notifications/delivery.py` (line 121) selects `Notification.channel == "email"` (line 155) and composes with `compose()` (line 60) into an `OutgoingEmail`.
- Missing per the board: a recipient per channel (a URL or topic, not the user's address) and a drain per channel.
- Email follow-ups: `backend/app/notifications/email.py` sends plain text only. `notify_alert_fired` (service.py line 202) links to `/alerts` because the email is queued before the investigation has a session. Links are built in `backend/app/notifications/urls.py`.
- Frontend: `frontend/app/(dashboard)/notifications/`. API: `backend/app/api/notifications.py`.

## Assumptions
- Assumption (not yet confirmed): split when promoted into "webhook channel" (first), "named presets for ntfy, Slack, Matrix", and "email follow-ups".
- Assumption (not yet confirmed): a webhook URL is operator-supplied and the backend calls it, so it needs the same care as other outbound URLs (no secrets in the URL shown back, a timeout, the existing backoff in `delivery.backoff`). Question for Dennis: is a channel recipient per user, or one shared recipient per install set by an admin?
- Assumption (not yet confirmed): a shared team address is an install setting behind `settings.manage`.
