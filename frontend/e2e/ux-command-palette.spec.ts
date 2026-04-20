import { test, expect } from "@playwright/test"

test.describe("Command palette (Ctrl+K)", () => {
  test("opens with Ctrl+K keyboard shortcut", async ({ page }) => {
    await page.goto("/dashboard")
    // Click body to ensure the page has keyboard focus before dispatching shortcut
    await page.click("body")
    await page.keyboard.press("Control+k")

    await expect(page.getByPlaceholder("Search pages...")).toBeVisible()
  })

  test("closes with Escape key", async ({ page }) => {
    await page.goto("/dashboard")
    await page.click("body")
    await page.keyboard.press("Control+k")

    const paletteInput = page.getByPlaceholder("Search pages...")
    await expect(paletteInput).toBeVisible()

    await page.keyboard.press("Escape")
    await expect(paletteInput).not.toBeVisible()
  })

  test("filters navigation items by search query", async ({ page }) => {
    await page.goto("/dashboard")
    await page.click("body")
    await page.keyboard.press("Control+k")

    const paletteInput = page.getByPlaceholder("Search pages...")
    await expect(paletteInput).toBeVisible()
    await paletteInput.fill("Hosts")

    const dialog = page.getByRole("dialog")
    await expect(dialog.getByText("Hosts")).toBeVisible()
    await expect(dialog.getByText("Audit Log")).not.toBeVisible()
  })
})
