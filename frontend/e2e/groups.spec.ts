import { test, expect } from "@playwright/test"

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"

test.describe("Groups page", () => {
  test("groups list page loads", async ({ page }) => {
    await page.goto("/groups")
    await expect(page.getByRole("heading", { name: "Groups" })).toBeVisible()
    await expect(page.getByRole("link", { name: "New Group" })).toBeVisible()
  })

  test("new group link navigates to /groups/new", async ({ page }) => {
    await page.goto("/groups")
    await page.getByRole("link", { name: "New Group" }).click()
    await expect(page).toHaveURL(/\/groups\/new/)
  })

  test("new group form renders correctly", async ({ page }) => {
    await page.goto("/groups/new")
    await expect(page.getByRole("heading", { name: "New Group" })).toBeVisible()
    await expect(page.locator("#name")).toBeVisible()
    await expect(page.locator("#description")).toBeVisible()
    await expect(page.locator("#priority")).toBeVisible()
    await expect(page.getByRole("button", { name: "Create Group" })).toBeVisible()
  })

  test("create group via form and see it in list", async ({ page }) => {
    const groupName = `e2e-group-${Date.now()}`

    await page.goto("/groups/new")
    await page.locator("#name").fill(groupName)
    await page.locator("#description").fill("E2E test group")
    await page.locator("#priority").fill("999")
    await page.getByRole("button", { name: "Create Group" }).click()

    await page.waitForURL("**/groups")
    await expect(page).toHaveURL(/\/groups$/)
    await expect(page.getByText(groupName)).toBeVisible()
  })

  test("cancel button on new group form returns to groups list", async ({ page }) => {
    await page.goto("/groups/new")
    await page.getByRole("button", { name: "Cancel" }).click()
    await expect(page).toHaveURL(/\/groups$/)
  })

  test("group detail page shows group info", async ({ request, page }) => {
    const groupName = `e2e-detail-${Date.now()}`
    const res = await request.post(`${API_BASE}/api/groups`, {
      data: { name: groupName, description: "Detail test", priority: 998 },
    })
    const group = await res.json()

    await page.goto(`/groups/${group.id}`)
    await expect(page.getByRole("heading", { name: groupName })).toBeVisible()
    await expect(page.getByRole("link", { name: "Manage Rules" })).toBeVisible()
    await expect(page.getByRole("link", { name: "Sync" })).toBeVisible()
  })

  test("group detail shows priority card", async ({ request, page }) => {
    const groupName = `e2e-priority-${Date.now()}`
    const res = await request.post(`${API_BASE}/api/groups`, {
      data: { name: groupName, description: null, priority: 42 },
    })
    const group = await res.json()

    await page.goto(`/groups/${group.id}`)
    await expect(page.getByText("42")).toBeVisible()
  })

  test("group not found shows error message", async ({ page }) => {
    await page.goto("/groups/999999")
    await expect(page.getByText("Group not found")).toBeVisible()
  })
})
