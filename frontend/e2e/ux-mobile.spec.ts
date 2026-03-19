import { test, expect } from "@playwright/test"

test.describe("Mobile responsive layout", () => {
  test("mobile viewport hides sidebar and shows hamburger menu", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 375, height: 812 })
    await page.goto("/dashboard")

    await expect(page.locator(".hidden.md\\:block")).not.toBeVisible()
    await expect(page.getByLabel("Open menu")).toBeVisible()
  })

  test("desktop viewport shows sidebar and hides hamburger", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto("/dashboard")

    await expect(page.locator(".hidden.md\\:block")).toBeVisible()
    await expect(page.getByLabel("Open menu")).not.toBeVisible()
  })
})
