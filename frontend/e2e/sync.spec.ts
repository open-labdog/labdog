import { test, expect } from "./fixtures"

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"

/**
 * Group sync.
 *
 * Sync used to be a dialog on the group detail page (`GroupSyncButton`,
 * components/group-sync-dialog.tsx): open it, it previews, Apply stays
 * disabled until the preview finds a change. That dialog is gone. The
 * group page's "Plan sync — N hosts" now opens Operations › Plans scoped
 * to the group (`/plans?scope=group:<id>`), where the dry run, the review
 * and the armed Apply are a screen with a shareable URL. These tests
 * cover that surface.
 */
test.describe("Group sync via Plans", () => {
  test.describe.configure({ mode: "serial" })
  let groupId: number
  let groupName: string

  test.beforeAll(async ({ request }) => {
    groupName = `e2e-sync-group-${Date.now()}`
    const res = await request.post(`${API_BASE}/api/groups`, {
      data: { name: groupName, description: "Sync test group", priority: 995 },
    })
    const group = await res.json()
    groupId = group.id
  })

  test("the group head offers Plan sync, disabled while the group is empty", async ({ page }) => {
    await page.goto(`/groups/${groupId}`)
    const plan = page.getByRole("button", { name: "Plan sync — 0 hosts" })
    await expect(plan).toBeVisible()
    await expect(plan).toBeDisabled()
    await expect(plan).toHaveAttribute("title", /no hosts in this group/)
  })

  test("a plan scoped to the group computes on open and names the group", async ({ page }) => {
    await page.goto(`/plans?scope=group:${groupId}`)
    await expect(page.getByRole("heading", { name: /plan/ })).toBeVisible()
    // The scope tag carries the group and links back to it.
    await expect(page.getByText(`group: ${groupName} →`)).toBeVisible()
  })

  test("an empty group has nothing to apply", async ({ page }) => {
    await page.goto(`/plans?scope=group:${groupId}`)
    // No hosts → the dry run has nothing in scope and Apply cannot be armed.
    await expect(page.getByText("Nothing in scope")).toBeVisible({ timeout: 15000 })
    await expect(page.getByRole("button", { name: "Review & apply…" })).toBeDisabled()
  })

  test("the scope tag returns to the group's Config tab", async ({ page }) => {
    await page.goto(`/plans?scope=group:${groupId}`)
    await page.getByText(`group: ${groupName} →`).click()
    await expect(page).toHaveURL(new RegExp(`/groups/${groupId}/?\\?tab=config`))
    await expect(page.getByRole("tab", { name: "Config", selected: true })).toBeVisible()
  })
})
