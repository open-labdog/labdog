import { test, expect } from "./fixtures"
import { mockRun } from "./run-mock"

// "Investigate with AI" on a finished run starts a read-only assistant
// session about it — or about the host picked in the log — and opens the
// assistant on it. Every API the page calls is mocked, so no provider or
// worker is needed.

const LOG = "[preflight] host reachable\nTASK [t] ***\nfatal: [web1]: FAILED! => {\n}"

const PROVIDER = {
  id: 1,
  name: "Claude",
  provider_type: "claude_agent",
  enabled: true,
  is_default: true,
  supports_tools: true,
  sends_data_offsite: true,
}

test.describe("Investigate a run with AI", () => {
  test("starts a session about the run and opens it", async ({ page }) => {
    await page.route(/\/api\/ai\/providers\/?$/, (r) => r.fulfill({ json: [PROVIDER] }))
    let body: unknown = null
    await page.route(/\/api\/actions\/runs\/9301\/investigate$/, (r) => {
      body = r.request().postDataJSON()
      return r.fulfill({ status: 201, json: { session_id: 77, created: true } })
    })
    await page.goto(await mockRun(page, { log: LOG, status: "failed" }))

    await page.getByTestId("investigate-run").click()
    const dialog = page.getByRole("dialog")
    await expect(dialog).toContainText("Investigate this run with AI?")
    await expect(dialog).toContainText("The log goes to Claude, off your network.")
    await dialog.getByRole("button", { name: "Start investigation" }).click()

    await expect(page).toHaveURL(/\/assistant\/?\?session=77$/)
    expect(body).toEqual({})
  })

  test("one host of a group run", async ({ page }) => {
    await page.route(/\/api\/ai\/providers\/?$/, (r) => r.fulfill({ json: [PROVIDER] }))
    let body: unknown = null
    await page.route(/\/api\/actions\/runs\/9301\/investigate$/, (r) => {
      body = r.request().postDataJSON()
      return r.fulfill({ status: 201, json: { session_id: 78, created: true } })
    })
    await page.goto(await mockRun(page, { log: LOG, status: "failed", hosts: 2 }))

    await page.getByText("host-02", { exact: true }).click()
    await page.getByRole("button", { name: "Investigate host-02 with AI" }).click()
    await expect(page.getByRole("dialog")).toContainText("read-only assistant session on host-02")
    await page.getByRole("button", { name: "Start investigation" }).click()

    await expect(page).toHaveURL(/session=78$/)
    expect(body).toEqual({ host_run_id: 930101 })
  })

  test("opens the investigation already started", async ({ page }) => {
    await page.route(/\/api\/ai\/providers\/?$/, (r) => r.fulfill({ json: [PROVIDER] }))
    await page.goto(
      await mockRun(page, {
        log: LOG,
        status: "failed",
        investigations: [{ session_id: 55, status: "succeeded", target_host_ids: [8101], created_at: "2026-10-08T03:00:00Z" }],
      }),
    )

    await expect(page.getByTestId("run-investigation")).toHaveText("session #55")
    await page.getByRole("button", { name: "View investigation →" }).click()
    await expect(page).toHaveURL(/session=55$/)
  })

  test("no button without an AI provider", async ({ page }) => {
    await page.route(/\/api\/ai\/providers\/?$/, (r) => r.fulfill({ json: [] }))
    await page.goto(await mockRun(page, { log: LOG, status: "failed" }))
    await expect(page.getByTestId("run-log")).toContainText("fatal: [web1]")
    await expect(page.getByTestId("investigate-run")).toHaveCount(0)
  })
})
