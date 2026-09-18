import { test, expect } from "./fixtures"

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"

/**
 * Group sync.
 *
 * This file used to drive a standalone /groups/{id}/sync page with its own
 * Preview / Apply / confirm-dialog flow. That page no longer exists —
 * `frontend/app/(dashboard)/groups/[id]/` has no `sync/` route — and every
 * test here navigated to a URL the SPA answered with the group detail page,
 * then failed looking for a heading that was never going to be there.
 *
 * Sync is now the `GroupSyncButton` dialog on the group detail page
 * (components/group-sync-dialog.tsx): opening it runs the preview
 * immediately, and Apply Changes stays disabled until the preview finds
 * something to change. These tests cover that surface instead.
 */
test.describe("Group sync dialog", () => {
  test.describe.configure({ mode: "serial" })
  let groupId: number

  test.beforeAll(async ({ request }) => {
    const res = await request.post(`${API_BASE}/api/groups`, {
      data: { name: `e2e-sync-group-${Date.now()}`, description: "Sync test group", priority: 995 },
    })
    const group = await res.json()
    groupId = group.id
  })

  test("the Sync Status card offers a Sync button", async ({ page }) => {
    await page.goto(`/groups/${groupId}`)
    await expect(page.getByRole("heading", { name: "Sync Status" })).toBeVisible()
    await expect(page.getByRole("button", { name: "Sync", exact: true })).toBeVisible()
  })

  test("opening it previews every module", async ({ page }) => {
    await page.goto(`/groups/${groupId}`)
    await page.getByRole("button", { name: "Sync", exact: true }).click()

    const dialog = page.getByRole("dialog")
    await expect(dialog).toBeVisible()
    await expect(
      dialog.getByRole("heading", { name: "Sync all modules — Preview" })
    ).toBeVisible()
  })

  test("an empty group has nothing to apply", async ({ page }) => {
    await page.goto(`/groups/${groupId}`)
    await page.getByRole("button", { name: "Sync", exact: true }).click()

    const dialog = page.getByRole("dialog")
    // The group was created with no hosts, so the preview resolves to the
    // empty state and Apply must stay disabled — `hasChanges` is false.
    await expect(dialog.getByText("No hosts in this group.")).toBeVisible({ timeout: 15000 })
    await expect(dialog.getByRole("button", { name: "Apply Changes" })).toBeDisabled()
  })

  test("Cancel closes the dialog", async ({ page }) => {
    await page.goto(`/groups/${groupId}`)
    await page.getByRole("button", { name: "Sync", exact: true }).click()

    const dialog = page.getByRole("dialog")
    await expect(dialog).toBeVisible()

    await dialog.getByRole("button", { name: "Cancel" }).click()
    await expect(dialog).not.toBeVisible()
  })
})
