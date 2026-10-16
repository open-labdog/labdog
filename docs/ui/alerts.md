# Alerts

**Path:** `/alerts` (Operations · Alerts)

Alerts LabDog has received from Grafana or Alertmanager, and what each one
set off. What an alert sets off is up to its **labels**, which you put on
the alert rule in Grafana:

| Label | What it does |
|---|---|
| `labdog_action=<action key>` | Runs that action on the alert's host — if the action is on the [list of actions alerts may run](#the-actions-alerts-may-run) |
| `labdog_ai=true` | Has the [AI assistant](assistant.md) investigate, when there is no action or the action did not fix the alert |
| `labdog_ai_automate=true` | The same, and the AI may fix what it finds, behind the [full auto safeguards](#full-auto-safeguards) |

An alert with none of them is recorded, and nothing else happens. See
[Labels](#labels) for how they combine.

The page has two tabs:

- **Received** — one row per alert, newest first: when it fired, status,
  severity, the alert name (with a `×N` repeat count), its summary, what
  its action did, and the investigation, with **investigate** or
  **view →** at the end of the row. Firing critical alerts are tinted. A
  **Firing / All** switch hides or shows resolved alerts. Firing alerts
  also land in the [Overview's Pending queue](dashboard.md#pending).
- **Handling** — what alerts are allowed to do: the actions alerts may
  run, a reference for the labels, whether alerts can get in, and whether
  the AI will answer them.

**Recording an alert, running its action and investigating it are
separate decisions.** Recording costs nothing and starts nothing. An
action runs only if it is on the list, which starts empty. Investigating
spends money without anyone asking, and is off by default.

---

## Turning it on

Two settings, in [Settings](settings.md): intake under Fleet defaults,
investigation under AI.

| Setting | Default | Effect |
|---|---|---|
| `alerts.intake_enabled` | `0` | Accept alerts at all |
| `ai.auto_investigate_enabled` | `0` | Start a session when an alert labelled `labdog_ai` or `labdog_ai_automate` arrives |

Plus a token in the config file — **not** in the UI settings, because
`/api/settings` is readable by any signed-in user and a shared secret does
not belong there:

```toml
[alerts]
webhook_token = "a-long-random-string"
```

or `LABDOG_ALERTS__WEBHOOK_TOKEN`. **An unset token refuses every
request**, and the **Intake** card on the Handling tab says so. An alert receiver that accepted unauthenticated POSTs would let
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
alerts.poll_minutes = 5     # 0 = never poll — the default
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
forever. Leave `alerts.poll_minutes` at `0` and use the webhook
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

## Labels

Put them on the alert rule in Grafana (**Configure labels and
notifications** in the rule's form). They travel with every notification
the rule sends, so LabDog reads them from the alert itself. How they
combine:

| The alert's labels | What happens |
|---|---|
| `labdog_action=restart-nginx` | LabDog runs `restart-nginx` on the host. Nothing else, whether or not it works. |
| `labdog_action=restart-nginx`, `labdog_ai=true` | It runs `restart-nginx`. If the run fails, or the alert is still firing afterwards, the AI investigates, told what was tried. |
| `labdog_action=restart-nginx`, `labdog_ai_automate=true` | The same, and the AI may fix what it finds. |
| `labdog_ai=true` | The AI investigates, at `ai.alert_autonomy_level`. |
| `labdog_ai_automate=true` | The AI investigates and may fix what it finds. |
| none | Recorded only. |

The AI values must be exactly `true`. `labdog_action` takes an action's
key, as shown under each action on the [Actions](actions.md) page.

To make a label depend on the host, give it a template. The
[alloy-install action](actions.md) stamps `labdog_hostname` (and
`labdog_host_id`) on every series it ships, so the template can use
LabDog's own names:

```
labdog_action      = restart-nginx
labdog_ai          = true
labdog_ai_automate = {{ if match "^(web-1|web-2)$" $labels.labdog_hostname }}true{{ else }}false{{ end }}
```

Write the `false` branch too rather than leaving the label empty.

With labels, **whoever can edit alert rules in Grafana decides what an
alert asks for.** LabDog keeps the last word on what it does: only an
action on the list runs, only on the hosts its entry allows, and full
auto only while every safeguard holds.

---

## Running an action

### The actions alerts may run

The **Handling** tab lists the actions an alert may start. **The list
starts empty**, so `labdog_action` does nothing until an action is on it:
an alert naming any other action is recorded and refused, and its row says
so. **Allow an action** adds one:

| Field | What |
|---|---|
| **Action** | Any action that can run on a single host, because a run an alert starts is always the **one host the alert names**. One entry per action. It cannot be changed afterwards, since Grafana rules point at it by name. |
| **Where** | All hosts, a group, or one host. An alert on a host outside it is refused. |
| **Parameters** | Fixed, and checked like any other run's. Labels are not a way to pass them. |
| **Snapshot / verify / rollback** | As for any run of a destructive action. |
| **Cooldown** (60 min) | No second alert run of the action on the same host within this time. 0 means none. |
| **Enabled** | Off pauses the entry without removing it; alerts naming it are refused. |

Taking an action off the list refuses it from then on. What earlier
alerts did with it stays on their rows.

### When an alert arrives

LabDog checks, in this order:

1. Only a **new firing** alert counts. Repeat notifications of the same
   firing, and resolved notifications, never start a run.
2. The alert must name a **host LabDog manages**. See [Scope](#scope) for
   how LabDog works that out.
3. The action must exist and be **on the list**, enabled, with the host
   inside its **Where**.
4. An alert that came **by webhook** needs `[alerts] webhook_token` to be
   at least 32 characters, as for full auto. The token is all that stands
   between whoever can reach the webhook and a run on your hosts.
5. No alert run of the action may be queued, waiting or running on the
   host, and the **cooldown** must have passed.

If all hold, LabDog starts a single-host run of the action with the
entry's parameters. It runs like any other: it waits for a busy host,
takes a snapshot first if the action is destructive and the entry says
so, and shows on the [Runs](operations.md#runs) page. The run page says which alert
started it.

If the action could not run, the row's **action** column says why: *not
allowed*, *unknown action*, *no host*, *token*, *already running* or
*cooldown*, with the detail on hover. An alert that also asks for the AI
then goes to it anyway, and its prompt says which action was asked for
and why it did not run — except when the action is *already running*,
because that run decides.

### Afterwards

While the action runs, an investigation the labels ask for **waits**.
Once the run has finished, LabDog checks every minute:

| Outcome | When |
|---|---|
| **fixed** | The alert resolved. |
| **did not fix it** | The run succeeded, but the alert is still firing `alerts.action_check_minutes` (10) after the run finished. |
| **run failed** | The run failed or was cancelled. |

If the action did not fix it and the alert is labelled `labdog_ai` or
`labdog_ai_automate`, it goes to the AI, through the same checks as any
investigation (below). Its prompt gains a section saying what LabDog
already tried: the action, the run, how it ended, and the end of its
output. Without either label nothing more happens.

Grafana must send **resolved notifications** for **fixed** to be told
apart from **did not fix it**. Subscribe to **Actions run by alerts** on
the [Email](notifications.md) page for a message with each outcome.

---

## When an investigation starts

An alert must clear every one of these:

1. `ai.auto_investigate_enabled` is on.
2. It **asks for the AI**: it is labelled `labdog_ai=true` or
   `labdog_ai_automate=true` — or, with `ai.alert_full_auto_rules_enabled`
   on, it matches one of [the rules](#which-alerts-may-run-at-full-auto).
3. The alert is **firing** — a resolved alert has nothing live to look
   at, and a session would read a healthy host and report that nothing is
   wrong.
4. It is **new**, not a repeat notification about something already being
   investigated.
5. `ai.enabled` is on, a provider that can run tools is configured, and
   the AI budget is not spent.

When its `labdog_action` started a run, the investigation waits for it —
see [Afterwards](#afterwards).

The prompt the session starts from is `ai.alert_mission_template` — see
[Settings](settings.md#the-investigation-prompt) if you want to change what
it asks.

**There is no rate limit on investigations, and that is deliberate.**
(Automatic *fixes* have one — see
[Full auto](#full-auto-safeguards).) LabDog does not own
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
happened" has several causes and each has a different fix:

| Tag | Meaning |
|---|---|
| **investigating** | A session was started; **view →** opens it |
| **auto-investigate off** | The policy switch is off |
| **not labelled for the AI** | Neither `labdog_ai=true` nor `labdog_ai_automate=true`, and no full-auto rule matched |
| **already resolved** | It cleared before LabDog got to it |
| **already investigated** | A repeat notification |
| **AI budget reached** | Spend limit hit; the alert is still recorded |
| **could not start** | AI disabled, no provider, or a provider that cannot run tools |
| **waiting on action** | Its `labdog_action` is running; the AI looks only if that does not fix it |
| **handled by action** | Its action fixed it, or the alert does not ask for the AI, or another alert's run of the action was already going |
| **below severity threshold** | Only on alerts from before 1.0.0, when a severity threshold decided |

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

---

## Investigating by hand

**investigate** on a firing alert's row starts a session whatever its
labels say — you have already made the judgement `labdog_ai` exists to
automate.

It does **not** bypass the kill switch, the provider check, or the
budget. Those are about whether LabDog may spend at all, which a button
press does not change.

---

## What the session can do

By default, **only look**. Two settings let it do more:

| Setting | Default | Effect |
|---|---|---|
| `ai.alert_autonomy_level` | `read_only` | What an alert's session may change: `read_only`, or `approval` — each change it wants to make waits for someone to approve it |
| `ai.alert_full_auto_rules_enabled` | `0` | Which alerts may change the host **without asking**: off, those Grafana labels `labdog_ai_automate=true`; on, those matching `ai.alert_full_auto_rules` — see [below](#which-alerts-may-run-at-full-auto) |

There is no `full_auto` choice on the level, and that is deliberate. An
alert is a machine's opinion that something is wrong, and you are the one
who knows which opinions are specific enough to act on unattended — a
"service down" alert with an obvious fix, say, rather than "disk fills in
four hours". Marking them one by one, on the alert rule or in a rule here,
keeps that judgement yours.

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

### Which alerts may run at full auto

**By default, the ones Grafana labels `labdog_ai_automate=true`** — with
a template if only some hosts should qualify; see [Labels](#labels). With
this, the decision lives with the alert rule, and whoever can edit alert
rules in Grafana decides which alerts may change your hosts unattended.

**With `ai.alert_full_auto_rules_enabled` on, `ai.alert_full_auto_rules`
decides instead.** One rule per line. A rule is conditions separated by
commas, all of which must hold, and an alert qualifies when any rule
does. There is no order and no "deny" line, so no rule overrides another.

| Condition | Holds when |
|---|---|
| `NginxDown` | the alert's name is exactly this (spaces allowed, no wildcards) |
| `severity=critical` | the alert has this label value; quote a value with a comma: `team="a, b"` |
| `severity!=info` | the label has another value, or is missing |
| `host:web-1`, `host:web-1\|web-2` | the LabDog host the alert resolved to is one of these (by hostname, any case) |
| `group:web` | that host is in this LabDog group |
| `!host:…`, `!group:…` | that host is not one of these, or in none of these groups |

Lines starting with `#` are comments. The default list is the single rule
`labdog_ai_automate=true`, so turning the rules on changes nothing until you
edit it, and an empty list lets **no** alert run at full auto. Two alerts
that both hosts can raise, each allowed on one host only:

```
NginxDown, host:web-1
DiskFull, host:db-1
```

| Alert on | NginxDown | DiskFull |
|---|---|---|
| web-1 | full auto | the base level |
| db-1 | the base level | full auto |

Groups keep a long list short, and `!` makes an exception:
`DiskFull, group:db, !host:db-legacy`. Labels and hosts combine:
`labdog_ai_automate=true, group:lab` trusts Grafana's label, but only for
hosts in `lab`.

An alert a rule lets run at full auto is also one that gets investigated,
with or without `labdog_ai`.

Saving a rule that names a host or group LabDog does not have is refused.
If one is renamed or deleted later, the setting shows a warning beside the
list, because the rule would otherwise silently match nothing.

**Where to see it.** An alert that ran at full auto says which rule it
matched on its row, for example *Full auto: matched NginxDown, host:web-1*.
A host's Overview has an **automatic fixes** panel listing the actions
alerts may run on it, the rules that can cover it, and anything that
would hold every alert's full auto back on it right now: intake off, a
short token, no snapshot possible, a fix that made it worse in the last
24 hours (with when that lifts), or a fix not yet checked.

### Full auto safeguards

An alert marked for full auto runs at it only while every one of these
holds. The first that fails drops the session to
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
looks like any other machine. Do not mark that host's alerts for full
auto.

### Scope

The session is scoped to the host LabDog resolved from the alert's
labels. It checks `labdog_host_id` first: the alloy-install action stamps
it on every series, so an alert on those metrics names its host exactly.
An id that is no longer a host is skipped. Then it tries `labdog_hostname`,
`nodename`, `hostname`, `host`, `node`, and last `instance` (stripping any
port). If none of them match a managed host the
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
`alerts.intake_enabled`, then that the contact point's token matches —
a wrong token is a 401 at LabDog and a delivery error in Grafana's own
logs.

**Alerts appear but nothing is investigated.** Read the investigation tag. It names
which gate stopped it. *Not labelled for the AI* means the alert rule in
Grafana has neither `labdog_ai=true` nor `labdog_ai_automate=true`.

**An alert's action did not run.** Hover the tag in its **action** column:
the action is not on the Handling tab's list, is paused there, does not
cover that host, or ran on it too recently. The label's value must be the
action's key exactly.

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

**An alert marked for full auto ran read-only (or at approval).** Hover
the level tag on its row. It names the rule it matched and the safeguard
that held it back — see [Full auto safeguards](#full-auto-safeguards).
With no tag at all, nothing matched. Names and label values are matched
exactly, so check the spelling against the row, check that the label's
value is exactly `true`, and check which host the row resolved to.

**The poller reports "No Alertmanager API at this endpoint".** Mimir
serves Alertmanager under `/alertmanager` on the same host as its query
API. If your deployment does not, leave `alerts.poll_minutes` at
`0` and use the webhook alone.

**The poll runs cleanly but never records anything.** Almost always
because your alert rules are Grafana-managed and Mimir's Alertmanager
cannot see them — see [Alertmanager poll](#alertmanager-poll-catch-up).
The poll cannot detect this itself: an Alertmanager with nothing routed
to it and an Alertmanager with nothing firing return the same empty
list.
