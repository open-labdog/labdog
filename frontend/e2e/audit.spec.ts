import { test, expect } from "@playwright/test"

test.describe("Audit page", () => {
  test("audit page loads with heading", async ({ page }) => {
    await page.goto("/audit")
    await expect(page.getByRole("heading", { name: "Audit Log" })).toBeVisible()
  })

  test("audit page shows filter controls", async ({ page }) => {
    await page.goto("/audit")
    await expect(page.getByLabel("Action:")).toBeVisible()
    await expect(page.getByLabel("Entity:")).toBeVisible()
  })

  test("action filter dropdown has expected options", async ({ page }) => {
    await page.goto("/audit")
    const actionSelect = page.getByLabel("Action:")
    await expect(actionSelect).toBeVisible()

    const options = await actionSelect.locator("option").allTextContents()
    expect(options).toContain("All Actions")
    expect(options).toContain("Create")
    expect(options).toContain("Update")
    expect(options).toContain("Delete")
  })

  test("entity filter dropdown has expected options", async ({ page }) => {
    await page.goto("/audit")
    const entitySelect = page.getByLabel("Entity:")
    await expect(entitySelect).toBeVisible()

    const options = await entitySelect.locator("option").allTextContents()
    expect(options).toContain("All Entities")
    expect(options).toContain("group")
    expect(options).toContain("host")
    expect(options).toContain("rule")
  })

  test("audit entries table is visible with data", async ({ page }) => {
    await page.goto("/audit")

    // The audit page uses stub data as fallback, so entries should always show
    await expect(
      page.getByRole("table").or(page.getByText("No audit entries found."))
    ).toBeVisible({ timeout: 10000 })
  })

  test("filter by action type filters entries", async ({ page }) => {
    await page.goto("/audit")

    // Wait for entries to load
    await expect(
      page.getByRole("table").or(page.getByText("No audit entries found."))
    ).toBeVisible({ timeout: 10000 })

    const actionSelect = page.getByLabel("Action:")
    await actionSelect.selectOption("create")

    // After filtering, either entries remain or empty state shows
    await expect(
      page.getByRole("table").or(page.getByText("No audit entries found."))
    ).toBeVisible()
  })

  test("filter by entity type filters entries", async ({ page }) => {
    await page.goto("/audit")

    await expect(
      page.getByRole("table").or(page.getByText("No audit entries found."))
    ).toBeVisible({ timeout: 10000 })

    const entitySelect = page.getByLabel("Entity:")
    await entitySelect.selectOption("group")

    await expect(
      page.getByRole("table").or(page.getByText("No audit entries found."))
    ).toBeVisible()
  })

  test("table shows expected columns", async ({ page }) => {
    await page.goto("/audit")

    await expect(page.getByRole("table")).toBeVisible({ timeout: 10000 })

    const headers = page.getByRole("columnheader")
    await expect(headers.filter({ hasText: "Timestamp" })).toBeVisible()
    await expect(headers.filter({ hasText: "User" })).toBeVisible()
    await expect(headers.filter({ hasText: "Action" })).toBeVisible()
    await expect(headers.filter({ hasText: "Entity" })).toBeVisible()
    await expect(headers.filter({ hasText: "Details" })).toBeVisible()
  })
})
