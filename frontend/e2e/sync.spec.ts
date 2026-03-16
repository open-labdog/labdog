import { test, expect } from "@playwright/test"

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"

test.describe("Sync page", () => {
  test.describe.configure({ mode: "serial" })
  let groupId: number

  test.beforeAll(async ({ request }) => {
    const res = await request.post(`${API_BASE}/api/groups`, {
      data: { name: `e2e-sync-group-${Date.now()}`, description: "Sync test group", priority: 995 },
    })
    const group = await res.json()
    groupId = group.id
  })

  test("sync page loads with correct heading", async ({ page }) => {
    await page.goto(`/groups/${groupId}/sync`)
    await expect(page.getByRole("heading", { name: "Sync Group" })).toBeVisible()
    await expect(page.getByRole("button", { name: "Preview Changes" })).toBeVisible()
  })

  test("Apply Changes button is disabled before preview", async ({ page }) => {
    await page.goto(`/groups/${groupId}/sync`)
    const applyBtn = page.getByRole("button", { name: "Apply Changes" })
    await expect(applyBtn).toBeDisabled()
  })

  test("Preview Changes button triggers preview", async ({ page }) => {
    await page.goto(`/groups/${groupId}/sync`)
    await page.getByRole("button", { name: "Preview Changes" }).click()

    // Wait for preview to complete (either shows planned changes or no hosts message)
    await expect(
      page.getByText("Planned Changes").or(page.getByText("No hosts in this group"))
    ).toBeVisible({ timeout: 15000 })
  })

  test("Apply Changes button is enabled after preview", async ({ page }) => {
    await page.goto(`/groups/${groupId}/sync`)
    await page.getByRole("button", { name: "Preview Changes" }).click()

    await expect(
      page.getByText("Planned Changes").or(page.getByText("No hosts in this group"))
    ).toBeVisible({ timeout: 15000 })

    const applyBtn = page.getByRole("button", { name: "Apply Changes" })
    await expect(applyBtn).toBeEnabled()
  })

  test("Apply Changes button opens confirmation dialog", async ({ page }) => {
    await page.goto(`/groups/${groupId}/sync`)
    await page.getByRole("button", { name: "Preview Changes" }).click()

    await expect(
      page.getByText("Planned Changes").or(page.getByText("No hosts in this group"))
    ).toBeVisible({ timeout: 15000 })

    await page.getByRole("button", { name: "Apply Changes" }).click()

    await expect(page.getByRole("dialog")).toBeVisible()
    await expect(page.getByRole("heading", { name: "Confirm Apply" })).toBeVisible()
    await expect(page.getByRole("button", { name: "Apply Changes" }).nth(1)).toBeVisible()
    await expect(page.getByRole("button", { name: "Cancel" })).toBeVisible()
  })

  test("Cancel in confirmation dialog closes it without applying", async ({ page }) => {
    await page.goto(`/groups/${groupId}/sync`)
    await page.getByRole("button", { name: "Preview Changes" }).click()

    await expect(
      page.getByText("Planned Changes").or(page.getByText("No hosts in this group"))
    ).toBeVisible({ timeout: 15000 })

    await page.getByRole("button", { name: "Apply Changes" }).click()
    await expect(page.getByRole("dialog")).toBeVisible()

    await page.getByRole("button", { name: "Cancel" }).click()
    await expect(page.getByRole("dialog")).not.toBeVisible()
  })

  test("initial state shows prompt to click Preview Changes", async ({ page }) => {
    await page.goto(`/groups/${groupId}/sync`)
    await expect(page.getByText("Preview Changes")).toBeVisible()
  })
})
