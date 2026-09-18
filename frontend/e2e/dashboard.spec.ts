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

    // Scoped to the sidebar, which is what this test is named for. It used
    // to search the whole page and lean on `exact: true` to dodge the
    // "View all hosts →" card link — a workaround that only held while no
    // other link on the dashboard was named exactly "Hosts". One eventually
    // was (the drift-trend empty state), and the assertion failed on a
    // strict-mode violation rather than on anything being wrong with the
    // sidebar. Scoping to the landmark is immune to whatever the page body
    // grows next.
    const sidebar = page.getByRole("complementary")

    await expect(sidebar.getByRole("link", { name: "Dashboard" })).toBeVisible()
    await expect(sidebar.getByRole("link", { name: "Groups" })).toBeVisible()
    await expect(sidebar.getByRole("link", { name: "Hosts", exact: true })).toBeVisible()
    await expect(sidebar.getByRole("link", { name: "SSH Keys" })).toBeVisible()
    await expect(sidebar.getByRole("link", { name: "Audit Log" })).toBeVisible()
  })

  test("sidebar navigation to Groups works", async ({ page }) => {
    await page.goto("/dashboard")
    await page.getByRole("link", { name: "Groups" }).click()
    await expect(page).toHaveURL(/\/groups/)
    await expect(page.getByRole("heading", { name: "Groups" })).toBeVisible()
  })

  test("sidebar navigation to Hosts works", async ({ page }) => {
    await page.goto("/dashboard")
    // Scoped for the same reason as the test above: an unscoped "Hosts"
    // matches the dashboard card link and the drift-trend empty state too,
    // and a strict-mode violation here reads as "the sidebar is broken".
    await page.getByRole("complementary").getByRole("link", { name: "Hosts" }).click()
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
