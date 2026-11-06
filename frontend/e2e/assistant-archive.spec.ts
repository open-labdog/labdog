import type { Page, Route } from "@playwright/test"
import { test, expect } from "./fixtures"

// The assistant's session list: an archive beside the list, a select mode
// that archives, unarchives or deletes several sessions at once, and
// paging. The session endpoints are mocked; the rest of the page talks to
// the real backend.

function session(id: number, extra: Record<string, unknown> = {}) {
  return {
    id,
    provider_id: null,
    mode: "chat",
    title: `Session ${id}`,
    mission: `Mission ${id}`,
    autonomy_level: "read_only",
    status: "succeeded",
    target_host_ids: [],
    skip_snapshots: false,
    action_run_id: null,
    investigates_action_run_id: null,
    iterations: 1,
    prompt_tokens: 0,
    completion_tokens: 0,
    cost: 0,
    cost_unknown: false,
    command_count: 0,
    report_markdown: null,
    error_message: null,
    stopped_reason: null,
    created_at: "2026-10-08T02:00:00Z",
    started_at: "2026-10-08T02:00:01Z",
    finished_at: "2026-10-08T02:05:00Z",
    archived_at: null,
    ...extra,
  }
}

/** Serve the list per view; `pages` answers the active view by cursor. */
async function mockList(page: Page, { active, archived = [] }: { active: (before: string | null) => unknown[]; archived?: unknown[] }) {
  const seen: string[] = []
  await page.route(/\/api\/ai\/sessions\?/, (r: Route) => {
    const url = new URL(r.request().url())
    seen.push(url.search)
    const view = url.searchParams.get("view")
    return r.fulfill({ json: view === "archived" ? archived : active(url.searchParams.get("before_id")) })
  })
  return seen
}

/** Capture what one bulk endpoint was sent, and answer with `result`. */
async function mockBulk(page: Page, path: string, result: unknown) {
  const sent: unknown[] = []
  await page.route(new RegExp(`/api/ai/sessions/${path}$`), (r) => {
    sent.push(r.request().postDataJSON())
    return r.fulfill({ json: result })
  })
  return sent
}

test.describe("Assistant session archive", () => {
  test("archives the picked sessions", async ({ page }) => {
    await mockList(page, { active: () => [session(2), session(1)] })
    const sent = await mockBulk(page, "archive", { done: [2], skipped: [] })
    await page.goto("/assistant")

    const list = page.getByRole("region", { name: "sessions" })
    await expect(list).toContainText("Session 2")
    await list.getByRole("button", { name: "select", exact: true }).click()
    await list.getByRole("checkbox", { name: "Select session: Session 2" }).check()
    await expect(list).toContainText("1 selected")
    await list.getByRole("button", { name: "archive", exact: true }).click()

    await expect(page.getByText("1 session archived")).toBeVisible()
    expect(sent).toEqual([{ session_ids: [2] }])
  })

  test("deletes from the archive and says what it kept", async ({ page }) => {
    await mockList(page, { active: () => [], archived: [session(5, { archived_at: "2026-10-09T00:00:00Z" }), session(4, { archived_at: "2026-10-09T00:00:00Z" })] })
    const sent = await mockBulk(page, "delete", {
      done: [5],
      skipped: [{ session_id: 4, reason: "This full-auto alert fix still counts towards the remediation safeguards." }],
    })
    await page.goto("/assistant")

    const list = page.getByRole("region", { name: "sessions" })
    await list.getByRole("button", { name: "archived", exact: true }).click()
    await expect(list).toContainText("Session 5")
    await list.getByRole("button", { name: "select", exact: true }).click()
    await list.getByRole("checkbox", { name: "Select session: Session 5" }).check()
    await list.getByRole("checkbox", { name: "Select session: Session 4" }).check()
    await list.getByRole("button", { name: "delete", exact: true }).click()
    await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click()

    await expect(page.getByText("1 session deleted, 1 kept")).toBeVisible()
    await expect(page.getByText("still counts towards the remediation safeguards")).toBeVisible()
    expect(sent).toEqual([{ session_ids: [5, 4] }])
  })

  test("archives everything finished before a date", async ({ page }) => {
    await mockList(page, { active: () => [session(1)] })
    const sent = await mockBulk(page, "archive", { done: [1], skipped: [] })
    await page.goto("/assistant")

    await page.getByRole("button", { name: "archive old…" }).click()
    const dialog = page.getByRole("dialog")
    await dialog.getByLabel("finished before").fill("2026-10-01")
    await dialog.getByLabel("alert investigations only").check()
    await dialog.getByRole("button", { name: "Archive" }).click()

    await expect(page.getByText("1 session archived")).toBeVisible()
    const body = sent[0] as { finished_before: string; mode: string }
    expect(body.mode).toBe("alert_investigation")
    expect(new Date(body.finished_before).getTime()).toBe(new Date("2026-10-01T00:00:00").getTime())
  })

  test("loads older sessions by cursor", async ({ page }) => {
    const first = Array.from({ length: 50 }, (_, i) => session(200 - i))
    const seen = await mockList(page, { active: (before) => (before === null ? first : [session(100)]) })
    await page.goto("/assistant")

    const list = page.getByRole("region", { name: "sessions" })
    await expect(list).toContainText("Session 151")
    await list.getByRole("button", { name: "load older" }).click()

    await expect(list).toContainText("Session 100")
    await expect(list.getByRole("button", { name: "load older" })).toHaveCount(0)
    expect(seen.some((q) => q.includes("before_id=151"))).toBe(true)
  })
})
