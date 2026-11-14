# Decisions

Open questions first, then the log of decisions made. Each open question
lists its options, a recommendation and the items it blocks. An item that
depends on an open decision is not scheduled until the decision is logged
below. Questions about Enterprise items (D-05, D-06, D-08 to D-12, D-15,
D-16) are in the Enterprise repository's `planning/DECISIONS.md`.

## Open

## Assumptions made without asking

- A-01. "Harden the webhook" (C-022) is taken from the board text as
  written; the spec will quote it and add nothing.
- A-02. C-015 is planned although its code is in the private pack
  repository, because the board item names those two actions.
- A-03. Board P3 items stay parked, except C-029, C-030 and C-037, which
  were raised to P2. The ranking table says why for each.
- A-04. Stage runs test on local Docker on lin-manager-2, and on hosts
  named "test" when a real host is needed. The live instance on
  lin-manager is semi-production and other hosts must never break.

## Log

| Date | Decision | By |
|---|---|---|
| 2026-11-14 | D-23 (kennel scope): an isolated sandbox network is supported from the start (a VLAN or SDN zone with no route to production, a jump host for unbork's SSH, outbound NAT), so rehearsing a change on a copy of a VM is in scope. PBS restore goes beside the original first; in-place restore over a production VM comes later, opt-in, with a second token, and only inside an unbork run. The name stays "kennel" for now and is renamed later. | Dennis, in the kennel thread |
| 2026-11-14 | D-22: build kennel (working name). C-028 leaves the parked list and is split and staged in Phase 4 as its own track. Scope to plan, from the exploration write-up and not yet confirmed by Dennis: a standalone AGPL "VM operations service" owning restore tests, PBS restore points, leases for surge nodes and test VMs, sandbox copies, a sweeper and a per-VM readiness API. Sandbox networking, in-place restore and the name are settled in D-23. C-045 moves with it. | Dennis, in the kennel thread |
| 2026-11-14 | D-04: Claude Code is installed on first use and no longer ships in the image (S-009, C-026). Community keeps only the operator's own Claude Code sign-in: new item S-014 removes the subscription token field and corrects its help text (`frontend/app/(dashboard)/ai-providers/client-page.tsx`, `docs/ui/assistant.md`), stage 1, S, class B. | Dennis, as relayed by the coordinator |
| 2026-11-14 | D-19: RAM in unbork, PBS in kennel. C-044 (memory in the rollback snapshot) goes ahead in stage 2. C-045 is recast as "PBS restore points through kennel" and parked with C-028. A Proxmox backup never holds RAM (checked in the qemu-server source; write-up https://claude.ai/artifact/MJo5wKpzFdKspSeMNDrL3S). S-003 carries: a per-VM capability check (snapshot, RAM, restore point, free space), which C-005 calls; `create(vm, label, include_ram)` returning an opaque handle; `rollback(handle, start)`; `delete`; asynchronous completion. | Dennis, on the card |
| 2026-11-14 | D-01: Apache-style individual and corporate CLAs, granted to Dennis "and his successors and assigns", with an explicit transfer clause and a promise that contributions stay available under the AGPL. CLA Assistant Lite checks every PR on unbork and unbork-playbooks, with signatures stored in a private repository in the organisation. Write-up: https://claude.ai/artifact/T7vmNSbLCWN54EF9EWJXFn. S-011 (texts, workflow and required check, CONTRIBUTING licensing section) is stage 0, class B. | Dennis, as relayed by the coordinator |
| 2026-11-14 | D-07: Dennis personally is the copyright holder. S-012 moves to stage 0, class A. | Dennis, as relayed by the coordinator |
| 2026-11-14 | D-02: Full rename with the old names as fallbacks, including on-host markers, alert labels, metrics and pack variables. Postgres database and role names stay; only new example defaults change. Host checks must recognise both names; the first sync removes old chains and files; test on hosts named "test" first. Map: `/mnt/project-files/rename-depth/rename-map.md` (project files). This accepts proposal PR-02 and unblocks C-001. | Dennis, as relayed from the D-02 thread |
| 2026-11-14 | D-03: The first unbork release is 2.0.0. The release PR (C-002) also needs an "Upgrading to 2.0.0" section in docs/upgrade.md, a reworded Versioning section, and a fix for docs/production-deploy.md, which pins an image tag that does not exist (unbork/unbork:1.0.0). | Dennis, as relayed from the D-03 thread |
| 2026-11-14 | D-05: Zabbix alert intake is Community: C-046, an adapter on the S-005 alert source seam, next to Grafana. | Dennis, as relayed by the D-05 thread |
| 2026-11-14 | D-13: propose_action hands off. It creates the ActionRun and ends the turn with a new `waiting_action` session status; the session resumes when the run finishes; `host_lock.py` is unchanged. Approval uses the existing `AIApprovalRequest` cards with frozen arguments. `allowed_action_keys` limits the tool at every level, and under full_auto a listed key runs without asking. The approver needs `ai.approve` and `actions.run` on the target. No AI snapshot. Write-up: https://claude.ai/artifact/AVhU8tSKwe6LeeQmfzE4AG. C-009 becomes class B. | Dennis, as relayed by the D-13 thread |
| 2026-11-14 | D-14: a new `ai.policy.manage` permission, admins only, checked at global scope, with a `PLUGIN_API` minor bump. Replaces the 2026-10-02 "any signed-in user" decision. Unblocks C-019. | Dennis, as relayed by the D-14 thread |
| 2026-11-14 | D-17: keep `planning/` on dev and amend the rule; `plans/` stays branch-scoped scratch. To amend in Phase 6: CONTRIBUTING.md (the `plans/` section and the release checklist line), CLAUDE.md, `.github/PULL_REQUEST_TEMPLATE/release.md`, and `.dockerignore` (add `planning/`). | Dennis, on the card in the D-17 thread |
| 2026-11-14 | D-18: customer-facing Enterprise feature titles appear in the public plan with an Enterprise tag and a no-commitment line; scores, specs, decisions and internal items stay private. | Dennis |
