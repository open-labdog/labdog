import { test, expect } from "./fixtures"

test.describe("Overview page", () => {
  test("/ and /dashboard land on Overview", async ({ page }) => {
    await page.goto("/dashboard")
    await expect(page).toHaveURL(/\/overview\/?$/)
    await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible()
  })

  test("fleet status bar shows every status as a filter", async ({ page }) => {
    await page.goto("/overview")
    // One segment per status; each is a click-through into the hosts list.
    for (const label of ["in sync", "drifted", "syncing", "failed", "unknown"]) {
      await expect(page.getByRole("button", { name: new RegExp(`${label} — click to filter`) })).toBeVisible()
    }
  })

  test("status segment filters the hosts list", async ({ page }) => {
    await page.goto("/overview")
    await page.getByRole("button", { name: /in sync — click to filter/ }).click()
    await expect(page).toHaveURL(/\/hosts\/?\?status=in_sync/)
  })

  test("Drift-check fleet and Plan a sync are the page actions", async ({ page }) => {
    await page.goto("/overview")
    await expect(page.getByRole("button", { name: "Drift-check fleet" })).toBeVisible()
    await page.getByRole("button", { name: "Plan a sync" }).click()
    await expect(page).toHaveURL(/\/plans/)
  })

  test("pending view is reachable from the pane", async ({ page }) => {
    await page.goto("/overview")
    await page.getByRole("complementary").getByRole("link", { name: "Pending" }).click()
    await expect(page).toHaveURL(/\/overview\/?\?view=pending/)
    await expect(page.getByRole("heading", { name: "Pending" })).toBeVisible()
  })

  test("rail carries the five zones and Settings", async ({ page }) => {
    await page.goto("/overview")
    const rail = page.getByRole("navigation", { name: "Zones" })
    for (const zone of ["Overview", "Fleet", "Config", "Operations", "Assistant", "Settings"]) {
      await expect(rail.getByRole("button", { name: zone, exact: true })).toBeVisible()
    }
  })

  test("Fleet zone opens the hosts list with Hosts, Groups and Discovery in the pane", async ({ page }) => {
    await page.goto("/overview")
    await page.getByRole("navigation", { name: "Zones" }).getByRole("button", { name: "Fleet", exact: true }).click()
    await expect(page).toHaveURL(/\/hosts\/?$/)
    const pane = page.getByRole("complementary")
    await expect(pane.getByRole("link", { name: /^Hosts/ })).toBeVisible()
    await expect(pane.getByRole("link", { name: /^Groups/ })).toBeVisible()
    await expect(pane.getByRole("link", { name: /^Discovery/ })).toBeVisible()
  })

  test("pane navigation to Groups works", async ({ page }) => {
    await page.goto("/hosts")
    await page.getByRole("complementary").getByRole("link", { name: /^Groups/ }).click()
    await expect(page).toHaveURL(/\/groups\/?$/)
    await expect(page.getByRole("heading", { name: "Groups" })).toBeVisible()
  })

  test("Operations zone pane reaches Audit", async ({ page }) => {
    await page.goto("/overview")
    await page.getByRole("navigation", { name: "Zones" }).getByRole("button", { name: "Operations", exact: true }).click()
    await page.getByRole("complementary").getByRole("link", { name: "Audit" }).click()
    await expect(page).toHaveURL(/\/audit/)
    await expect(page.getByRole("heading", { name: "Audit", exact: true })).toBeVisible()
  })

  test("Settings sits at the foot of the rail and opens the Integrations registry", async ({ page }) => {
    await page.goto("/overview")
    await page.getByRole("navigation", { name: "Zones" }).getByRole("button", { name: "Settings", exact: true }).click()
    await expect(page).toHaveURL(/\/settings/)
    await expect(page.getByRole("tab", { name: "Integrations", selected: true })).toBeVisible()
  })

  test("account menu carries log out", async ({ page }) => {
    await page.goto("/overview")
    await page.getByRole("button", { name: "Account menu" }).click()
    await expect(page.getByRole("menuitem", { name: "Log out" })).toBeVisible()
    await expect(page.getByRole("menuitem", { name: "Change password…" })).toBeVisible()
  })
})
