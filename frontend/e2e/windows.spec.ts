import { test, expect } from "./fixtures"
import type { Locator, Page } from "@playwright/test"
import { mockRun } from "./run-mock"

/** Drag a resize handle vertically by `dy` pixels. */
async function drag(page: Page, handle: Locator, dy: number) {
  const h = (await handle.boundingBox())!
  await page.mouse.move(h.x + h.width / 2, h.y + h.height / 2)
  await page.mouse.down()
  await page.mouse.move(h.x + h.width / 2, h.y + h.height / 2 + dy, { steps: 5 })
  await page.mouse.up()
}

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

    // The log is docked at the bottom with its handle on top: dragging the
    // handle down by 150px shrinks it, and its bottom edge stays put.
    const start = (await win.boundingBox())!
    const before = start.height
    await drag(page, win.getByRole("separator"), 150)
    const end = (await win.boundingBox())!
    const after = end.height
    expect(Math.round(before - after)).toBe(150)
    expect(Math.round(end.y + end.height)).toBe(Math.round(start.y + start.height))

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

test.describe("Run page with many hosts", () => {
  test("the log grows upwards over the host table, and no further", async ({ page }) => {
    await page.goto(await mockRun(page, { log: LOG, hosts: 17 }))
    const win = page.getByTestId("log-window")
    await expect(page.getByTestId("run-log")).toContainText("line 80")
    const start = (await win.boundingBox())!
    await drag(page, win.getByRole("separator"), -150)
    const grown = (await win.boundingBox())!
    expect(Math.round(grown.height - start.height)).toBe(150)
    expect(Math.round(grown.y + grown.height)).toBe(Math.round(start.y + start.height))
    // As far up as the page's content goes, and the handle stays on screen.
    await drag(page, win.getByRole("separator"), -2000)
    const top = (await win.boundingBox())!
    const head = (await page.getByRole("heading", { name: "linux-upgrade" }).boundingBox())!
    expect(top.y).toBeGreaterThan(head.y + head.height)
    await expect(win.getByRole("separator")).toBeInViewport()
  })

  test("the host table is paged, at a size that is remembered", async ({ page }) => {
    await page.goto(await mockRun(page, { log: LOG, hosts: 17 }))
    const range = page.getByTestId("pager-range")
    await expect(range).toHaveText("1–10 of 17")
    await expect(page.getByText("host-10", { exact: true })).toBeVisible()
    await expect(page.getByText("host-11", { exact: true })).toHaveCount(0)
    await page.getByRole("button", { name: "Next page" }).click()
    await expect(range).toHaveText("11–17 of 17")
    await expect(page.getByText("host-17", { exact: true })).toBeVisible()
    await page.getByLabel("hosts per page").selectOption("25")
    await expect(range).toHaveText("1–17 of 17")
    await page.reload()
    await expect(page.getByTestId("pager-range")).toHaveText("1–17 of 17")
  })
})

test.describe("Terminal window", () => {
  test("cannot be dragged past the bottom of the page", async ({ page }) => {
    await page.goto("/hosts/1/terminal", { waitUntil: "domcontentloaded" })
    const win = page.getByTestId("terminal-window")
    await expect(win.getByRole("separator")).toBeVisible()
    await drag(page, win.getByRole("separator"), -200)
    await drag(page, win.getByRole("separator"), 3000)
    await expect(win.getByRole("separator")).toBeInViewport()
    const box = (await win.boundingBox())!
    expect(box.y + box.height).toBeLessThanOrEqual(page.viewportSize()!.height)
  })

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
