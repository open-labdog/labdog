# Phase 2 — refactoring and readability (branch scratchpad, delete before PR)

Scope approved by Dennis 2026-11-08: Tiers A + B, frontend C1–C3. C4–C7 and the
60–100 line functions go to TODO.md for a later pass.

Findings doc: https://claude.ai/artifact/WAov6T8fJz7hUHH1C81Wz6

## Rules
- Move, don't rewrite. Same statements, same order, same SQL/SSH/Celery calls,
  same log text, same commit points, same lock scope.
- BUG-/SEC- comments move with the line they explain.
- No existing test is edited. Characterisation tests first (own commit) where a
  target is under ~25% covered.
- One commit per item: `refactor(<scope>): ...`.

## Items
- [ ] A1  tasks/action_group.py  _run_action_group_async (+ _snapshot_all, _verify_all, _aggregate_and_finalise, _rollback_all)
- [ ] A2  metrics/collector.py collect
- [ ] A3  api/_repo_scan.py activate_repo
- [ ] A4  main.py create_app
- [ ] A5  tasks/scan_run.py _async_run
- [ ] A6  api/host_state.py _detect_firewall_backend
- [ ] A7  api/ssh_terminal.py ssh_terminal_ws
- [ ] A8  workflows/steps/verify.py run_verification
- [ ] A9  tasks/host_lock.py dispatch_next_pending_for_host
- [ ] A10 tasks/action_orchestrator.py _run_action_async
- [ ] A11 tasks/host_sync_orchestrator.py _async_run
- [ ] A12 tasks/gitops.py _process_webhook_async
- [ ] A13 ai/loop.py AgentLoop.run, _run_tool
- [ ] A14 ai/providers stream_turn (both)
- [ ] A15 api/actions.py create_run
- [ ] A16 sync/parsers/nftables.py parse_nftables_json
- [ ] A17 api/host_state.py _build_collectors, collect_module_state
- [ ] A18 ai/tools/ssh.py _run_ssh_command, ai/subject_investigation.py _prepare_host
- [ ] A19 tasks/scheduled_action_schedule.py _check_due_async
- [ ] B1  scoped rule CRUD helpers (8 modules)
- [ ] B2  drift-check helpers (5 modules)
- [ ] B3  group sync trigger helper + hosts-entry dict
- [ ] B4  gitops importer helpers
- [ ] B5  ResolverYAML subclasses ResolverConfigCreate
- [ ] B6  shared Create/Update validators
- [ ] C1  PlanPage
- [ ] C2  ActionPacksPage
- [ ] C3  AssistantPage
- [ ] TODO.md: deferred C4–C7 + medium functions
