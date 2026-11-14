# C-050: Rename: on-host markers, alert labels, metrics and pack variables

| | |
|---|---|
| Edition | community |
| Source | proposed (PR-02), decision D-02 |
| Tier / score | P0 / 4.3 |
| Stage | 2 |
| Effort | L |
| Autonomy | B |
| Depends on | C-048 |

## Goal

Everything unbork writes outside itself carries the new name: firewall chains and comments, drop-in files, certificate files, Proxmox snapshots, alert labels, metric names and the variables handed to packs. A host, alert rule, dashboard or pack that still uses the old names keeps working, and a host's first sync after the upgrade replaces its old markers without a moment of missing firewall coverage.

## Scope

In scope:
- Firewall: chain names, rule comments, backend detection, competing-store probe, teardown tasks.
- Other host files: the NetworkManager DNS file, the cloud-init file, systemd drop-ins, CA certificate files, file headers.
- Proxmox snapshot name prefixes.
- Alert labels, the `/metrics` family names, host-metric queries, pack extra-vars.
- The two playbook repositories (separate PRs there).

Out of scope:
- Config, env, cookies (C-048); install paths and packages (C-049); prose (C-051).
- Removing the old names. They are recognised permanently on hosts and for the whole deprecation period elsewhere.
- Rewriting history already stored in Prometheus, Mimir or Loki.

## Context

Firewall, all in `backend/app/`:
- `rules/renderers/iptables.py:5,70-89`: `_COMMENT_PREFIX = "Managed by LabDog"`, chains `LABDOG-INPUT` and `LABDOG-OUTPUT`. `rules/renderers/nftables.py:15`: the same comment prefix.
- `sync/parsers/iptables.py:13-14,179-181`: only the two `LABDOG-` chains are read. `sync/plan.py:86` rebuilds the comment for display. `sync/diff.py` compares with `matches()`, which ignores comments, so an old comment alone is not drift.
- `api/host_state.py:795-801`: `_PROBE_NFT_LABDOG` greps `"Managed by LabDog"`, `_PROBE_IPT_LABDOG` tests `-S LABDOG-INPUT`; used by `_detect_firewall_backend` (marker stickiness, line 953) and `_probe_competing_firewall_store` (line 812).
- `ansible_runtime/generator.py`: `_iptables_teardown_task` (line 80) and `_nftables_teardown_task` (line 115) match the old names; `generate_iptables_playbook` inserts the jumps at lines 322-346 after `iptables-restore --noflush`, inside the 60 second deadman revert.

Other host files: `resolver/generator.py:3,126` and `resolver/collector.py:59` (`99-labdog-disable-network-config.cfg`, `90-labdog-dns.conf`); `services/generator.py:35,44,200` and `services/collector.py:293` (`<svc>.service.d/labdog.conf`); `ca_certs/generator.py:37,131` (`labdog-<fp>.crt`, reconcile pattern `labdog-*.crt`); headers in `resolver/renderer.py` and `hosts_mgmt/merge.py:223`. The hosts and resolver collectors skip comment lines, so a header is never drift.

Proxmox: `workflows/snapshot_cleanup.py:47-49`, `workflows/steps/snapshot.py:58`, `ai/snapshots.py:48`.

Wire names: `alerts/labels.py:59-60` (`labdog_action`, `labdog_ai`), `ai/full_auto_rules.py:47` (`labdog_ai_automate`), `ai/alerts.py:56-57` (`labdog_host_id`, `labdog_hostname`, read in `resolve_host`), about 24 label strings in the frontend. `metrics/collector.py` defines 48 `labdog_*` families; `with_meta` (line 699) is the single exit. `grafana/metrics.py` selects on `labdog_host_id` (lines 28-169). `grafana/service.py:269` injects `labdog_host_id` and `labdog_hostname`; `tasks/action_host.py:749` merges them.

Packs: `unbork-playbooks/actions/alloy-install/roles/role-alloy-linux-configure/templates/endpoints.alloy.j2:9-10,59-64` stamps the two labels; role variables `labdog_alloy`, `labdog_docker`; `LABDOG_SRC` in the validate scripts. The private pack has `labdog-rollback/` image tags and an apt proxy script named `labdog-apt-proxy-detect` on hosts.

`docs/upgrade.md:44-60` lists the alert labels, metric names and pack format as stable.

## Dependencies

- C-048. [D-02](../DECISIONS.md) and `/mnt/project-files/rename-depth/rename-map.md`.
- From Dennis: at least two hosts named "test" (one nftables, one iptables, ideally one of them running Docker), one with a Proxmox VM mapping, and a 1.1.0 instance on `lin-manager-2` to sync them first.
- Write access to `open-unbork/unbork-playbooks` and the private pack for their PRs.

## Implementation steps

Land as three PRs in this order. Each is releasable alone.

PR 1, host markers:
1. Add `backend/app/markers.py`: `CHAIN_INPUT = "UNBORK-INPUT"`, `CHAIN_OUTPUT`, `LEGACY_CHAINS`, `COMMENT_PREFIX = "Managed by unbork"`, `LEGACY_COMMENT_PREFIX`, `MARKER_GREP = "Managed by \(unbork\|LabDog\)"`, file names and snapshot prefixes, each with its legacy twin. Every site below imports from here.
2. Renderers write the new chains and comment. The iptables parser maps all four chain names; the nftables parser strips either prefix where it shows the comment. `plan.py:86` uses the constant.
3. `host_state.py`: both probes match either name. A host with only old markers is "ours" for stickiness. Add a collection warning, next to the competing-store one: "This host still carries LabDog-named firewall markers. The next firewall sync renames them."
4. `generate_iptables_playbook`: keep the order restore, then insert jumps to the new chains at position 1, then a new task `_legacy_iptables_chains_task()` that removes the old jumps and deletes the old chains for IPv4 and IPv6, then cancel the deadman, then persist. The new chain ends in the default-policy rule, so traffic is decided there while both jumps exist. The backup taken at the start still holds the old chains, so the deadman revert restores them.
5. Both teardown tasks remove footprints under either name. The nftables playbook needs no extra step: it replaces the table in one `nft -f`.
6. Resolver, services and CA generators write the new file name and remove the old file later in the same play, after the new one is in place and before any reload. Collectors read the new name, then the old. The CA reconcile pattern covers both prefixes.
7. Snapshots: create with `unbork-` and `unbork-ai-`; `snapshot_cleanup.py`, `snapshot_reaper.py` and the AI cleanup match both.

PR 2, wire names:
8. Alerts: a `canonical_labels(labels)` helper in `alerts/labels.py` that, for each of the five names, copies the value to the other name when only one is present. Apply it once at intake in `backend/app/api/webhooks.py` so rules, the AI and the UI see both. Constants move to the `unbork_` names. The frontend shows the new names and mentions the old ones once in help text.
9. Metrics: families are named `unbork_*`. Add `MetricsConfig.legacy_names: bool = True`; when set, `with_meta` appends a copy of every family under its `labdog_*` name with HELP "Deprecated alias of unbork_…".
10. `grafana/metrics.py`: build each selector as `expr{unbork_host_id=…} or expr{labdog_host_id=…}` and read either label from results, so a host whose Alloy config predates the rename still has graphs.
11. `build_metrics_extra_vars` returns both `unbork_host_id`/`unbork_hostname` and the old pair. Update the manifest docs in `actions/manifest.py:156-158`.

PR 3, packs (in their repositories, after PR 2 is on `dev`):
12. `alloy-install` stamps both label pairs, reading `unbork_host_id | default(labdog_host_id)`. Rename role variables with `default()` fallbacks; scripts accept `UNBORK_SRC` then `LABDOG_SRC`. Private pack: new rollback tag and script names, with cleanup that also recognises the old ones.

## Acceptance criteria

- Unit: a rendered iptables rules file contains only `UNBORK-` chains; the parser returns the same specs for a 1.1.0 `iptables-save` fixture and a new one; both probes succeed against old-name and new-name fixtures.
- On an iptables "test" host last synced by 1.1.0: before any sync the host shows as managed with the legacy warning and no firewall drift. After one sync `iptables -S` has no `LABDOG-` chain or jump, `UNBORK-INPUT` is rule 1 of `INPUT`, and a continuous probe to an allowed port and a denied port during the sync never sees the denied port open or the allowed port closed. The state survives a reboot.
- On an nftables "test" host: same, checking `nft list table inet filter` and `/etc/nftables.conf`.
- After one full sync no file named `*labdog*` written by 1.1.0 remains under `/etc/NetworkManager/conf.d`, `/etc/systemd/system/*.service.d` or the CA trust directories, and the service overrides are still in effect.
- An alert labelled `labdog_action=<key>` and one labelled `unbork_action=<key>` both start the action. An alert carrying only `labdog_host_id` resolves to its host.
- `/metrics` exposes `unbork_sync_jobs_total` and, by default, `labdog_sync_jobs_total` with equal values; with `legacy_names = false` no `labdog_` family is present.
- Host metric graphs render for a host whose Alloy config was deployed by 1.1.0.
- A pack playbook that uses `labdog_host_id` still receives it.

## Test plan

- `cd backend && source .venv/bin/activate && pytest tests/test_markers_legacy.py tests/test_firewall_teardown.py tests/test_iptables_persistence.py tests/test_metrics_endpoint.py tests/test_metrics_examples.py tests/test_alert_label_aliases.py -q` (`test_markers_legacy.py` and `test_alert_label_aliases.py` are new), then the full suite.
- `cd frontend && npx tsc --noEmit && npm run lint`.
- Packs: `UNBORK_SRC=../unbork python scripts/validate_pack.py`, `ansible-lint`, and `tests/e2e.sh` in each pack repository.
- Real hosts, in this order and never skipping ahead: (1) a 1.1.0 stack on `lin-manager-2` syncs the "test" hosts; (2) upgrade that stack to the branch image; (3) run the host criteria above on each "test" host, firewall first; (4) only when all pass, a final read-only check on `lin-manager` (collect state, no sync) to confirm real hosts show as managed with the legacy warning. No sync against a non-"test" host is part of this item.

## Docs and changelog

- `docs/metrics-export.md`, `docs/ui/alerts.md`, `docs/ui/assistant.md`, `docs/ui/actions.md`, `docs/ui/host-metrics.md`: new names, with the old ones listed as deprecated aliases. `docs/upgrade.md`: what the first sync changes on a host and the deprecation period.
- `CHANGELOG.md` `[Unreleased]`, Changed: "Firewall chains, managed-file names, snapshot names, alert labels, metric names and pack variables use `unbork`. Old names are still recognised; each host is renamed on its next sync, and `labdog_*` metrics and labels remain as deprecated aliases."

## Risks and rollback

- A firewall gap or lockout on a managed host. Mitigations: the ordering in step 4, the existing deadman revert, and the "test" hosts gate. Do not merge PR 1 before the host criteria pass.
- Downgrading the server after hosts were renamed: 1.1.0 does not know `UNBORK-` chains and would treat the host as unmanaged. Rollback is therefore "revert the PR and re-sync", with the legacy removal task kept in the revert so both chain sets are not left active. State this in `docs/upgrade.md`.
- Doubled metric series while `legacy_names` is on. Documented, with the switch.
- Rollback for PR 2 and PR 3: revert; both names were accepted throughout.

## Assumptions

- [D-02](../DECISIONS.md): full rename with fallbacks, "test" hosts first.
- Assumption (not yet confirmed): old host markers are recognised with no end date; old labels, metrics and pack variables stay for all of 2.x. Question for Dennis: confirm the period that `docs/upgrade.md` will state.
- Assumption (not yet confirmed): old metric names are emitted by default behind `metrics.legacy_names`, in preference to a relabel snippet, because the stage requires existing dashboards to keep working unchanged.
- Assumption (not yet confirmed): hosts are renamed lazily on their next sync, not by a forced fleet-wide sync at upgrade.
- Assumption (not yet confirmed): Proxmox snapshot prefixes belong to this item.

## Definition of done

- [ ] Three PRs merged (`dev` here, default branches of the pack repositories).
- [ ] Host criteria recorded per "test" host in `planning/PROGRESS.md`.
- [ ] Docs and changelog updated.
- [ ] `planning/roadmap.yaml` status set to done.
