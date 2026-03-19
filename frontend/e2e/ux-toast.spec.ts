import { test, expect } from "@playwright/test"

test.describe("Toast notifications (Sonner)", () => {
  test("Toaster component is rendered in DOM", async ({ page }) => {
    await page.goto("/dashboard")

    await expect(page.locator("[data-sonner-toaster]")).toBeAttached()
  })

  test("toast appears after creating a group", async ({ page }) => {
    const groupName = `e2e-toast-${Date.now()}`

    await page.goto("/groups/new")
    await page.locator("#name").fill(groupName)
    await page.locator("#priority").fill("999")
    await page.getByRole("button", { name: "Create Group" }).click()

    await expect(
      page.locator("[data-sonner-toast]").first()
    ).toBeVisible({ timeout: 10000 })
  })
})
