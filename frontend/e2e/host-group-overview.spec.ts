import { test, expect, API_BASE } from "./fixtures"

/**
 * The host and group Overview tabs. Runs need a worker, so a host's run
 * history and the schedules are served from routes; the host, the group and
 * the membership are real.
 */

const iso = (msAgo: number) => new Date(Date.now() - msAgo).toISOString()

function schedule(id: number, groupId: number) {
  return {
    id,
    target_kind: "group",
    target_id: groupId,
    action_key: "e2e-upgrade",
    parameters: {},
    schedule_cron: "0 4 * * *",
    enabled: true,
    snapshot_enabled: true,
    verify_enabled: true,
    auto_rollback: true,
    batch_size: 1,
    last_dispatched_at: null,
    created_at: iso(30 * 86_400_000),
    updated_at: iso(30 * 86_400_000),
    target_name: null,
    action_name: "E2E upgrade",
    pack_name: "e2e-pack",
    destructive: true,
    last_run: null,
    next_run_at: new Date(Date.now() + 3_600_000).toISOString(),
    upcoming_runs: [],
    recent_runs: [],
  }
}

function hostRun(runId: number, scheduleId: number, status: string, msAgo: number, reason: string | null = null) {
  return {
    host_run_id: runId * 10,
    action_run_id: runId,
    action_key: "e2e-upgrade",
    run_status: status === "failed" ? "partial" : status,
    status,
    target_kind: "group",
    target_label: "group: e2e",
    scheduled_action_id: scheduleId,
    alert_event_id: null,
    triggered_by_user_id: null,
    created_at: iso(msAgo),
    started_at: iso(msAgo),
    finished_at: iso(msAgo - 60_000),
    exit_code: status === "failed" ? 2 : 0,
    error_message: status === "failed" ? "ansible-runner exited with status=failed, rc=2" : null,
    reason,
    rolled_back: false,
  }
}

test.describe("Host and group Overview", () => {
  test("a group schedule failing on this host leads the host's Overview", async ({ request, page }) => {
    const stamp = Date.now()
    const group = await (await request.post(`${API_BASE}/api/groups`, { data: { name: `e2e-ov-group-${stamp}`, description: null, priority: 31 } })).json()
    const host = await (
      await request.post(`${API_BASE}/api/hosts`, { data: { hostname: `e2e-ov-host-${stamp}`, ip_address: "10.0.0.31", ssh_port: 22, group_ids: [group.id] } })
    ).json()

    await page.route(/\/api\/scheduled-actions\/?(\?.*)?$/, (r) => (r.request().method() === "GET" ? r.fulfill({ json: [schedule(9401, group.id)] }) : r.fallback()))
    await page.route(/\/api\/actions\/host-runs\/?\?/, (r) =>
      r.fulfill({
        json: [
          hostRun(9503, 9401, "failed", 3_600_000, "E: Could not get lock /var/lib/dpkg/lock-frontend"),
          hostRun(9502, 9401, "failed", 86_400_000, "E: Could not get lock /var/lib/dpkg/lock-frontend"),
          hostRun(9501, 9401, "succeeded", 2 * 86_400_000),
        ],
      }),
    )

    await page.goto(`/hosts/${host.id}`)
    // The banner names the schedule, how many in a row, and this host's reason.
    const banner = page.getByTestId("host-failed-runs")
    await expect(banner).toContainText("E2E upgrade failed on this host in its last 2 scheduled runs.")
    await expect(banner).toContainText("E: Could not get lock")
    await expect(banner.getByRole("link", { name: "Open run" })).toHaveAttribute("href", new RegExp(`^/hosts/${host.id}/actions/runs/9503/?$`))

    // The schedule reaches the host through its group.
    const schedules = page.getByTestId("host-schedules")
    await expect(schedules.getByText("E2E upgrade", { exact: true })).toBeVisible()
    await expect(schedules.getByRole("link", { name: group.name })).toHaveAttribute("href", new RegExp(`^/groups/${group.id}/?$`))
    await expect(schedules.getByLabel("last 3 runs on this host")).toBeVisible()

    // Drift is one line in the host card; the full facts are a click away.
    await expect(page.getByTestId("host-config-line")).toContainText("not checked yet")
    await page.getByRole("button", { name: "all facts ▾" }).click()
    await expect(page.getByText("drift monitoring")).toBeVisible()

    // Where its config comes from lists the group as a layer.
    await expect(page.getByTestId("config-layers").getByRole("link", { name: group.name })).toBeVisible()
  })

  test("a group's Overview shows its members' health and folds the settings into one bar", async ({ request, page }) => {
    const stamp = Date.now()
    const group = await (await request.post(`${API_BASE}/api/groups`, { data: { name: `e2e-ov-g-${stamp}`, description: null, priority: 32 } })).json()
    const hostname = `e2e-ov-member-${stamp}`
    await request.post(`${API_BASE}/api/hosts`, { data: { hostname, ip_address: "10.0.0.32", ssh_port: 22, group_ids: [group.id] } })

    // No schedule reaches this group; fleet-wide ones from other specs would.
    await page.route(/\/api\/scheduled-actions\/?(\?.*)?$/, (r) => (r.request().method() === "GET" ? r.fulfill({ json: [] }) : r.fallback()))
    await page.goto(`/groups/${group.id}`)
    // A member never checked is a problem row, so it shows without "show all".
    await expect(page.getByTestId("health-matrix").getByText(hostname)).toBeVisible()
    await expect(page.getByTestId("group-numbers")).toContainText("members1")
    await expect(page.getByTestId("group-runs")).toContainText("Nothing is scheduled on this group")

    // Settings, GitOps and delete are folded into one bar.
    const bar = page.getByTestId("group-settings-row")
    await expect(bar).toContainText(`name${group.name}`)
    await expect(page.getByRole("button", { name: "Save changes" })).toHaveCount(0)
    await bar.getByRole("button", { name: "edit settings" }).click()
    await expect(page.getByRole("button", { name: "Save changes" })).toBeVisible()

    // Delete is rarer still: the danger zone inside it starts folded too.
    await expect(page.getByRole("button", { name: "Delete group" })).toHaveCount(0)
    await bar.getByRole("button", { name: "expand" }).click()
    await expect(page.getByRole("button", { name: "Delete group" })).toBeVisible()
  })
})
