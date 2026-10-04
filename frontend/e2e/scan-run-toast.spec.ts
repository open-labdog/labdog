import { test, expect } from "./fixtures"

// "run now" on a scan schedule returns 202 straight away; the result only
// exists once the Celery task has committed. The page watches the schedule
// and announces the outcome in a toast. The API is mocked so no worker or
// network is needed.

const BASE = {
  ssh_key_id: 1,
  ssh_port: 22,
  default_group_ids: [],
  interval_minutes: 60,
  cron_expression: null,
  enabled: true,
  last_run_error: null,
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-01T00:00:00Z",
  pending_count: 0,
  cidrs: ["10.0.0.0/24"],
  last_run_at: "2026-10-04T09:00:00Z",
  last_run_status: "ok",
  last_run_hosts_added: 0,
  last_run_hosts_pending: 0,
}

type Scan = typeof BASE & { id: number; name: string; auto_add: boolean }

test.describe("Scan schedule run-now result toast", () => {
  test("announces hosts added, hosts queued, and a failure", async ({ page }) => {
    const scans: Record<number, Scan> = {
      9101: { ...BASE, id: 9101, name: "e2e-added", auto_add: true },
      9102: { ...BASE, id: 9102, name: "e2e-queued", auto_add: false },
      9103: { ...BASE, id: 9103, name: "e2e-broken", auto_add: true },
    }
    const finish: Record<number, (s: Scan) => void> = {
      9101: (s) => Object.assign(s, { last_run_at: "2026-10-04T11:00:00Z", last_run_hosts_added: 1 }),
      9102: (s) => Object.assign(s, { last_run_at: "2026-10-04T11:00:00Z", last_run_hosts_pending: 2 }),
      9103: (s) => Object.assign(s, { last_run_status: "error", last_run_error: "ssh key missing" }),
    }

    await page.route("**/api/scans**", async (route) => {
      const { pathname } = new URL(route.request().url())
      const json = (body: unknown, status = 200) =>
        route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) })
      if (pathname.endsWith("/pending-summary")) return json({ total: 0, by_scan: [] })
      if (pathname.endsWith("/pending")) return json([])
      const run = pathname.match(/\/api\/scans\/(\d+)\/run$/)
      if (run) {
        const id = Number(run[1])
        // The task finishes a moment after the 202, as it does for real.
        setTimeout(() => finish[id](scans[id]), 1500)
        return json({ queued: true }, 202)
      }
      const one = pathname.match(/\/api\/scans\/(\d+)$/)
      if (one) return json(scans[Number(one[1])])
      return json(Object.values(scans))
    })

    await page.goto("/discovery?tab=schedules")
    await expect(page.getByText("e2e-added", { exact: true })).toBeVisible()

    const cases: [string, string, string | null][] = [
      ["e2e-added", 'Scan "e2e-added" added 1 host', "View hosts"],
      ["e2e-queued", 'Scan "e2e-queued" found 2 hosts awaiting review', "Review"],
      ["e2e-broken", 'Scan "e2e-broken" failed: ssh key missing', null],
    ]
    for (const [name, message, action] of cases) {
      await page.getByRole("row").filter({ hasText: name }).getByRole("button", { name: "run now" }).click()
      const toast = page.locator("[data-sonner-toast]").filter({ hasText: message })
      await expect(toast).toBeVisible({ timeout: 15_000 })
      if (action) await expect(toast.getByRole("button", { name: action })).toBeVisible()
    }
  })
})
