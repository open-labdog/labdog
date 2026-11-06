import { test, expect } from "./fixtures"
import type { Page } from "@playwright/test"

// "Investigate with AI" outside the run page: the button sends the
// subject's ids only, then opens the assistant on the session the API
// started or reopened. The AI, metrics and stuck-operation APIs are
// mocked, so no provider, Mimir or worker is needed.

const PROVIDER = {
  id: 1,
  name: "Claude",
  provider_type: "claude_agent",
  enabled: true,
  is_default: true,
  supports_tools: true,
  sends_data_offsite: true,
}

const FLEET = {
  configured: true,
  unconfigured_reason: null,
  sampled_at: "2026-11-06T17:00:00Z",
  error: null,
  hosts: [
    { host_id: 7, hostname: "gitlab", cpu: 10, memory: 30, disk: 87 },
    { host_id: 8, hostname: "k8s-1", cpu: 12, memory: 35, disk: 81 },
  ],
}

async function mockAI(page: Page, { providers = [PROVIDER], earlier = [] as unknown[] } = {}) {
  await page.route(/\/api\/ai\/providers\/?$/, (r) => r.fulfill({ json: providers }))
  const posted: unknown[] = []
  await page.route(/\/api\/ai\/investigations\/?$/, (r) => {
    if (r.request().method() === "POST") {
      posted.push(r.request().postDataJSON())
      return r.fulfill({ status: 201, json: { session_id: 81, created: true } })
    }
    return r.fulfill({ json: earlier })
  })
  return posted
}

test.describe("Investigate with AI from the overview", () => {
  test("a resource hot spot", async ({ page }) => {
    const posted = await mockAI(page)
    await page.route(/\/api\/grafana\/fleet\/metrics$/, (r) => r.fulfill({ json: FLEET }))
    await page.goto("/overview?view=state")

    const first = page.getByTestId("hot-spot").first()
    await expect(first).toContainText("gitlab · disk /")
    await first.getByTestId("investigate").click()

    const dialog = page.getByRole("dialog")
    await expect(dialog).toContainText("Investigate gitlab's root disk with AI?")
    await expect(dialog).toContainText("It goes to Claude, off your network")
    await dialog.getByRole("button", { name: "Start investigation" }).click()

    await expect(page).toHaveURL(/\/assistant\/?\?session=81$/)
    expect(posted).toEqual([{ subject: { kind: "resource", host_id: 7, metric: "disk" } }])
  })

  test("reopens the investigation already started", async ({ page }) => {
    const posted = await mockAI(page, {
      earlier: [{ subject_key: "resource:7:disk", session_id: 55, status: "succeeded", created_at: new Date().toISOString() }],
    })
    await page.route(/\/api\/grafana\/fleet\/metrics$/, (r) => r.fulfill({ json: FLEET }))
    await page.goto("/overview?view=state")

    const spots = page.getByTestId("hot-spot")
    await expect(spots.nth(1).getByTestId("investigate")).toBeVisible()
    await spots.first().getByTestId("investigate-open").click()
    await expect(page).toHaveURL(/session=55$/)
    expect(posted).toEqual([])
  })

  test("no button without an AI provider", async ({ page }) => {
    await mockAI(page, { providers: [] })
    await page.route(/\/api\/grafana\/fleet\/metrics$/, (r) => r.fulfill({ json: FLEET }))
    await page.goto("/overview?view=state")
    await expect(page.getByTestId("hot-spot")).toHaveCount(4)
    await expect(page.getByTestId("investigate")).toHaveCount(0)
  })

  test("a stuck operation in Pending", async ({ page }) => {
    const posted = await mockAI(page)
    await page.route(/\/api\/dashboard\/stuck-operations$/, (r) =>
      r.fulfill({
        json: {
          threshold_minutes: 30,
          items: [
            {
              kind: "sync",
              id: 12,
              label: "packages",
              host_ids: [7],
              hostnames: ["gitlab"],
              started_at: new Date(Date.now() - 3 * 3600_000).toISOString(),
              queued_behind: 2,
            },
          ],
        },
      }),
    )
    await page.goto("/overview?view=pending&lane=stuck")

    await page.getByRole("button", { name: /Why is it stuck\?/ }).click()
    await expect(page.getByRole("dialog")).toContainText("Investigate the stuck sync on gitlab with AI?")
    await page.getByRole("button", { name: "Start investigation" }).click()

    await expect(page).toHaveURL(/session=81$/)
    expect(posted).toEqual([{ subject: { kind: "stuck", op: "sync", id: 12 } }])
  })
})
