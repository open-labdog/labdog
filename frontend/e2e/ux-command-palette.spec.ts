import { test, expect } from "./fixtures"

const PLACEHOLDER = "Go to a host, module, scope — or run a command"

test.describe("Command palette (Ctrl+K)", () => {
  test("opens with Ctrl+K keyboard shortcut", async ({ page }) => {
    await page.goto("/overview")
    // Click body to ensure the page has keyboard focus before dispatching shortcut
    await page.click("body")
    await page.keyboard.press("Control+k")

    await expect(page.getByPlaceholder(PLACEHOLDER)).toBeVisible()
  })

  test("closes with Escape key", async ({ page }) => {
    await page.goto("/overview")
    await page.click("body")
    await page.keyboard.press("Control+k")

    const paletteInput = page.getByPlaceholder(PLACEHOLDER)
    await expect(paletteInput).toBeVisible()

    await page.keyboard.press("Escape")
    await expect(paletteInput).not.toBeVisible()
  })

  test("opens from the rail button", async ({ page }) => {
    await page.goto("/overview")
    await page.getByRole("button", { name: "Command palette" }).click()
    await expect(page.getByPlaceholder(PLACEHOLDER)).toBeVisible()
  })

  test("indexes destinations and filters them by query", async ({ page }) => {
    await page.goto("/overview")
    await page.click("body")
    await page.keyboard.press("Control+k")

    const paletteInput = page.getByPlaceholder(PLACEHOLDER)
    await expect(paletteInput).toBeVisible()
    await paletteInput.fill("audit")

    const dialog = page.getByRole("dialog")
    await expect(dialog.getByText("Operations · Audit")).toBeVisible()
    await expect(dialog.getByText("Fleet · Hosts")).not.toBeVisible()
  })

  test("indexes module × scope pairs", async ({ page }) => {
    await page.goto("/overview")
    await page.click("body")
    await page.keyboard.press("Control+k")

    await page.getByPlaceholder(PLACEHOLDER).fill("firewall fleet")
    const dialog = page.getByRole("dialog")
    await expect(dialog.getByText("Firewall — fleet")).toBeVisible()
    await dialog.getByText("Firewall — fleet").click()
    await expect(page).toHaveURL(/\/config\/firewall/)
  })
})
