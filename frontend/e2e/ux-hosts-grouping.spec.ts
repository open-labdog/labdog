import { test, expect } from "@playwright/test"

test.describe("Hosts page group features", () => {
  test("groups column header exists in hosts table", async ({ page }) => {
    await page.goto("/hosts")
    await expect(page.getByRole("heading", { name: "Hosts" })).toBeVisible()
    await expect(
      page.getByRole("columnheader", { name: "Groups" })
    ).toBeVisible()
  })

  test("group filter dropdown exists with 'All Groups' default", async ({ page }) => {
    await page.goto("/hosts")
    await expect(page.getByRole("heading", { name: "Hosts" })).toBeVisible()
    const select = page.locator("select")
    await expect(select).toBeVisible()
    await expect(select.locator("option", { hasText: "All Groups" })).toBeAttached()
    await expect(select.locator("option", { hasText: "Ungrouped" })).toBeAttached()
  })

  test("view toggle button exists with 'Group View' text", async ({ page }) => {
    await page.goto("/hosts")
    await expect(page.getByRole("heading", { name: "Hosts" })).toBeVisible()
    await expect(page.getByText("Group View")).toBeVisible()
  })

  test("clicking toggle switches to grouped view with sections", async ({ page }) => {
    await page.goto("/hosts")
    await expect(page.getByRole("heading", { name: "Hosts" })).toBeVisible()
    await page.getByText("Group View").click()
    await expect(page.getByText("Flat View")).toBeVisible()
    const details = page.locator("details")
    const hasHosts = (await page.locator("table").count()) > 0
    if (hasHosts) {
      await expect(details.first()).toBeVisible()
    }
  })
})
