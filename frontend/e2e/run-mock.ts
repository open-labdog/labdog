import type { Page } from "@playwright/test"

/** Serve one finished action run, with `log` as its host's output, so the
 *  run page renders without a worker. */
export async function mockRun(page: Page, { id = 9301, log, status = "succeeded" }: { id?: number; log: string; status?: string }) {
  const hostRun = {
    id: id * 10,
    action_run_id: id,
    host_id: 8101,
    hostname: "web1",
    status,
    started_at: "2026-10-08T02:00:56Z",
    finished_at: "2026-10-08T02:04:10Z",
    exit_code: status === "succeeded" ? 0 : 2,
    error_message: null,
    snapshot_name: null,
    pending_reason: null,
  }
  const run = {
    id,
    action_key: "linux-upgrade",
    action_version: "1.0",
    host_id: 8101,
    group_id: null,
    target_kind: "host",
    target_label: "web1",
    scheduled_action_id: null,
    parameters: {},
    parallelism: 1,
    snapshot_enabled: true,
    verify_enabled: true,
    auto_rollback: true,
    status,
    triggered_by_user_id: null,
    started_at: hostRun.started_at,
    finished_at: hostRun.finished_at,
    error_message: null,
    pending_reason: null,
    created_at: hostRun.started_at,
    host_runs: [hostRun],
  }
  await page.route(new RegExp(`/api/actions/runs/${id}/?$`), (r) => r.fulfill({ json: run }))
  await page.route(new RegExp(`/api/actions/runs/${id}/host-runs/${hostRun.id}/output`), (r) =>
    r.fulfill({ body: log, contentType: "text/plain" }),
  )
  return `/actions/runs/${id}`
}
