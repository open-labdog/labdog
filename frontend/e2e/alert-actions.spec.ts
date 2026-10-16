import { test, expect, API_BASE } from "./fixtures"
import { mockRun } from "./run-mock"

// Grafana labels choose what an alert does. Alerts › Handling holds the
// actions an alert's labdog_action label may run; Received shows what each
// alert's action came to; the run it started says which alert started it.

const ALERT = {
  id: 501,
  source: "grafana_webhook",
  fingerprint: "fp",
  alertname: "NginxDown",
  severity: "critical",
  status: "resolved",
  labels: { alertname: "NginxDown", labdog_action: "restart-nginx" },
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

const dispatch = (over: Record<string, unknown>) => ({
  id: 1,
  alert_action_id: 7,
  action_key: "restart-nginx",
  action_name: "Restart nginx",
  host_id: 8101,
  action_run_id: null,
  run_status: null,
  outcome: "started",
  detail: null,
  check_outcome: null,
  checked_at: null,
  ...over,
})

test("Alerts is under Operations, with Received and Handling", async ({ page }) => {
  await page.goto("/alerts")
  await expect(page.getByRole("tab", { name: /Received/ })).toHaveAttribute("aria-selected", "true")
  await page.getByRole("tab", { name: /Handling/ }).click()
  await expect(page).toHaveURL(/\/alerts\/?\?tab=handling$/)
  await expect(page.getByTestId("alert-labels")).toContainText("labdog_ai_automate=true")
  await expect(page.getByTestId("alert-intake")).toContainText("/api/webhooks/grafana-alerts")
  await expect(page.getByText(/No action is allowed yet/)).toBeVisible()
})

test("an alert's row shows what its action came to", async ({ page }) => {
  await page.route(/\/api\/alerts(\?.*)?$/, (r) =>
    r.fulfill({
      json: [
        { ...ALERT, actions: [dispatch({ action_run_id: 9301, run_status: "succeeded", check_outcome: "fixed", checked_at: "2026-10-14T13:34:56Z" })] },
        {
          ...ALERT,
          id: 502,
          alertname: "DiskFull",
          investigation_outcome: "skipped_no_label",
          investigation_detail: "not labelled labdog_ai=true or labdog_ai_automate=true",
          actions: [dispatch({ id: 2, alert_action_id: null, action_key: "linux-upgrade", action_name: null, outcome: "not_allowed", detail: "linux-upgrade is not on the list of actions alerts may run" })],
        },
      ],
    }),
  )
  await page.goto("/alerts")
  const cells = page.getByTestId("alert-actions")
  await expect(cells.first().getByRole("link", { name: "Restart nginx #9301" })).toHaveAttribute("href", /\/actions\/runs\/9301\/?$/)
  await expect(cells.first().getByText("fixed", { exact: true })).toBeVisible()
  await expect(cells.nth(1).getByText("not allowed", { exact: true })).toHaveAttribute("title", "linux-upgrade is not on the list of actions alerts may run")
  await expect(page.getByText("handled by action")).toBeVisible()
  await expect(page.getByText("not labelled for the AI")).toBeVisible()
})

test("a run started by an alert links to its row", async ({ page }) => {
  await page.goto(await mockRun(page, { log: "ok: [web1]", alert: { id: 501, name: "NginxDown" } }))
  await expect(page.getByTestId("run-alert")).toHaveText("NginxDown")
  await expect(page.getByTestId("run-alert")).toHaveAttribute("href", /\/alerts\/?\?alert=501$/)
  await expect(page.getByText("triggered by alert")).toBeVisible()
})

test("allow an action, change it, and take it off the list", async ({ request, page }) => {
  const hostRes = await request.post(`${API_BASE}/api/hosts`, {
    data: { hostname: `e2e-alert-host-${Date.now()}`, ip_address: "10.0.0.1", ssh_port: 22, group_ids: [] },
  })
  const host = (await hostRes.json()) as { id: number; hostname: string }

  await page.goto("/alerts?tab=handling")
  await page.getByTestId("alert-action-new").click()
  const key = "_builtin.collect_state"
  await page.getByTestId("alert-action-picker").selectOption(key)
  await expect(page.getByText(`labdog_action=${key}`)).toBeVisible()
  await page.getByTestId("alert-action-where-host").check()
  await page.getByTestId("alert-action-host").fill(host.hostname)
  await page.getByRole("option", { name: new RegExp(`^${host.hostname}`) }).first().click()
  await page.getByLabel("cooldown (minutes)").fill("15")
  await page.getByTestId("alert-action-submit").click()

  const row = page.locator(`[data-testid=alert-action-row][data-action-key="${key}"]`)
  await expect(row).toBeVisible()
  const listed = page.getByRole("row").filter({ has: row })
  await expect(listed).toContainText(`host: ${host.hostname}`)
  await expect(listed).toContainText("15 min")
  await expect(listed).toContainText("enabled")
  await expect(page.getByRole("tab", { name: /Handling/ })).toContainText("1")

  // The host's Overview lists it, AI or not.
  await page.goto(`/hosts/${host.id}`)
  await expect(page.getByTestId("host-alert-actions")).toContainText(key)
  await expect(page.getByTestId("host-alert-actions")).toContainText(`host ${host.hostname}`)

  // Pause it, then take it off.
  await page.goto("/alerts?tab=handling")
  await row.getByRole("button", { name: "edit" }).click()
  await expect(page.getByTestId("alert-action-picker")).toBeDisabled()
  await page.getByText(/^Enabled/).click()
  await page.getByTestId("alert-action-submit").click()
  await expect(listed).toContainText("paused")
  await row.getByRole("button", { name: "remove" }).click()
  await page.getByRole("button", { name: "Remove", exact: true }).click()
  await expect(row).toHaveCount(0)
  await request.delete(`${API_BASE}/api/hosts/${host.id}`)
})

test("the host page says when full auto is paused on it", async ({ request, page }) => {
  const hostRes = await request.post(`${API_BASE}/api/hosts`, {
    data: { hostname: `e2e-paused-host-${Date.now()}`, ip_address: "10.0.0.1", ssh_port: 22, group_ids: [] },
  })
  const host = (await hostRes.json()) as { id: number }
  await page.route(/\/api\/alerts\/hosts\/\d+\/?$/, (r) =>
    r.fulfill({
      json: {
        ai_enabled: true,
        auto_investigate_enabled: true,
        rules_enabled: false,
        rules: ["labdog_ai_automate=true"],
        blockers: ["an automatic fix made this host worse, so full auto is off it until 2026-10-16 16:13 UTC"],
        alert_actions: [],
      },
    }),
  )
  await page.goto(`/hosts/${host.id}`)
  await expect(page.getByTestId("host-alert-blockers")).toContainText("full auto is off it until 2026-10-16 16:13 UTC")
  await request.delete(`${API_BASE}/api/hosts/${host.id}`)
})
