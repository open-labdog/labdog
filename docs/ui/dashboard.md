# Overview

**Path:** `/overview` (`/dashboard` and `/` redirect here)

The landing page: fleet state and what is waiting on you, weighted evenly.
It is the first zone on the rail, and the only one whose rail icon carries a
badge. The badge counts only what blocks or expires — assistant approval
gates, firing alerts, and one for the discovery queue when it is non-empty.
Drift never inflates it: a badge that says 214 because of drift findings is
a badge you stop reading.

The Overview pane lists five views of the same data. Each is a URL
(`/overview?view=pending` …), so any of them can be deep-linked.

## Summary

The default view. Every panel is a filter, a list or a trend — there are no
single-number cards.

| Panel | What it shows |
|-------|---------------|
| **Fleet status bar** | in sync · drifted · syncing · failed · unknown, segment width proportional to count. Every segment is a click-through into the hosts list filtered to that status. |
| **Pending** | The first four items of the queue (see below), soonest to expire first, with **Review** and, where allowed, **Hide**. |
| **Activity · last 24h** | Applies, action runs, scheduled runs and state collections from the last day, failures pinned to the top. **open →** on a run goes to its transcript. |
| **Drift trend** | How many hosts are drifted right now and a 14-day sparkline of drifted checks. When drift checking is off on every host it says so, with a link to turn it on — an empty chart otherwise reads as "all clear". |
| **Stale hosts** | Hosts not synced in 30+ days (or never), oldest first. The failure mode nobody notices: neither drifted nor failed, just forgotten. |
| **Upcoming** | The next enabled schedules with their blast radius — how many hosts, and whether a snapshot is taken first. |

**Drift-check fleet** (top right) queues a state collection on every host,
the same operation the old Collect State button ran; **Plan a sync** opens
[Plans](operations.md#plans).

## Pending

One queue, typed lanes, sorted by expiry rather than recency. Nothing here
is a message: an item is a decision waiting to be made, and it leaves the
list when the decision is.

| Lane | Source | Expires |
|------|--------|---------|
| **Approvals** | Assistant sessions paused at an approval gate; discovered hosts awaiting approval, one item per scan | Gates carry the approval's expiry; discovered hosts do not expire |
| **Alerts** | Alerts currently firing (Grafana webhook or Alertmanager poll), critical ones marked blocking | "firing" for as long as they fire |
| **Drift** | One informational item when any host is drifted | never |

**Review** goes to the place the decision is made — the session, the
Discovery queue, the alert, Drift. **Hide** removes an alert or a discovery
item from *this browser's* queue only; it stays in history and comes back in
a new session. Approval gates cannot be hidden: a hidden gate would still
block its session, and the only honest way off the list is a decision.

## Fleet state

The status bar again, with every stale host, a per-group breakdown (host
count and how many of them are drifted; click a row for the hosts list
filtered to that group) and the drift trend at full size. This is standing
condition, not a queue.

## Activity

The whole last 24 hours, failures first, with a link to the full
[Runs](operations.md#runs) history.

## Upcoming

Every schedule with its cron, scope and blast radius, plus **integration
health**: what is connected (Proxmox, Grafana/Mimir/Loki, Git remotes, the
AI provider and today's spend, the Prometheus export) — the things the
scheduled runs depend on.
