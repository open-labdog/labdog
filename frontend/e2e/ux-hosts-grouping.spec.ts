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
    // The hosts page uses a custom button-based dropdown (not a native <select>)
    // The default state shows "All Groups" as the button label
    const filterButton = page.getByRole("button", { name: /All Groups/i })
    await expect(filterButton).toBeVisible()
  })

  // TODO: "Group View" toggle feature was removed — the hosts page now uses a
  // button-based group filter dropdown instead of a flat/group view toggle.
  test.skip("view toggle button exists with 'Group View' text", async ({ page }) => {
    await page.goto("/hosts")
    await expect(page.getByRole("heading", { name: "Hosts" })).toBeVisible()
    await expect(page.getByText("Group View")).toBeVisible()
  })

  // TODO: Dependent on the removed Group View toggle feature
  test.skip("clicking toggle switches to grouped view with sections", async ({ page }) => {
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
