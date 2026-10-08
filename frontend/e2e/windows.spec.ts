import { test, expect } from "./fixtures"
import { mockRun } from "./run-mock"

// The run log and the SSH terminal can be resized, maximized and
// minimized, and their text size changed; the choices survive a reload.

const LOG = Array.from({ length: 80 }, (_, i) => `ok: [web1] => line ${i + 1}`).join("\n")

test.describe("Run log window", () => {
  test("text size, maximize, minimize and drag", async ({ page }) => {
    const url = await mockRun(page, { log: LOG })
    await page.goto(url)
    const win = page.getByTestId("log-window")
    const log = page.getByTestId("run-log")
    await expect(log).toContainText("line 80")
    await expect(log).toHaveCSS("font-size", "11px")

    await win.getByRole("button", { name: "Larger text" }).click()
    await win.getByRole("button", { name: "Larger text" }).click()
    await expect(log).toHaveCSS("font-size", "13px")

    // Maximize fills the window; Esc puts it back.
    await win.getByRole("button", { name: "Maximize" }).click()
    const vp = page.viewportSize()!
    const box = (await win.boundingBox())!
    expect(Math.round(box.width)).toBe(vp.width)
    expect(Math.round(box.height)).toBe(vp.height)
    await page.keyboard.press("Escape")
    await expect(win).not.toHaveAttribute("data-maximized", "true")

    // Drag the bottom edge up by 150px.
    const before = (await win.boundingBox())!.height
    const handle = win.getByRole("separator")
    const h = (await handle.boundingBox())!
    await page.mouse.move(h.x + h.width / 2, h.y + h.height / 2)
    await page.mouse.down()
    await page.mouse.move(h.x + h.width / 2, h.y + h.height / 2 - 150, { steps: 5 })
    await page.mouse.up()
    const after = (await win.boundingBox())!.height
    expect(Math.round(before - after)).toBe(150)

    // Minimize hides the body without removing it.
    await win.getByRole("button", { name: "Minimize" }).click()
    await expect(log).toBeHidden()
    await expect(log).toBeAttached()

    // Remembered across a reload: minimized, 13px, the dragged height.
    await page.reload()
    await expect(win).toHaveAttribute("data-minimized", "true")
    await win.getByRole("button", { name: "Restore" }).click()
    await expect(log).toHaveCSS("font-size", "13px")
    expect(Math.round((await win.boundingBox())!.height)).toBe(Math.round(after))

    // Double-clicking the handle returns to the default height.
    await win.getByRole("separator").dblclick()
    expect(Math.round((await win.boundingBox())!.height)).toBe(Math.round(before))
  })
})

test.describe("Terminal window", () => {
  test("minimize keeps the terminal mounted; Esc stays with the shell", async ({ page }) => {
    await page.goto("/hosts/1/terminal", { waitUntil: "domcontentloaded" })
    const win = page.getByTestId("terminal-window")
    const term = page.getByTestId("ssh-terminal")
    await expect(term).toBeAttached()

    await win.getByRole("button", { name: "Minimize" }).click()
    await expect(win).toHaveAttribute("data-minimized", "true")
    await expect(term).toBeAttached()
    await win.getByRole("button", { name: "Restore" }).click()

    await win.getByRole("button", { name: "Maximize" }).click()
    await page.keyboard.press("Escape")
    await expect(win).toHaveAttribute("data-maximized", "true")
    await win.getByRole("button", { name: "Exit maximized" }).click()
    await expect(win).not.toHaveAttribute("data-maximized", "true")
  })
})
