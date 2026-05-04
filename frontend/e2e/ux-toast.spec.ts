import { test, expect } from "@playwright/test"

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"

test.describe("Toast notifications (Sonner)", () => {
  test("Toaster component is rendered in DOM", async ({ page }) => {
    await page.goto("/dashboard")

    // Sonner v2 renders a <section> wrapper element always (even with no toasts).
    // The <ol data-sonner-toaster> only mounts when there are active toasts.
    // Check that the section with aria-label matching "Notifications" exists.
    await expect(
      page.locator("section[aria-label*='Notifications']")
    ).toBeAttached()
  })

  test("toast appears after creating a group", async ({ page, request }) => {
    // Create a group via API first to ensure the groups page has data
    const groupName = `e2e-toast-del-${Date.now()}`
    const res = await request.post(`${API_BASE}/api/groups`, {
      data: { name: groupName, description: null, priority: 1 },
    })
    const group = await res.json()

    // Navigate to groups page — delete the group to trigger a success toast
    await page.goto("/groups")
    await expect(page.getByRole("heading", { name: "Groups" })).toBeVisible()

    // Check the row's checkbox and then delete
    const row = page.getByRole("row").filter({ hasText: groupName })
    await row.getByRole("checkbox").check()
    await page.getByRole("button", { name: "Delete Selected" }).click()

    // Confirm the deletion dialog
    const dialog = page.getByRole("dialog")
    await expect(dialog).toBeVisible()
    await dialog.getByRole("button", { name: /Delete/i }).last().click()

    // A success toast should appear
    await expect(
      page.locator("[data-sonner-toaster]").first()
    ).toBeAttached({ timeout: 10000 })
  })
})
