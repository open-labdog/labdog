# Alerts

**Path:** `/alerts`

Alerts LabDog has received from Grafana or Alertmanager, newest first.
Each one can be handed to the [AI assistant](assistant.md) to investigate
— automatically under a policy you set, or by hand from this page.

**Recording an alert and investigating it are separate switches.**
Recording costs nothing and starts nothing; investigating spends money
without anyone asking. Both are off by default.

---

## Turning it on

Two settings, in [Settings](settings.md):

| Setting | Default | Effect |
|---|---|---|
| `ai.alert_intake_enabled` | `0` | Accept alerts at all |
| `ai.auto_investigate_enabled` | `0` | Start a session when an eligible alert arrives |

Plus a token in the config file — **not** in the UI settings, because
`/api/settings` is readable by any signed-in user and a shared secret does
not belong there:

```toml
[alerts]
webhook_token = "a-long-random-string"
```

or `LABDOG_ALERTS__WEBHOOK_TOKEN`. **An unset token refuses every
request.** An alert receiver that accepted unauthenticated POSTs would let
anyone on the network write rows LabDog might then spend money
investigating.

---

## Two ways in — and which one you need

### Grafana contact point (immediate)

Point a webhook contact point at:

```
POST https://labdog.example.com/api/webhooks/grafana-alerts
Authorization: Bearer <your webhook_token>
```

Basic auth works too — LabDog accepts the token as either the username or
the password, because which fields a contact-point form offers varies by
Grafana version.

### Alertmanager poll (catch-up)

```
ai.alertmanager_poll_minutes = 5     # 0 = never poll — the default
```

LabDog reads the Alertmanager API of your default Mimir instance — the
same one registered under [Grafana](README.md) — so there is no second
endpoint to configure and keep in step. It derives the path by swapping
`/prometheus` for `/alertmanager` on that instance's URL.

**Check whether this applies to you before turning it on.** The poller
reads *Mimir's* Alertmanager, and that is not the only Alertmanager in a
Grafana stack:

| Where your alert rules live | Does the poller see them? |
|---|---|
| **Grafana-managed** (Alerting → Alert rules, the default) | **No.** Grafana evaluates these and routes them to its own built-in Alertmanager. Mimir's never hears about them. |
| **Mimir ruler** (datasource-managed rules) | Yes |
| Grafana configured to use Mimir's Alertmanager as an *external* one | Yes |

A Grafana-managed setup is the common case, and for it the poller is not
merely unnecessary — it is **silently useless**: the endpoint answers
`200` with an empty list, so the poll looks healthy and records nothing,
forever. Leave `ai.alertmanager_poll_minutes` at `0` and use the webhook
alone.

If it does apply, run both. The webhook is immediate but only works while
LabDog is reachable from Grafana; the poller is slower but catches what
happened while it was not — a restart, a partition, an upgrade. They
deduplicate against each other, so there is no cost to having both.

To check which case you are in, ask Mimir directly:

```bash
curl -H "X-Scope-OrgID: anonymous" \
  https://<your-mimir>/alertmanager/api/v2/alerts
```

`404` means Mimir has no Alertmanager — webhook only. `200` with `[]`
while something is genuinely firing in Grafana means your rules are
Grafana-managed and it cannot see them — webhook only.

---

## How deduplication works

The key is **(fingerprint, start time)**, not fingerprint alone.

Alertmanager's fingerprint hashes the alert's label set, so the same rule
firing for the same host produces the same fingerprint every time it ever
fires. Keying on it alone would fold next month's outage into this
month's row and lose the history. Including the start time keeps one
continuous firing as one row, while a genuinely new firing becomes a new
one.

The `×3` badge on a row is `dedup_count` — how many times LabDog has been
told about that same firing. A high count on a short-lived alert is what
flapping looks like from here.

---

## When an investigation starts

An alert must clear every one of these:

1. `ai.auto_investigate_enabled` is on.
2. The alert is **firing** — a resolved alert has nothing live to look
   at, and a session would read a healthy host and report that nothing is
   wrong.
3. It is **new**, not a repeat notification about something already being
   investigated.
4. Its `severity` label meets `ai.auto_investigate_min_severity`.
5. `ai.enabled` is on, a provider that can run tools is configured, and
   the AI budget is not spent.

The prompt the session starts from is `ai.alert_mission_template` — see
[Settings](settings.md#the-investigation-prompt) if you want to change what
it asks.

Whatever happened is recorded on the row and shown as a badge, because
"nothing happened" has six different causes and each has a different fix:

| Badge | Meaning |
|---|---|
| **Investigating** | A session was started; click through to read it |
| **Auto-investigate off** | The policy switch is off |
| **Below severity threshold** | Including a severity LabDog did not recognise — see below |
| **Already resolved** | It cleared before LabDog got to it |
| **Already investigated** | A repeat notification |
| **AI budget reached** | Spend limit hit; the alert is still recorded |
| **Could not start** | AI disabled, no provider, or a provider that cannot run tools |

### Reading the outcome on the row

Once a session exists, the row stops reporting whether anything started
and reports what came of it — this badge replaces the policy badge above:

| On the row | Meaning |
|---|---|
| **Investigation queued** | Accepted, not started yet |
| **Investigating** | The session is running |
| **Waiting for approval** | Parked on an approval request |
| **Investigated** | Finished, with the opening of its conclusion quoted beneath |
| **Investigation failed** | The session errored; open it to see why |
| **Investigation stopped** | Cancelled, by you or by a cap |

The quoted text is the first real paragraph of the report — headings are
skipped, so the row shows the verdict rather than the word `Summary`.

**View investigation** opens that session's transcript directly. It is a
deep link to the session, not merely a jump to the Assistant page, so the
run you clicked is the one you land on.

### Severity is read, not guessed

The threshold understands `info`, `warning`, and `critical`. Grafana lets
you label an alert anything, so an alert labelled `sev1` or `P1`
**does not meet any threshold** and is skipped with the unrecognised value
named in the row.

That is deliberate. Treating `sev1` as critical would be a guess, and
treating it as trivial would be another — but only one of them spends
money unattended. Relabel the alert, or investigate it by hand.

---

## Investigating by hand

The **Investigate** button on a firing alert starts a session regardless
of the severity threshold — you have already made the judgement the
threshold exists to automate.

It does **not** bypass the kill switch, the provider check, or the
budget. Those are about whether LabDog may spend at all, which a button
press does not change.

---

## What the session can do

Always **read-only**, with no way to raise it. An alert is a machine's
opinion that something is wrong; acting on it unattended is a different
feature with a different risk, and this one only looks.

The session is scoped to the host LabDog resolved from the alert's
labels — it checks `nodename`, `hostname`, `host`, `node`, then
`instance` (stripping any port). If none of them match a managed host the
session runs with **no host in scope**, because putting an investigation
on the wrong host is worse than putting it on none: it would read a
healthy machine and report that nothing is wrong.

The model is given the alert's full label and annotation set. LabDog
reads three keys out of them itself, but an investigation is only as good
as its context, and you chose what to label.

### The prompt it starts from

`ai.alert_mission_template` in [Settings](settings.md#the-investigation-prompt)
is the wording the session begins with. It is editable because the
built-in text has to work for an alert LabDog has never seen, so it asks a
deliberately generic question — is this real, and what is causing it. You
know things it cannot: which alerts on your estate are chronically noisy,
that an exporter lies during backups, that an answer should always name
the service the host runs.

These placeholders are filled in from the alert:

| Placeholder | Expands to |
|---|---|
| `{alertname}` | The alert's name |
| `{severity}` | The severity label, or `(not labelled)` |
| `{status}` | `firing` or `resolved` |
| `{starts_at}` | When the alert started, in ISO 8601 |
| `{labels}` | Every label, one per line as `- key: value` |
| `{annotations}` | Every annotation, one per line as `- key: value` |

Anything else in braces is refused when you save, naming both what it did
not recognise and what is available. Write `{{` and `}}` for a literal
brace. Dropping a placeholder is allowed — it deprives the model of that
context, which is your call to make.

---

## Troubleshooting

**Nothing appears.** The page shows what arrived, not what fired. Check
`ai.alert_intake_enabled`, then that the contact point's token matches —
a wrong token is a 401 at LabDog and a delivery error in Grafana's own
logs.

**Alerts appear but nothing is investigated.** Read the badge. It names
which of the six gates stopped it.

**The poller reports "No Alertmanager API at this endpoint".** Mimir
serves Alertmanager under `/alertmanager` on the same host as its query
API. If your deployment does not, leave `ai.alertmanager_poll_minutes` at
`0` and use the webhook alone.

**The poll runs cleanly but never records anything.** Almost always
because your alert rules are Grafana-managed and Mimir's Alertmanager
cannot see them — see [Alertmanager poll](#alertmanager-poll-catch-up).
The poll cannot detect this itself: an Alertmanager with nothing routed
to it and an Alertmanager with nothing firing return the same empty
list.
