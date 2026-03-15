import { test, expect } from "@playwright/test"

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"

test.describe("Rules page", () => {
  let groupId: number

  test.beforeAll(async ({ request }) => {
    const res = await request.post(`${API_BASE}/api/groups`, {
      data: { name: `e2e-rules-group-${Date.now()}`, description: "Rules test group", priority: 996 },
    })
    const group = await res.json()
    groupId = group.id
  })

  test("rules page loads for a group", async ({ page }) => {
    await page.goto(`/groups/${groupId}/rules`)
    await expect(page.getByRole("heading", { name: "Firewall Rules" })).toBeVisible()
    await expect(page.getByRole("button", { name: "Add Rule" })).toBeVisible()
  })

  test("clicking Add Rule opens dialog", async ({ page }) => {
    await page.goto(`/groups/${groupId}/rules`)
    await page.getByRole("button", { name: "Add Rule" }).click()

    await expect(page.getByRole("dialog")).toBeVisible()
    await expect(page.getByRole("heading", { name: "Add Rule" })).toBeVisible()
    await expect(page.locator("#action")).toBeVisible()
    await expect(page.locator("#protocol")).toBeVisible()
    await expect(page.locator("#direction")).toBeVisible()
  })

  test("create a TCP allow rule via dialog", async ({ page }) => {
    await page.goto(`/groups/${groupId}/rules`)
    await page.getByRole("button", { name: "Add Rule" }).click()

    await page.locator("#action").selectOption("allow")
    await page.locator("#protocol").selectOption("tcp")
    await page.locator("#direction").selectOption("input")
    await page.locator("#source_cidr").fill("0.0.0.0/0")
    await page.locator("#port_start").fill("80")
    await page.locator("#port_end").fill("80")
    await page.locator("#comment").fill("Allow HTTP")

    await page.getByRole("button", { name: "Add Rule" }).click()

    await expect(page.getByRole("dialog")).not.toBeVisible()
    await expect(page.getByText("Allow HTTP")).toBeVisible()
  })

  test("create a deny rule and see it in table", async ({ page }) => {
    await page.goto(`/groups/${groupId}/rules`)
    await page.getByRole("button", { name: "Add Rule" }).click()

    await page.locator("#action").selectOption("deny")
    await page.locator("#protocol").selectOption("tcp")
    await page.locator("#direction").selectOption("input")
    await page.locator("#comment").fill("E2E deny rule")

    await page.getByRole("button", { name: "Add Rule" }).click()

    await expect(page.getByRole("dialog")).not.toBeVisible()
    await expect(page.getByText("E2E deny rule")).toBeVisible()
  })

  test("cancel button closes dialog without saving", async ({ page }) => {
    await page.goto(`/groups/${groupId}/rules`)
    await page.getByRole("button", { name: "Add Rule" }).click()

    await page.locator("#comment").fill("Should not be saved")
    await page.getByRole("button", { name: "Cancel" }).click()

    await expect(page.getByRole("dialog")).not.toBeVisible()
    await expect(page.getByText("Should not be saved")).not.toBeVisible()
  })

  test("system rules have disabled Edit and Delete buttons", async ({ request, page }) => {
    // Create a system rule via API
    await request.post(`${API_BASE}/api/groups/${groupId}/rules`, {
      data: {
        action: "allow",
        protocol: "tcp",
        direction: "input",
        source_cidr: null,
        destination_cidr: null,
        port_start: null,
        port_end: null,
        comment: "system-rule-e2e",
        is_system: true,
      },
    })

    await page.goto(`/groups/${groupId}/rules`)

    // Find the row with the system rule comment
    const systemRow = page.getByRole("row").filter({ hasText: "system-rule-e2e" })
    await expect(systemRow).toBeVisible()

    const editBtn = systemRow.getByRole("button", { name: "Edit" })
    const deleteBtn = systemRow.getByRole("button", { name: "Delete" })

    await expect(editBtn).toBeDisabled()
    await expect(deleteBtn).toBeDisabled()
  })

  test("edit an existing rule", async ({ request, page }) => {
    const ruleRes = await request.post(`${API_BASE}/api/groups/${groupId}/rules`, {
      data: {
        action: "allow",
        protocol: "tcp",
        direction: "input",
        source_cidr: null,
        destination_cidr: null,
        port_start: 443,
        port_end: 443,
        comment: "original-comment",
        is_system: false,
      },
    })
    await ruleRes.json()

    await page.goto(`/groups/${groupId}/rules`)

    const ruleRow = page.getByRole("row").filter({ hasText: "original-comment" })
    await ruleRow.getByRole("button", { name: "Edit" }).click()

    await expect(page.getByRole("dialog")).toBeVisible()
    await expect(page.getByRole("heading", { name: "Edit Rule" })).toBeVisible()

    await page.locator("#comment").fill("updated-comment")
    await page.getByRole("button", { name: "Save Changes" }).click()

    await expect(page.getByRole("dialog")).not.toBeVisible()
    await expect(page.getByText("updated-comment")).toBeVisible()
  })
})
