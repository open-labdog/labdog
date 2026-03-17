# Draft: Service Live Control & Inventory

## Requirements (confirmed)
- **List all systemd services**: Full inventory via `systemctl list-units --type=service --all`, with client-side filtering
- **Ad-hoc commands**: Direct SSH `systemctl start/stop/restart` — not through Ansible pipeline
- **Protected services**: Allow with confirmation warning (not blocked)
- **Audit**: Log all ad-hoc commands to audit_log

## Technical Decisions
- SSH execution: reuse asyncssh pattern from `services/collector.py`
- No SyncJob created for ad-hoc commands — these are instant actions, not declarative state
- Filter is client-side (frontend) since full list is fetched once

## Open Questions
- None — all decisions resolved
