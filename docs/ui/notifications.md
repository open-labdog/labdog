# Email notifications

**Path:** `/notifications` — Settings · Integrations · **Email**, or
**Email notifications…** in the account menu behind your avatar.

LabDog can email you when an alert fires, when the assistant is waiting
for you to approve a change, when that request is about to expire or has
expired, and when a full-auto alert investigation has changed a host. Each
is opt-in, per person.

Without it, an alert at three in the morning or a change parked waiting
for a decision reaches nobody unless they happen to have LabDog open — and
an approval request nobody sees simply expires.

The page has four parts: the mail server, LabDog's own address, your
subscriptions, and a log of what was sent. A banner at the top says why
nothing is being sent, while anything is missing.

---

## Setting it up

### 1. The mail server

| Field | Notes |
|---|---|
| **send email** | The master switch. While off, nothing is even queued — so turning it on later does not deliver a backlog of alerts that stopped mattering hours ago. Anything still queued when you switch it off is dropped and logged as such. |
| **smtp server**, **port** | Your provider's submission server, or a relay on the LAN. |
| **security** | **STARTTLS** (usually port 587), **TLS from the start** (usually 465), or **None** — for a relay on the same machine or a trusted LAN only. Changing it moves the port to the usual one, unless you typed a port of your own. The server's certificate is verified against the system trust store. |
| **username**, **password** | Blank username for a relay that needs no login. The password is encrypted at rest and never sent back to the browser: the field shows that one is stored, blank keeps it, and **remove the stored password** clears it. |
| **from address** | A plain address such as `labdog@example.com`. Domains like `home.arpa` and `.lan` are fine — whether the server will send as that address is the server's decision, and the test tells you. |

**Send test** sends one email with what is in the form, saved or not, to
the address beside it (yours if blank), and shows the server's own answer
— `535 5.7.8 Username and Password not accepted`, `could not connect:
Connection refused`, a TLS error — rather than "failed". A blank password
uses the stored one, so changing the port of a working setup does not
mean retyping it.

Gmail and Microsoft 365 want an app password rather than your account
password, and STARTTLS on 587.

### 2. LabDog's address

`notifications.public_url` — for example `https://labdog.example.com`.
Every notification links back to the page it is about, and **nothing is
sent while this is empty**. LabDog does not guess its own address from a
web request's `Host` header: most notifications are not triggered by a
browser at all, and where one is, that header is whatever the client sent
— a link built from it is a phishing link waiting for someone to choose
the host. A message that cannot be sent for this reason is marked failed
in the log with that explanation.

### 3. What you want to hear about

Under **your notifications**, tick what you want, then **Save**. This
only ever changes your own subscriptions, and email goes to the address
you sign in with.

| Event | When it is sent |
|---|---|
| **Alert fired** | A new firing alert is recorded, from the [Grafana webhook or the Alertmanager poll](alerts.md). Repeats of the same firing are not sent again. |
| **Approval requested** | The [assistant](assistant.md#approving-a-change) wants to change a host and has paused for a decision: the host, the exact command, its stated reason, why the classifier calls it a change, and when the request expires. |
| **Approval about to expire** | A request is still undecided `notifications.approval_expiry_warning_hours` (default 2) before it expires. Once per request; `0` turns it off. |
| **Approval expired** | Nobody decided in time; the session continued without the change. |
| **Automatic fix made** | A [full-auto alert investigation](alerts.md#full-auto-for-named-alerts) changed its host: each command it ran and whether it succeeded, the snapshot taken before each, and the opening of its report. Sent however the session ended — a session that restarted a service and then hit its time limit still restarted the service — and not sent when it changed nothing. |

**Nothing in an email can approve anything.** Approving happens in
LabDog, signed in. A link that ran a root command would make your inbox a
credential.

---

## How sending works

Notifications are written to an outbox in the same database transaction
as whatever caused them — a rolled-back approval leaves no email about a
request that does not exist — and a background task sends them **once a
minute**.

**Everything due for one person in that minute is one email.** An alert
storm becomes one message a minute listing what fired, not one message
per alert. The first 20 items appear in full; the rest are listed by
subject.

**Failures are retried** after 1, 2, 4, 8 and 16 minutes; after the sixth
attempt the message is given up on. A server that refuses one recipient
does not stop the others.

Delivery is at least once: if LabDog is stopped between the server
accepting a message and LabDog recording that, the message is sent again
on the next run. A duplicate is the better failure than a lost one.

Text that came from outside LabDog — alert labels and annotations,
commands the model proposed, its reasons, report text — passes through
the same credential redaction as the assistant's transcript before it is
put in an email, because an email is stored in places LabDog does not
control.

## The log

**recent notifications** lists the last hundred messages: when each was
queued, to whom, its subject, and whether it was sent, is waiting to be
retried (with the server's last answer), or failed for good. This is
where to look when someone did not get an email. Message bodies are not
shown.

Sent and failed records are kept for `logging.run_retention_days`
(default 90), like run history.

---

## Settings

| Setting | Default | Effect |
|---|---|---|
| `notifications.public_url` | empty | LabDog's address, for links. Required — see above. |
| `notifications.approval_expiry_warning_hours` | `2` | How long before an approval request expires to warn about it (0 = never). |

Both are edited on this page. The mail server itself is not an
`ai.*`-style setting but its own record, so that its password can be
stored encrypted and never returned by the settings API.
