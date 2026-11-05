import { test, expect } from "./fixtures"

test.describe("Overview page", () => {
  test("/ and /dashboard land on Overview", async ({ page }) => {
    await page.goto("/dashboard")
    await expect(page).toHaveURL(/\/overview\/?$/)
    await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible()
  })

  test("fleet status bar shows every status as a filter", async ({ page }) => {
    await page.goto("/overview")
    // One segment per status; each is a click-through into the hosts list.
    for (const label of ["in sync", "drifted", "syncing", "failed", "unknown"]) {
      await expect(page.getByRole("button", { name: new RegExp(`^\\d+ ${label} — filter the hosts list$`) })).toBeVisible()
    }
  })

  test("status segment filters the hosts list", async ({ page }) => {
    await page.goto("/overview")
    await page.getByRole("button", { name: /^\d+ in sync — filter the hosts list$/ }).click()
    await expect(page).toHaveURL(/\/hosts\/?\?status=in_sync/)
  })

  test("Drift-check fleet and Plan a sync are the page actions", async ({ page }) => {
    await page.goto("/overview")
    await expect(page.getByRole("button", { name: "Drift-check fleet" })).toBeVisible()
    await page.getByRole("button", { name: "Plan a sync" }).click()
    await expect(page).toHaveURL(/\/plans/)
  })

  test("pending view is reachable from the pane", async ({ page }) => {
    await page.goto("/overview")
    await page.getByRole("complementary").getByRole("link", { name: "Pending" }).click()
    await expect(page).toHaveURL(/\/overview\/?\?view=pending/)
    await expect(page.getByRole("heading", { name: "Pending" })).toBeVisible()
  })

  test("only a destructive action's schedule is tagged snap", async ({ page }) => {
    // BUG-98: every schedule stores snapshot_enabled, but the runner only
    // snapshots before a destructive action, so the tag promised a
    // rollback point the non-destructive one never gets.
    const schedule = (id: number, name: string, destructive: boolean) => ({
      id,
      target_kind: "fleet",
      target_id: null,
      action_key: `key-${id}`,
      parameters: {},
      schedule_cron: "0 4 * * 0",
      enabled: true,
      snapshot_enabled: true,
      verify_enabled: true,
      auto_rollback: true,
      batch_size: 1,
      last_dispatched_at: null,
      created_at: "2026-10-01T00:00:00Z",
      updated_at: "2026-10-01T00:00:00Z",
      target_name: null,
      action_name: name,
      pack_name: "labdog-playbooks",
      destructive,
      last_run: null,
    })
    await page.route(/\/api\/scheduled-actions\/?(\?.*)?$/, (r) =>
      r.request().method() === "GET"
        ? r.fulfill({
            json: [schedule(9101, "Upgrade Linux packages", true), schedule(9102, "Update Docker Compose images", false)],
          })
        : r.fallback(),
    )
    await page.goto("/overview?view=upcoming")
    // The table, not the timeline above it, which names each schedule too.
    const table = page.getByTestId("schedule-table")
    const row = (name: string) => table.getByText(name, { exact: true }).locator("xpath=../..")
    await expect(row("Upgrade Linux packages").getByText("snap", { exact: true })).toBeVisible()
    await expect(row("Update Docker Compose images")).toBeVisible()
    await expect(row("Update Docker Compose images").getByText("snap", { exact: true })).toHaveCount(0)
  })

  test("a schedule whose last run failed waits in Pending with the failing host's reason", async ({ page }) => {
    const now = Date.now()
    const iso = (msAgo: number) => new Date(now - msAgo).toISOString()
    const run = (id: number, status: string, msAgo: number) => ({ id, status, started_at: iso(msAgo), finished_at: iso(msAgo - 60_000), created_at: iso(msAgo) })
    await page.route(/\/api\/scheduled-actions\/?(\?.*)?$/, (r) =>
      r.request().method() === "GET"
        ? r.fulfill({
            json: [
              {
                id: 9201,
                target_kind: "fleet",
                target_id: null,
                action_key: "e2e-upgrade",
                parameters: {},
                schedule_cron: "0 4 * * *",
                enabled: true,
                snapshot_enabled: false,
                verify_enabled: true,
                auto_rollback: true,
                batch_size: 1,
                last_dispatched_at: null,
                created_at: iso(30 * 86_400_000),
                updated_at: iso(30 * 86_400_000),
                target_name: null,
                action_name: "E2E upgrade",
                pack_name: "e2e-pack",
                destructive: false,
                last_run: run(9302, "partial", 3_600_000),
                next_run_at: new Date(now + 3_600_000).toISOString(),
                upcoming_runs: [new Date(now + 3_600_000).toISOString()],
                recent_runs: [run(9302, "partial", 3_600_000), run(9301, "failed", 86_400_000), run(9300, "succeeded", 2 * 86_400_000)],
              },
            ],
          })
        : r.fallback(),
    )
    await page.route(/\/api\/actions\/runs\/?\?.*status=failed/, (r) =>
      r.fulfill({
        json: [
          {
            ...run(9302, "partial", 3_600_000),
            action_key: "e2e-upgrade",
            action_version: "1",
            host_id: null,
            group_id: null,
            target_kind: "fleet",
            target_label: "All hosts",
            scheduled_action_id: 9201,
            alert_event_id: null,
            parameters: {},
            parallelism: 1,
            snapshot_enabled: false,
            verify_enabled: true,
            auto_rollback: true,
            triggered_by_user_id: null,
            error_message: null,
            pending_reason: null,
            host_runs: [],
            host_counts: { succeeded: 17, failed: 1 },
            first_failure: { host_run_id: 1, host_id: null, hostname: "e2e-media", exit_code: 2, error_message: "ansible-runner exited with status=failed, rc=2", reason: "E: Could not get lock /var/lib/dpkg/lock-frontend" },
          },
        ],
      }),
    )
    await page.goto("/overview?view=pending&lane=failed")
    await expect(page.getByRole("button", { name: /failed runs\s*1/i })).toHaveAttribute("aria-pressed", "true")
    await expect(page.getByText("E2E upgrade failed on some hosts · e2e-media · 2 times in a row")).toBeVisible()
    await expect(page.getByText(/17 ok · 1 failed · e2e-media: E: Could not get lock/)).toBeVisible()
    await expect(page.getByRole("button", { name: "Open run" })).toBeVisible()
  })

  test("the pane highlights the view that is open", async ({ page }) => {
    // BUG-97: with trailingSlash the pathname is /overview/, and Summary
    // stayed highlighted whichever view was open.
    await page.goto("/overview")
    const pane = page.getByRole("complementary")
    const summary = pane.getByRole("link", { name: "Summary" })
    await expect(summary).toHaveAttribute("aria-current", "page")
    await pane.getByRole("link", { name: "Fleet state" }).click()
    await expect(page).toHaveURL(/\/overview\/?\?view=state/)
    await expect(pane.getByRole("link", { name: "Fleet state" })).toHaveAttribute("aria-current", "page")
    await expect(summary).not.toHaveAttribute("aria-current", "page")
  })

  test("rail carries the four zones and Settings", async ({ page }) => {
    await page.goto("/overview")
    const rail = page.getByRole("navigation", { name: "Zones" })
    for (const zone of ["Overview", "Fleet", "Operations", "Assistant", "Settings"]) {
      await expect(rail.getByRole("button", { name: zone, exact: true })).toBeVisible()
    }
    // Config is not a zone: a module's desired state is edited on its group's page.
    await expect(rail.getByRole("button", { name: "Config", exact: true })).toHaveCount(0)
  })

  test("rail expands to show zone names and remembers it", async ({ page }) => {
    await page.goto("/overview")
    const rail = page.getByRole("navigation", { name: "Zones" })
    // Collapsed: icons only, the names live in the buttons' labels.
    await expect(rail.getByText("Fleet", { exact: true })).toHaveCount(0)
    await rail.getByRole("button", { name: "Expand rail" }).click()
    for (const zone of ["Overview", "Fleet", "Operations", "Assistant", "Settings"]) {
      await expect(rail.getByText(zone, { exact: true })).toBeVisible()
    }
    await page.reload()
    await expect(rail.getByText("Fleet", { exact: true })).toBeVisible()
    // `]` collapses it again.
    await page.keyboard.press("]")
    await expect(rail.getByText("Fleet", { exact: true })).toHaveCount(0)
    await expect(rail.getByRole("button", { name: "Expand rail" })).toBeVisible()
  })

  test("Fleet zone opens the hosts list with Hosts, Groups and Discovery in the pane", async ({ page }) => {
    await page.goto("/overview")
    await page.getByRole("navigation", { name: "Zones" }).getByRole("button", { name: "Fleet", exact: true }).click()
    await expect(page).toHaveURL(/\/hosts\/?$/)
    const pane = page.getByRole("complementary")
    await expect(pane.getByRole("link", { name: /^Hosts/ })).toBeVisible()
    await expect(pane.getByRole("link", { name: /^Groups/ })).toBeVisible()
    await expect(pane.getByRole("link", { name: /^Discovery/ })).toBeVisible()
  })

  test("pane navigation to Groups works", async ({ page }) => {
    await page.goto("/hosts")
    await page.getByRole("complementary").getByRole("link", { name: /^Groups/ }).click()
    await expect(page).toHaveURL(/\/groups\/?$/)
    await expect(page.getByRole("heading", { name: "Groups" })).toBeVisible()
  })

  test("Operations zone pane reaches Audit", async ({ page }) => {
    await page.goto("/overview")
    await page.getByRole("navigation", { name: "Zones" }).getByRole("button", { name: "Operations", exact: true }).click()
    await page.getByRole("complementary").getByRole("link", { name: "Audit" }).click()
    await expect(page).toHaveURL(/\/audit/)
    await expect(page.getByRole("heading", { name: "Audit", exact: true })).toBeVisible()
  })

  test("Settings sits at the foot of the rail and opens the Integrations registry", async ({ page }) => {
    await page.goto("/overview")
    await page.getByRole("navigation", { name: "Zones" }).getByRole("button", { name: "Settings", exact: true }).click()
    await expect(page).toHaveURL(/\/settings/)
    await expect(page.getByRole("tab", { name: "Integrations", selected: true })).toBeVisible()
  })

  test("account menu carries log out", async ({ page }) => {
    await page.goto("/overview")
    await page.getByRole("button", { name: "Account menu" }).click()
    await expect(page.getByRole("menuitem", { name: "Log out" })).toBeVisible()
    await expect(page.getByRole("menuitem", { name: "Change password…" })).toBeVisible()
  })
})
