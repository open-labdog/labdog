import { test, expect } from "@playwright/test"

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"

test.describe("ConfirmDialog UX", () => {
  test("ConfirmDialog appears on SSH key delete with Cancel button", async ({
    page,
    request,
  }) => {
    const listRes = await request.get(`${API_BASE}/api/ssh-keys`, {})
    const keys = await listRes.json()

    if (!Array.isArray(keys) || keys.length === 0) {
      test.skip(true, "No SSH keys exist — skipping delete dialog test")
    }

    await page.goto("/ssh-keys")
    await expect(
      page.getByRole("heading", { name: "SSH Keys" })
    ).toBeVisible()

    await page.getByRole("button", { name: "Delete" }).first().click()

    const dialog = page.getByRole("dialog")
    await expect(dialog).toBeVisible()
    await expect(dialog.getByText("Delete SSH Key")).toBeVisible()

    const cancelButton = dialog.getByRole("button", { name: "Cancel" })
    await expect(cancelButton).toBeVisible()
    await cancelButton.click()

    await expect(dialog).not.toBeVisible()
  })

  test("ConfirmDialog cancel preserves data on groups bulk delete", async ({
    page,
    request,
  }) => {
    const groupName = `e2e-confirm-${Date.now()}`
    await request.post(`${API_BASE}/api/groups`, {
      data: { name: groupName, description: "Confirm dialog test", priority: 999 },
    })

    await page.goto("/groups")
    await expect(page.getByRole("heading", { name: "Groups" })).toBeVisible()

    const row = page.getByRole("row").filter({ hasText: groupName })
    await row.getByRole("checkbox").check()

    await page.getByRole("button", { name: "Delete Selected" }).click()

    const dialog = page.getByRole("dialog")
    await expect(dialog).toBeVisible()
    await expect(dialog.getByText("Delete")).toBeVisible()

    await dialog.getByRole("button", { name: "Cancel" }).click()
    await expect(dialog).not.toBeVisible()

    await expect(page.getByText(groupName)).toBeVisible()
  })
})
