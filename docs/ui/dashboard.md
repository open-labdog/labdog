# Dashboard

![Dashboard](screenshots/dashboard.png)

The dashboard gives a fleet-wide health overview at a glance.

## Metric Cards

| Card | What it shows |
|------|---------------|
| **Total Hosts** | All hosts registered in Barricade |
| **Hosts in Sync** | Hosts whose current state matches desired state |
| **Hosts Drifted** | Hosts where actual state has diverged from desired |
| **Hosts with Errors** | Hosts where the last sync or drift check failed |
| **Unknown / Pending** | Hosts that have never been checked |
| **Last Fleet Check** | When the most recent drift check ran |
| **Never Checked** | Count of hosts with no drift check history |
| **Never Synced** | Count of hosts that have never had Ansible applied |

The page auto-refreshes every 30 seconds.

## Host Table

Below the cards, a table lists every host with its IP address, current status badge, last check time, and last sync time. Click any row to go to that host's detail page.

**Status badges:**

| Badge | Meaning |
|-------|---------|
| `In Sync` (green) | All modules match desired state |
| `Out of Sync` (amber) | At least one module has drifted |
| `Error` (red) | Last check or sync returned an error |
| `Unknown` (grey) | Host has never been checked |

## Check All

The **Check All** button in the top-right triggers an immediate drift check across every host that has drift detection enabled. Results appear as the checks complete (the table refreshes automatically).
