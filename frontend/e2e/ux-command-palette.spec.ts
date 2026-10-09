import { test, expect } from "./fixtures"

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"
const PLACEHOLDER = "Go to a host, module, scope — or run a command"

test.describe("Command palette (Ctrl+K)", () => {
  test("opens with Ctrl+K keyboard shortcut", async ({ page }) => {
    await page.goto("/overview")
    // Click body to ensure the page has keyboard focus before dispatching shortcut
    await page.click("body")
    await page.keyboard.press("Control+k")

    await expect(page.getByPlaceholder(PLACEHOLDER)).toBeVisible()
  })

  test("closes with Escape key", async ({ page }) => {
    await page.goto("/overview")
    await page.click("body")
    await page.keyboard.press("Control+k")

    const paletteInput = page.getByPlaceholder(PLACEHOLDER)
    await expect(paletteInput).toBeVisible()

    await page.keyboard.press("Escape")
    await expect(paletteInput).not.toBeVisible()
  })

  test("opens from the rail button", async ({ page }) => {
    await page.goto("/overview")
    await page.getByRole("button", { name: "Command palette" }).click()
    await expect(page.getByPlaceholder(PLACEHOLDER)).toBeVisible()
  })

  test("indexes destinations and filters them by query", async ({ page }) => {
    await page.goto("/overview")
    await page.click("body")
    await page.keyboard.press("Control+k")

    const paletteInput = page.getByPlaceholder(PLACEHOLDER)
    await expect(paletteInput).toBeVisible()
    await paletteInput.fill("audit")

    const dialog = page.getByRole("dialog")
    await expect(dialog.getByText("Operations · Audit")).toBeVisible()
    await expect(dialog.getByText("Fleet · Hosts")).not.toBeVisible()
  })

  test("indexes module × group pairs and lands on the group's editor", async ({ request, page }) => {
    const groupName = `e2e-palette-${Date.now()}`
    const res = await request.post(`${API_BASE}/api/groups`, {
      data: { name: groupName, description: null, priority: 991 },
    })
    const group = await res.json()

    await page.goto("/overview")
    await page.click("body")
    await page.keyboard.press("Control+k")

    await page.getByPlaceholder(PLACEHOLDER).fill(`firewall ${groupName}`)
    const dialog = page.getByRole("dialog")
    const entry = dialog.getByText(`Firewall — group: ${groupName}`)
    await expect(entry).toBeVisible()
    await entry.click()
    await expect(page).toHaveURL(new RegExp(`/groups/${group.id}/?\\?tab=config&module=firewall`))
    await expect(page.getByRole("button", { name: "Firewall", pressed: true })).toBeVisible()
  })
})
