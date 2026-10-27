import { test, expect } from "./fixtures"

test.describe("Responsive shell", () => {
  test("phone width: rail moves to the bottom edge, no pane", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto("/overview")

    const rail = page.getByRole("navigation", { name: "Zones" })
    await expect(rail).toBeVisible()
    // The bottom rail labels its zones; the desktop rail relies on titles.
    await expect(rail.getByText("Fleet", { exact: true })).toBeVisible()
    await expect(page.getByRole("complementary")).toHaveCount(0)
    await expect(page.getByRole("button", { name: "Search" })).toBeVisible()
  })

  test("desktop width: icon rail beside a contextual pane", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto("/overview")

    await expect(page.getByRole("navigation", { name: "Zones" })).toBeVisible()
    await expect(page.getByRole("complementary")).toBeVisible()
    await expect(page.getByRole("button", { name: "Search" })).toHaveCount(0)
  })

  test("1024 width: the pane is an overlay that opens on demand", async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 768 })
    await page.goto("/overview")

    await expect(page.getByRole("complementary")).toHaveCount(0)
    await page.getByRole("button", { name: "Open pane" }).click()
    await expect(page.getByRole("complementary")).toBeVisible()
    await page.getByRole("button", { name: "Close pane" }).click()
    await expect(page.getByRole("complementary")).toHaveCount(0)
  })

  test("[ toggles the pane", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto("/overview")
    await page.click("body")
    await expect(page.getByRole("complementary")).toBeVisible()
    await page.keyboard.press("[")
    await expect(page.getByRole("complementary")).toHaveCount(0)
    await page.keyboard.press("[")
    await expect(page.getByRole("complementary")).toBeVisible()
  })
})
