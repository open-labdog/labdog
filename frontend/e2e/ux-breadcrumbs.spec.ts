import { test, expect } from "@playwright/test"

test.describe("Breadcrumb navigation UX", () => {
  test("breadcrumb renders on groups page", async ({ page }) => {
    await page.goto("/groups")
    await expect(page.getByRole("heading", { name: "Groups" })).toBeVisible()

    // Scope to main content area to avoid matching sidebar nav
    const main = page.locator("main")
    const breadcrumbNav = main.locator("nav").filter({ hasText: "Groups" })
    await expect(breadcrumbNav).toBeVisible()
  })

  test("breadcrumb on groups/new shows parent link and navigates back", async ({
    page,
  }) => {
    await page.goto("/groups/new")
    await expect(
      page.getByRole("heading", { name: "New Group" })
    ).toBeVisible()

    // Scope to main content area to avoid matching sidebar nav
    const main = page.locator("main")
    const breadcrumbNav = main.locator("nav").filter({ hasText: "Groups" })
    await expect(breadcrumbNav).toBeVisible()
    await expect(breadcrumbNav.getByText("New Group")).toBeVisible()

    const groupsLink = breadcrumbNav.getByRole("link", { name: "Groups" })
    await expect(groupsLink).toBeVisible()
    await groupsLink.click()

    await expect(page).toHaveURL(/\/groups\/?$/)
  })
})
