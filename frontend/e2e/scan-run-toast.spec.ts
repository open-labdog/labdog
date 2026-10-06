import { test, expect } from "./fixtures"

// "run now" on a scan schedule returns 202 and a task id straight away; the
// result only exists once the Celery task has finished. The page polls that
// task and announces the outcome in a toast. The API is mocked so no worker
// or network is needed.

const BASE = {
  ssh_key_id: 1,
  ssh_port: 22,
  default_group_ids: [],
  interval_minutes: 60,
  cron_expression: null,
  enabled: true,
  last_run_error: null as string | null,
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

const PENDING = { status: "pending", hosts_added: 0, hosts_pending: 0, error: null }

test.describe("Scan schedule run-now result toast", () => {
  test("announces each run's own outcome", async ({ page }) => {
    const scans: Record<number, Scan> = {
      9101: { ...BASE, id: 9101, name: "e2e-added", auto_add: true },
      9102: { ...BASE, id: 9102, name: "e2e-queued", auto_add: false },
      9103: { ...BASE, id: 9103, name: "e2e-both", auto_add: true },
      // Fails exactly as its previous run did, so its last-run fields don't
      // change; only the task result says this run is over.
      9104: { ...BASE, id: 9104, name: "e2e-broken", auto_add: true, last_run_status: "error", last_run_error: "ssh key missing" },
      9105: { ...BASE, id: 9105, name: "e2e-disabled", auto_add: true, enabled: false },
    }
    const outcome: Record<number, object> = {
      9101: { ...PENDING, status: "done", hosts_added: 1 },
      9102: { ...PENDING, status: "done", hosts_pending: 2 },
      9103: { ...PENDING, status: "done", hosts_added: 3, hosts_pending: 2 },
      9104: { ...PENDING, status: "error", error: "ssh key missing" },
    }
    const finishedAt: Record<string, number> = {}

    await page.route("**/api/scans**", async (route) => {
      const { pathname } = new URL(route.request().url())
      const json = (body: unknown, status = 200) =>
        route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) })
      if (pathname.endsWith("/pending-summary")) return json({ total: 0, by_scan: [] })
      if (pathname.endsWith("/pending")) return json([])
      const run = pathname.match(/\/api\/scans\/(\d+)\/run$/)
      if (run) {
        const taskId = `task-${run[1]}`
        // The task finishes a moment after the 202, as it does for real.
        finishedAt[taskId] = Date.now() + 1500
        return json({ queued: true, task_id: taskId }, 202)
      }
      const status = pathname.match(/\/api\/scans\/(\d+)\/runs\/([\w-]+)$/)
      if (status) {
        const done = Date.now() >= (finishedAt[status[2]] ?? Infinity)
        return json(done ? outcome[Number(status[1])] : PENDING)
      }
      const one = pathname.match(/\/api\/scans\/(\d+)$/)
      if (one) return json(scans[Number(one[1])])
      return json(Object.values(scans))
    })

    await page.goto("/discovery?tab=schedules")
    await expect(page.getByText("e2e-added", { exact: true })).toBeVisible()

    const runNow = (name: string) => page.getByRole("row").filter({ hasText: name }).getByRole("button", { name: "run now" })
    await expect(runNow("e2e-disabled")).toBeDisabled()

    const cases: [string, string, string[]][] = [
      ["e2e-added", 'Scan "e2e-added" added 1 host', ["View hosts"]],
      ["e2e-queued", 'Scan "e2e-queued" found 2 hosts awaiting review', ["Review"]],
      ["e2e-both", 'Scan "e2e-both" added 3 hosts; 2 awaiting review', ["View hosts", "Review"]],
      ["e2e-broken", 'Scan "e2e-broken" failed: ssh key missing', []],
    ]
    for (const [name, message, actions] of cases) {
      await runNow(name).click()
      const toast = page.locator("[data-sonner-toast]").filter({ hasText: message })
      await expect(toast).toBeVisible({ timeout: 15_000 })
      for (const action of actions) await expect(toast.getByRole("button", { name: action })).toBeVisible()
    }
  })

  test("says so when a run is still going after two minutes", async ({ page }) => {
    await page.clock.install()
    await page.route("**/api/scans**", async (route) => {
      const { pathname } = new URL(route.request().url())
      const json = (body: unknown, status = 200) =>
        route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) })
      if (pathname.endsWith("/pending-summary")) return json({ total: 0, by_scan: [] })
      if (pathname.endsWith("/run")) return json({ queued: true, task_id: "task-slow" }, 202)
      if (pathname.includes("/runs/")) return json(PENDING)
      return json([{ ...BASE, id: 9201, name: "e2e-slow", auto_add: true }])
    })

    await page.goto("/discovery?tab=schedules")
    await page.getByRole("row").filter({ hasText: "e2e-slow" }).getByRole("button", { name: "run now" }).click()
    await expect(page.getByText('Run triggered for "e2e-slow"')).toBeVisible()
    await page.clock.fastForward("02:01")
    await expect(page.getByText(`Scan "e2e-slow" is still running; you'll get a toast when it finishes`)).toBeVisible()
  })
})
