# Drift detection

Drift detection asks a host "does your live configuration still match what
LabDog says it should be?" and records the answer. It is read-only — a drift
check never changes anything on the host. Fixing drift is a sync.

Checks run on a timer (`drift.check_interval_minutes`, default 30, see
[Settings](settings.md)) and can also be run on demand from the Host detail
page.

## It is off by default, on every host

`drift_check_enabled` defaults to **off** for newly added hosts. The periodic
sweep runs on schedule regardless and simply finds no hosts to check, so a
fresh install shows no error, no warning, and no checks — indefinitely.

This is worth stating plainly because everything downstream of drift goes
quiet too, and each looks like a separate malfunction:

| Surface | What you see when drift is off everywhere |
|---|---|
| Dashboard → Fleet Overview | `Never Checked` counts every host, permanently |
| Dashboard drift-trend chart | "Drift history is being collected" forever |
| `/metrics` exporter | No `labdog_drift_*` series at all — absent, not zero |

The exporter's `labdog_hosts_drift_check_enabled` gauge is the quickest way to
see the fleet-wide answer. See [Metrics export](../metrics-export.md).

## The two flags

There are **two independent** `drift_check_enabled` flags, and they are not
hierarchical — neither one gates the other.

| Flag | Governs | Set from |
|---|---|---|
| `Host.drift_check_enabled` | **Firewall drift only** | Hosts list bulk action; Host → Overview → "Drift Monitoring" badge; the **Firewall** module row's Enable/Disable Drift Check action |
| `HostModuleStatus.drift_check_enabled` | One module, for one host — **services, /etc/hosts, users, cron, packages, resolver** | That module's tab on the Host detail page |

Firewall is the exception because it predates per-module toggles: its sweep
is the only one that selects candidates from the host-level flag
(`host_gated=True` in `app/tasks/drift_sweep.py`). The other six each read
their own row.

Three consequences follow, and none of them is guessable from the UI:

1. **Turning on host-level drift does not turn on the other six modules.**
   The Overview badge reading `Enabled` means firewall drift is on. Services,
   cron, packages and the rest each stay off until switched on individually.

2. **Turning on a module does not turn on firewall drift.** Enabling drift on
   the Services tab writes only the services row.

3. **The Firewall row's toggle is the host-level switch.** Clicking Enable
   Drift Check on the Firewall module row does the same thing as the Overview
   badge and the Hosts-list bulk action — all three write the same flag. It is
   the one module row whose control is not module-scoped.

### Turning it on for a whole fleet

The Hosts list has a bulk **Enable Drift Check** action for the host-level
flag. There is no bulk equivalent for the per-module flags; those are set per
host, per module.

## Per-module drift settings API

Each module exposes its own toggle under its own route prefix, which is why
there is no single endpoint for "drift settings":

| Module | Endpoint |
|---|---|
| Firewall (host-level) | `PUT /api/drift/hosts/{id}/settings` |
| Services | `PUT /api/services/hosts/{id}/drift-settings` |
| /etc/hosts | `PUT /api/hosts-mgmt/hosts/{id}/drift-settings` |
| Linux users | `PUT /api/linux-users/hosts/{id}/drift-settings` |
| Cron | `PUT /api/cron/hosts/{id}/drift-settings` |
| Packages | `PUT /api/packages/hosts/{id}/drift-settings` |
| Resolver | `PUT /api/resolver/hosts/{id}/drift-settings` |

All take `{"drift_check_enabled": true|false}`.

Note the firewall row: its path ends `/settings`, not `/drift-settings`, and
it writes the host record rather than a module row.

## What a check does not do

- It does not change the host. A drift verdict is a status, not a repair.
- It does not run while the host is busy. A drift sweep that meets a
  running sync or action run skips that host for the tick and picks it up on
  the next one, rather than queueing behind it.

## See also

- [Hosts](hosts.md) — where the per-host controls live
- [Dashboard](dashboard.md) — the fleet-wide drift trend
- [Settings](settings.md) — `drift.check_interval_minutes`
- [Metrics export](../metrics-export.md) — `labdog_drift_*` and
  `labdog_hosts_drift_check_enabled`
