import { test, expect } from "@playwright/test"

test.describe("Dashboard page", () => {
  test("dashboard loads with heading", async ({ page }) => {
    await page.goto("/dashboard")
    await expect(page.getByRole("heading", { name: "Drift Dashboard" })).toBeVisible()
  })

  test("dashboard shows summary cards", async ({ page }) => {
    await page.goto("/dashboard")

    await expect(page.getByText("Total Hosts")).toBeVisible()
    await expect(page.getByText("In Sync")).toBeVisible()
    await expect(page.getByText("Out of Sync")).toBeVisible()
    await expect(page.getByText("Error")).toBeVisible()
    await expect(page.getByText("Unknown")).toBeVisible()
  })

  test("Check All button is visible", async ({ page }) => {
    await page.goto("/dashboard")
    await expect(page.getByRole("button", { name: "Check All" })).toBeVisible()
  })

  test("dashboard shows hosts table when hosts exist or empty state", async ({ page }) => {
    await page.goto("/dashboard")

    await expect(
      page.getByRole("table").or(page.getByText("No hosts configured yet."))
    ).toBeVisible({ timeout: 10000 })
  })

  test("hosts table has expected columns when populated", async ({ page }) => {
    await page.goto("/dashboard")

    const table = page.getByRole("table")
    const hasTable = await table.isVisible().catch(() => false)

    if (hasTable) {
      const headers = page.getByRole("columnheader")
      await expect(headers.filter({ hasText: "Hostname" })).toBeVisible()
      await expect(headers.filter({ hasText: "IP Address" })).toBeVisible()
      await expect(headers.filter({ hasText: "Sync Status" })).toBeVisible()
    }
  })

  test("sidebar navigation links are visible", async ({ page }) => {
    await page.goto("/dashboard")

    await expect(page.getByRole("link", { name: "Dashboard" })).toBeVisible()
    await expect(page.getByRole("link", { name: "Groups" })).toBeVisible()
    await expect(page.getByRole("link", { name: "Hosts" })).toBeVisible()
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

  test("Check All button triggers drift check", async ({ page }) => {
    await page.goto("/dashboard")
    const checkAllBtn = page.getByRole("button", { name: "Check All" })
    await expect(checkAllBtn).toBeVisible()

    // Click should not throw or navigate away
    await checkAllBtn.click()
    await expect(page).toHaveURL(/\/dashboard/)
  })
})
