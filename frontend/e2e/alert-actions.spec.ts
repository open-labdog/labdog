import { test, expect, API_BASE } from "./fixtures"
import { mockRun } from "./run-mock"

// What an alert-triggered schedule did shows on the alert's row, and the
// run it started says which alert started it.

const ALERT = {
  id: 501,
  source: "grafana_webhook",
  fingerprint: "fp",
  alertname: "NginxDown",
  severity: "critical",
  status: "resolved",
  labels: { alertname: "NginxDown" },
  annotations: { summary: "nginx is down" },
  starts_at: "2026-10-14T13:23:13Z",
  ends_at: null,
  dedup_count: 2,
  host_id: 8101,
  investigation_session_id: null,
  investigation_outcome: "handled_by_action",
  investigation_detail: "fixed by restart-nginx run #9301",
  investigation_status: null,
  investigation_summary: null,
  investigation_autonomy: null,
  investigation_autonomy_note: null,
  remediation_outcome: null,
  remediation_detail: null,
  remediation_checked_at: null,
  rollback_status: null,
  rollback_detail: null,
  created_at: "2026-10-14T13:23:13Z",
}

test("an alert's row shows the action it ran and how that went", async ({ page }) => {
  await page.route(/\/api\/ai\/alerts(\?.*)?$/, (r) =>
    r.fulfill({
      json: [
        {
          ...ALERT,
          actions: [
            { id: 1, scheduled_action_id: 9100, action_key: "restart-nginx", action_name: "Restart nginx", host_id: 8101, action_run_id: 9301, run_status: "succeeded", outcome: "started", detail: null, check_outcome: "fixed", checked_at: "2026-10-14T13:34:56Z" },
            { id: 2, scheduled_action_id: 9101, action_key: "linux-upgrade", action_name: null, host_id: 8101, action_run_id: null, run_status: null, outcome: "skipped_cooldown", detail: "run #9200 was 12 min ago (cooldown 60 min)", check_outcome: null, checked_at: null },
          ],
        },
      ],
    }),
  )
  await page.goto("/alerts")
  const cell = page.getByTestId("alert-actions")
  await expect(cell.getByRole("link", { name: "Restart nginx #9301" })).toHaveAttribute("href", /\/actions\/runs\/9301\/?$/)
  await expect(cell.getByText("fixed", { exact: true })).toBeVisible()
  await expect(cell.getByText("cooldown", { exact: true })).toHaveAttribute("title", "run #9200 was 12 min ago (cooldown 60 min)")
  await expect(page.getByText("handled by action")).toBeVisible()
})

test("a run started by an alert says so", async ({ page }) => {
  await page.goto(await mockRun(page, { log: "ok: [web1]", alert: { id: 501, name: "NginxDown" } }))
  await expect(page.getByTestId("run-alert")).toHaveText("NginxDown")
  await expect(page.getByText("triggered by alert")).toBeVisible()
})

test("a host's Overview lists the alert triggers that cover it, AI or not", async ({ request, page }) => {
  const hostRes = await request.post(`${API_BASE}/api/hosts`, {
    data: { hostname: `e2e-alert-host-${Date.now()}`, ip_address: "10.0.0.1", ssh_port: 22, group_ids: [] },
  })
  const host = (await hostRes.json()) as { id: number }
  const created = await request.post(`${API_BASE}/api/scheduled-actions`, {
    data: {
      target_kind: "host",
      target_id: host.id,
      action_key: "_builtin.collect_state",
      trigger: "alert",
      alert_match: "NginxDown\nlabdog_action=collect",
      enabled: true,
    },
  })
  expect(created.status(), await created.text()).toBe(201)
  const schedule = (await created.json()) as { id: number }
  try {
    await page.goto(`/hosts/${host.id}`)
    const panel = page.getByTestId("host-alert-actions")
    await expect(panel).toContainText("Alerts on this host run these actions")
    await expect(panel).toContainText("NginxDown · labdog_action=collect")
  } finally {
    await request.delete(`${API_BASE}/api/scheduled-actions/${schedule.id}`)
  }
})
