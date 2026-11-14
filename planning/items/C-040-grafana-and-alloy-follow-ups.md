# C-040: Grafana and Alloy follow-ups

| | |
|---|---|
| Edition | community |
| Source | roadmap, board item "Grafana and Alloy follow-ups: ingest auth, Loki logs, more metrics, group labels, examples" |
| Tier / score | P3 / 2.3 |
| Stage | parked |
| Effort | L |
| Autonomy | B |
| Depends on | none |

## Goal
The metrics integration works against backends that need credentials or a private CA for ingest, shows logs and more metrics on the host page, and the `alloy-install` action labels metrics by group and ships worked examples.

## Why it is parked
The item is six small follow-ups across two repositories, each a deliberate deferral from 0.4.0. It must be split when promoted; none is needed by a staged item.

## What would un-park it
Promote any one of these as its own item:
1. Pass ingest auth and a private CA to the Alloy action (backend and pack).
2. Loki logs on the host page (backend and frontend).
3. More metrics and tuning: network, per-mount disk, thresholds, refresh interval.
4. Label metrics with group membership (backend extra-var and pack templates).
5. Example group configs in the pack.
6. Document the `alloy_custom_services` extension point in the pack.

## Context
- Backend: `backend/app/grafana/service.py` `build_metrics_extra_vars` (line 245) builds the `metrics_backend` vars passed to the action, with URL, tenant and TLS flag but no credentials or CA. `resolve_host_instance` (line 111) picks the destination. `backend/app/grafana/models.py` already stores Loki instances. Queries are in `backend/app/grafana/metrics.py` (`fetch_host_metrics` line 56, CPU, memory and root disk only).
- Follow-up 1 must keep the secret out of the run log and out of ansible-runner's `env/extravars`; the board suggests a file the playbook reads under `no_log`, mode `0600`.
- Follow-up 2: the assistant already queries Loki (`backend/app/ai/tools/logs.py` `_query_loki`), which is a starting point for a host-page reader.
- Pack: `/home/user/unbork-playbooks/actions/alloy-install/`, template `roles/role-alloy-linux-configure/templates/endpoints.alloy.j2`, and `roles/role-alloy-linux-services/`.
- Frontend: `frontend/components/host-metrics-section.tsx`, `metrics-destinations-panel.tsx`, `frontend/app/(dashboard)/hosts/[id]/_tabs/metrics.tsx`.

## Assumptions
- Follow-ups 5 and 6 may be stale: the checked-out pack has no `group_configs/` directory and no `alloy_custom_services` variable (searched the whole repository). Re-check against the pack before promoting either.
- Assumption (not yet confirmed): for follow-up 4, one `labdog_groups` label per host (a comma-separated list) is preferred over one label per group, to keep series counts down. Question for Dennis, with the label name subject to the rename ([D-02](../DECISIONS.md)).
- Pack changes are tested with the commands in the pack's own README and on hosts named "test" ([A-04](../DECISIONS.md)).
