# Decisions — ext-cron-jobs

## 2026-03-18 Session start
- Migration: `0007_cron_jobs.py`, down_revision="0006"
- Use new `cronstate` enum (not reuse userstate) — different name to avoid SAEnum conflicts
- `module_type = "cron"` string for all SyncJob and host_module_status entries
