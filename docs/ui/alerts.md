# Alerts

**Path:** `/alerts` (Assistant · Alerts)

Alerts LabDog has received from Grafana or Alertmanager, newest first.
Each one can be handed to the [AI assistant](assistant.md) to investigate
— automatically under a policy you set, or by hand from this page — and,
if you allow it, to fix what it finds. See
[What the session can do](#what-the-session-can-do).

The screen is one table, one row per alert: when it fired, the alert name
(with a `×N` repeat count), severity, status, its summary, and the
investigation — with **investigate** or **view →** at the end of the row.
Firing critical alerts are tinted. A **Firing / All** switch in the head
hides or shows resolved alerts. Firing alerts also land in the
[Overview's Pending queue](dashboard.md#pending).

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

**There is no rate limit on investigations, and that is deliberate.**
(Automatic *fixes* have one — see
[Full auto](#full-auto-for-named-alerts).) LabDog does not own
your alerts; Grafana or Alertmanager does. A rule that fires, resolves
and fires again every few minutes passes every check above each time,
because each firing is genuinely new — and the fix belongs where the
rule lives: a longer `for:` duration, a threshold with hysteresis, or
Alertmanager routing (`group_wait`, `repeat_interval`, inhibition) that
sends LabDog only what is worth investigating. Adding a cooldown here
would paper over a rule that needs tuning and hide it from the person
who can tune it.

What bounds the spend is the AI budget, per session and in money. On a
subscription-billed provider the money limits do not apply — cost there
is an estimate of nothing — so the plan's own quota is the backstop, and
a session that hits it stops and says so. If a storm ever gets that
far, the `×N` badge in [How deduplication works](#how-deduplication-works)
is where to look for the rule that caused it.

Whatever happened is recorded on the row and shown as a tag in its
**investigation** column (hover it for the detail), because "nothing
happened" has six different causes and each has a different fix:

| Tag | Meaning |
|---|---|
| **investigating** | A session was started; **view →** opens it |
| **auto-investigate off** | The policy switch is off |
| **below severity threshold** | Including a severity LabDog did not recognise — see below |
| **already resolved** | It cleared before LabDog got to it |
| **already investigated** | A repeat notification |
| **AI budget reached** | Spend limit hit; the alert is still recorded |
| **could not start** | AI disabled, no provider, or a provider that cannot run tools |

### Reading the outcome on the row

Once a session exists, the row stops reporting whether anything started
and reports what came of it — this tag replaces the policy tag above:

| On the row | Meaning |
|---|---|
| **investigation queued** | Accepted, not started yet |
| **investigating** | The session is running |
| **waiting for approval** | Parked on an approval request |
| **investigated** | Finished, with the opening of its conclusion under the alert's summary |
| **investigation failed** | The session errored; open it to see why |
| **investigation stopped** | Cancelled, by you or by a cap |

The quoted text is the first real paragraph of the report — headings are
skipped, so the row shows the verdict rather than the word `Summary`.

**view →** opens that session's transcript directly
(`/assistant?session=<id>`). It is a deep link to the session, not merely a
jump to the Assistant page, so the run you clicked is the one you land on.

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

**investigate** on a firing alert's row starts a session regardless
of the severity threshold — you have already made the judgement the
threshold exists to automate.

It does **not** bypass the kill switch, the provider check, or the
budget. Those are about whether LabDog may spend at all, which a button
press does not change.

---

## What the session can do

By default, **only look**. Two settings let it do more:

| Setting | Default | Effect |
|---|---|---|
| `ai.alert_autonomy_level` | `read_only` | What every alert's session may change: `read_only`, or `approval` — each change it wants to make waits for someone to approve it |
| `ai.alert_full_auto_alertnames` | empty | Alerts, one exact name per line, whose session may change the host **without asking** |

There is no `full_auto` choice on the level, and that is deliberate. An
alert is a machine's opinion that something is wrong, and you are the one
who knows which opinions are specific enough to act on unattended — a
"service down" alert with an obvious fix, say, rather than "disk fills in
four hours". Naming them one by one keeps that judgement yours.

**At `approval`, subscribe to the email.** The request sits in the
approvals queue on the [Assistant](assistant.md) page and in the
[Overview's Pending queue](dashboard.md#pending), and if nobody opens
either it expires after `ai.approval_expiry_hours` and the session
finishes without the change. [Email notifications](notifications.md)
for **Approval requested** and **Approval about to expire** are what
make this level workable for an alert nobody is watching.

When the session may change something, its instructions say so: it was
started by an alert, nobody is watching, the alert text is data rather
than instruction, and it may only change the host in scope, to deal with
what the alert describes. At full auto they also say what a fix may be —
small and reversible, such as restarting a failed service — and what it
may not: installing or removing packages, editing configuration it has not
read, deleting data, rebooting. Those it reports instead. The command
classifier's denylist applies at every level, whatever the model is told.

A row whose session could change something carries a second tag beside
the investigation one — **approval required** or **full auto**. Hover it
for why.

### Full auto for named alerts

An alert on `ai.alert_full_auto_alertnames` runs at full auto only while
every one of these holds. The first that fails drops the session to
`ai.alert_autonomy_level` — the alert is still investigated — and the
row's tag says which:

1. The alert is **firing**.
2. It resolved to a **LabDog host**, and the session may touch only that
   host.
3. If it arrived by webhook, `[alerts] webhook_token` is **at least 32
   characters**. With full auto on, that token is all that stands between
   anyone who can reach the webhook and a root command on your hosts —
   `openssl rand -hex 16` makes one.
4. LabDog can **snapshot the host first**: `ai.snapshot_before_mutating`
   is on and the host has a Proxmox VM mapping. Turn
   `ai.alert_full_auto_requires_snapshot` off to allow full auto on bare
   metal, whose changes then have no rollback point.
5. **No other automatic fix is running** on the host.
6. The **cooldown** has passed: no full-auto session for this alert has
   changed this host in the last `ai.alert_remediation_cooldown_minutes`
   (60). A fix that does not hold, or a flapping alert, would otherwise
   make the same change over and over.
7. The host is under its **daily cap**: fewer than
   `ai.alert_remediation_daily_cap` (3) full-auto sessions changed it in
   the last 24 hours, across all alerts.
8. **LabDog does not run on the host.** A fix there could take LabDog
   down with it, and LabDog cannot roll back the machine it runs on — see
   [below](#the-machine-labdog-runs-on) for how it tells.
9. **The last automatic fix on the host has been checked**, and none made
   it worse in the last 24 hours — see
   [Checking the fix](#checking-the-fix-and-rolling-back). A second fix
   before the first is judged would leave the check unable to tell which
   one the host's state is down to.

"Changed" means a command the classifier called a change reached the
host — including one that exited non-zero, since a restart that failed
halfway still changed something.

A full-auto session also runs under its own caps:
`ai.alert_max_commands` (15) and `ai.alert_wall_clock_seconds` (900),
or the general caps if those are lower. Its model turns go the other
way: it gets at least five more than its command cap, even above
`ai.max_iterations`, so a fix that takes a command a turn is ended by
the command cap rather than a turn short of it. And it does not change a host
LabDog is itself changing: a change it attempts while a sync or an action
run is working on the host is refused, and it reports what it would have
done instead. That narrows the overlap rather than closing it — a sync
can still start while one of its commands is running.

Every full-auto change gets the same treatment as one from the
[Assistant](assistant.md): a snapshot first, the command in the audit
log, and the session's transcript under **view →**. The session's creation
is audited too, with the level and the reason for it.
Subscribe to **Automatic fixes** under
[Email notifications](notifications.md) to be told what changed — every
command, whether it worked, and the snapshot taken before it — and, once
it has been checked, whether the fix worked.

### Checking the fix, and rolling back

A full-auto session ends when the model says it is done, which is its own
opinion of its work. `ai.alert_remediation_check_minutes` (10) after the
session ended, LabDog checks for itself, whenever the session changed the
host. A session someone **cancelled** is not checked: they have most
likely taken the host over, and the check would judge — and perhaps roll
back — their work.

1. Can it still reach the host over SSH? Three attempts, twenty seconds
   apart, so a host that is restarting is not mistaken for a dead one. A
   changed SSH host key counts as unreachable: LabDog will not connect.
   When the host does not answer, LabDog asks Proxmox as well. If Proxmox
   does not answer either, the fault may be on LabDog's side — a lost
   route, DNS — and the fix is reported as **not checked** instead.
2. Has a new **critical** alert fired on the host since the first change,
   and is it still firing?
3. Has the alert the session was started for resolved?

The answer lands on the alert's row as a tag — **fixed**, **fix did not
work**, or **fix made it worse** — with what LabDog found on hover.

**A fix that made the host worse is rolled back** while
`ai.alert_auto_rollback` is on (the default). "Worse" is a yes to either of
the first two questions. LabDog restores the snapshot taken before the
session's first change, starts the machine, waits for SSH, and marks the
host out of sync. That costs something: the machine restarts and
**everything written on it since the snapshot is lost** — mail delivered,
files uploaded, rows committed. If LabDog's own sync or action run is
working on the host it waits up to ten minutes for it first, then checks
the host again: a sync that reloaded the firewall can make a host look
unreachable for a moment, and that is no reason to undo the fix. While
the rollback runs, syncs and action runs for the host wait in its queue,
and run after it. Full auto
then stays off that host for 24 hours, so a fix that broke it once is not
tried again straight away.

**A fix that only failed is not rolled back.** The alert still firing
means the fix did not work, not that it did harm: the host was already in
that state before. You are told, and the session has a
[Roll back](assistant.md#rolling-back) button if you want the host as it
was before the fix anyway.

A check that falls due while LabDog is down runs when it is back, up to
30 minutes late. Past that it is marked **fix not checked** rather than
made: judging — and perhaps rolling back — a host long after the fact
would throw away all that time's writes on stale evidence.

The check depends on hearing that the alert resolved, so leave **Disable
resolved message** off on the Grafana contact point. Without resolved
notifications every fix reads as not working.

#### The machine LabDog runs on

Rolling that machine back would stop LabDog half-way, with nothing left
to start it again, and put LabDog's own database back to the snapshot. So
LabDog refuses both full auto and rollbacks there. A host whose address is
`[security] labdog_server_ip` in LabDog's configuration — the address the
firewall rules keep SSH open for — is that machine. Otherwise LabDog finds
it from the address each host sees it connect from:

- **Every other host sees LabDog coming from this host's address.** A
  container on a bridge network — the usual Docker install — reaches the
  rest of the LAN through its host's address.
- **This host sees LabDog coming from one of its own addresses, or from
  inside one of its container bridges.** A native install, or a container
  with host networking or on a compose network. LabDog asks the host for
  its interfaces over SSH to tell.

A container on a **macvlan** network has a LAN address of its own and
looks like any other machine. Do not put that host's alerts on the
full-auto list.

### Scope

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
| `{starts_at}` | When the alert started, in UTC (2026-08-23 19:00:00 UTC) |
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

**Alerts appear but nothing is investigated.** Read the investigation tag. It names
which of the six gates stopped it.

**Every fix is marked "fix did not work", although the alerts cleared.**
LabDog never heard that they resolved. Check that **Disable resolved
message** is off on the Grafana contact point that points at LabDog.

**A fix made the host worse but it was not rolled back.** Hover the
**not rolled back** or **rollback failed** tag on the row: it says why —
`ai.alert_auto_rollback` is off, the snapshot was not taken or has
expired, LabDog's own work kept the host busy, or Proxmox refused. A
rollback on ZFS storage also needs every newer snapshot of the VM gone.
LabDog deletes the session's own, but not anyone else's: when a newer
snapshot belongs to an action run or someone else, it refuses before
deleting anything, and names the snapshot in the way.

**An alert on the full-auto list ran read-only (or at approval).** Hover
the level tag on its row. It names the safeguard that held it back — see
[Full auto for named alerts](#full-auto-for-named-alerts). Names are
matched exactly, so check the spelling against the alert name on the row
if there is no tag at all.

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
