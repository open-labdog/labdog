import type { Page } from "@playwright/test"

/** Serve one finished action run, with `log` as every host's output, so the
 *  run page renders without a worker. One host is a host-targeted run named
 *  web1; more make it a group run of host-01, host-02, … */
export async function mockRun(
  page: Page,
  { id = 9301, log, status = "succeeded", hosts = 1 }: { id?: number; log: string; status?: string; hosts?: number },
) {
  const hostRuns = Array.from({ length: hosts }, (_, i) => ({
    id: id * 100 + i,
    action_run_id: id,
    host_id: 8101 + i,
    hostname: hosts === 1 ? "web1" : `host-${String(i + 1).padStart(2, "0")}`,
    status,
    started_at: "2026-10-08T02:00:56Z",
    finished_at: "2026-10-08T02:04:10Z",
    exit_code: status === "succeeded" ? 0 : 2,
    error_message: null,
    snapshot_name: null,
    pending_reason: null,
  }))
  const run = {
    id,
    action_key: "linux-upgrade",
    action_version: "1.0",
    host_id: hosts === 1 ? 8101 : null,
    group_id: hosts === 1 ? null : 7101,
    target_kind: hosts === 1 ? "host" : "group",
    target_label: hosts === 1 ? "web1" : "group: web",
    scheduled_action_id: null,
    parameters: {},
    parallelism: 1,
    snapshot_enabled: true,
    verify_enabled: true,
    auto_rollback: true,
    status,
    triggered_by_user_id: null,
    started_at: "2026-10-08T02:00:56Z",
    finished_at: "2026-10-08T02:04:10Z",
    error_message: null,
    pending_reason: null,
    created_at: "2026-10-08T02:00:56Z",
    host_runs: hostRuns,
  }
  await page.route(new RegExp(`/api/actions/runs/${id}/?$`), (r) => r.fulfill({ json: run }))
  await page.route(new RegExp(`/api/actions/runs/${id}/host-runs/\\d+/output`), (r) => r.fulfill({ body: log, contentType: "text/plain" }))
  return `/actions/runs/${id}`
}
