import { test, expect } from "./fixtures"

test.describe("Dashboard page", () => {
  test("dashboard loads with heading", async ({ page }) => {
    await page.goto("/dashboard")
    await expect(page.getByRole("heading", { name: "Fleet Overview" })).toBeVisible()
  })

  test("dashboard shows summary cards", async ({ page }) => {
    await page.goto("/dashboard")

    await expect(page.getByText("Total Hosts")).toBeVisible()
    await expect(page.getByText("Hosts in Sync")).toBeVisible()
    await expect(page.getByText("Hosts Drifted")).toBeVisible()
    await expect(page.getByText("Hosts with Errors")).toBeVisible()
    await expect(page.getByText("Unknown / Pending")).toBeVisible()
  })

  test("Collect State button is visible", async ({ page }) => {
    await page.goto("/dashboard")
    await expect(page.getByRole("button", { name: "Collect State" })).toBeVisible()
  })

  test("dashboard shows hosts table when hosts exist or empty state", async ({ page }) => {
    await page.goto("/dashboard")

    // The table is always rendered; when there are no hosts the empty
    // message is a cell *inside* it. `.or()` therefore matched both the
    // table and that cell and failed Playwright's strict mode — asserting
    // on the table alone covers both states.
    await expect(page.getByRole("table")).toBeVisible({ timeout: 10000 })
  })

  test("hosts table has expected columns when populated", async ({ page }) => {
    await page.goto("/dashboard")

    const table = page.getByRole("table")
    const hasTable = await table.isVisible().catch(() => false)

    if (hasTable) {
      const headers = page.getByRole("columnheader")
      await expect(headers.filter({ hasText: "Hostname" })).toBeVisible()
      await expect(headers.filter({ hasText: "IP Address" })).toBeVisible()
      await expect(headers.filter({ hasText: "Status" })).toBeVisible()
    }
  })

  test("sidebar navigation links are visible", async ({ page }) => {
    await page.goto("/dashboard")

    await expect(page.getByRole("link", { name: "Dashboard" })).toBeVisible()
    await expect(page.getByRole("link", { name: "Groups" })).toBeVisible()
    // Not `exact`, this also matches the "View all hosts →" card link.
    await expect(page.getByRole("link", { name: "Hosts", exact: true })).toBeVisible()
    await expect(page.getByRole("link", { name: "SSH Keys" })).toBeVisible()
    await expect(page.getByRole("link", { name: "Audit Log" })).toBeVisible()
  })

  test("sidebar navigation to Groups works", async ({ page }) => {
    await page.goto("/dashboard")
    await page.getByRole("link", { name: "Groups" }).click()
    await expect(page).toHaveURL(/\/groups/)
    await expect(page.getByRole("heading", { name: "Groups" })).toBeVisible()
  })

  test("sidebar navigation to Hosts works", async ({ page }) => {
    await page.goto("/dashboard")
    await page.getByRole("link", { name: "Hosts" }).click()
    await expect(page).toHaveURL(/\/hosts/)
  })

  test("sidebar navigation to SSH Keys works", async ({ page }) => {
    await page.goto("/dashboard")
    await page.getByRole("link", { name: "SSH Keys" }).click()
    await expect(page).toHaveURL(/\/ssh-keys/)
    await expect(page.getByRole("heading", { name: "SSH Keys" })).toBeVisible()
  })

  test("sidebar navigation to Audit Log works", async ({ page }) => {
    await page.goto("/dashboard")
    await page.getByRole("link", { name: "Audit Log" }).click()
    await expect(page).toHaveURL(/\/audit/)
    await expect(page.getByRole("heading", { name: "Audit Log" })).toBeVisible()
  })

  test("Collect State button triggers state collection", async ({ page }) => {
    await page.goto("/dashboard")
    const collectBtn = page.getByRole("button", { name: "Collect State" })
    await expect(collectBtn).toBeVisible()

    // Click should not throw or navigate away
    await collectBtn.click()
    await expect(page).toHaveURL(/\/dashboard/)
  })
})
