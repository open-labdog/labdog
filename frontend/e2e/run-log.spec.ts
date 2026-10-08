import fs from "fs"
import path from "path"
import type { Locator, Page } from "@playwright/test"
import { test, expect } from "./fixtures"
import { mockRun } from "./run-mock"

// The run log colours Ansible's plain-text output by what each line says,
// and can be searched, wrapped and jumped to its first failure — without
// changing the text a copy of it produces. The sample is a real
// linux-upgrade whose verify playbook failed and was rolled back.

const LOG = fs.readFileSync(path.join(__dirname, "ansible-log-sample.txt"), "utf8")

/** The colour a CSS variable resolves to, as getComputedStyle reports it. */
const resolved = (page: Page, cssVar: string) =>
  page.evaluate((v) => {
    const el = document.createElement("span")
    el.style.color = `var(${v})`
    document.body.appendChild(el)
    const c = getComputedStyle(el).color
    el.remove()
    return c
  }, cssVar)

const colour = (l: Locator) => l.evaluate((el) => getComputedStyle(el).color)

/** Whether `el` is inside the visible part of the log. */
async function inView(log: Locator, el: Locator): Promise<boolean> {
  const b = (await log.boundingBox())!
  const a = (await el.boundingBox())!
  return a.y >= b.y && a.y + a.height <= b.y + b.height
}

test.describe("Run log", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(await mockRun(page, { log: LOG, status: "failed" }))
    await expect(page.getByTestId("run-log")).toContainText("[cleanup] snapshot")
  })

  test("lines are coloured by what they say", async ({ page }) => {
    const log = page.getByTestId("run-log")
    const danger = await resolved(page, "--danger")
    expect(await colour(log.locator('[data-kind="fatal"]').first())).toBe(danger)
    expect(await colour(log.locator('[data-kind="error"]').first())).toBe(danger)
    expect(await colour(log.locator('[data-kind="labdog-failed"]').first())).toBe(danger)
    expect(await colour(log.locator('[data-kind="changed"]').first())).toBe(await resolved(page, "--warn"))
    expect(await colour(log.locator('[data-kind="ok"]').first())).toBe(await resolved(page, "--ok"))
    // The second recap (the verify playbook's) has failed=1.
    const recap = log.locator('[data-kind="recap-row"]').nth(1)
    expect(await colour(recap.getByText("failed=1"))).toBe(danger)
    // A fatal result's JSON body is painted with it.
    await expect(log.locator('[data-kind="fatal"]')).toHaveCount(6)
  })

  test("copying the log gives back the text unchanged", async ({ page }) => {
    const log = page.getByTestId("run-log")
    expect(await log.evaluate((pre) => pre.textContent)).toBe(LOG)
    // What a copy produces. Chromium's selection drops trailing whitespace
    // (spaces at the end of a wrapped line, the final newline) from a
    // plain <pre> as much as from this one, so that is not compared.
    const copied = await log.evaluate((pre) => {
      const sel = window.getSelection()!
      sel.selectAllChildren(pre)
      return sel.toString()
    })
    const lines = (t: string) => t.trimEnd().split("\n").map((l) => l.trimEnd())
    expect(lines(copied)).toEqual(lines(LOG))
  })

  test("jump to the first failure", async ({ page }) => {
    const log = page.getByTestId("run-log")
    await page.getByRole("button", { name: "first of 3 failures ↓" }).click()
    await expect(page.getByLabel("pin to bottom")).not.toBeChecked()
    expect(await inView(log, log.locator('[data-kind="labdog-failed"]').first())).toBe(true)
  })

  test("search counts matches and steps through them", async ({ page }) => {
    const log = page.getByTestId("run-log")
    const box = page.getByLabel("Search the log")
    await box.fill("LABDOG-242")
    const count = page.getByTestId("search-count")
    await expect(count).toHaveText("1/3")
    await expect(log.locator("mark")).toHaveCount(3)
    await box.press("Enter")
    await expect(count).toHaveText("2/3")
    await box.press("Shift+Enter")
    await box.press("Shift+Enter")
    await expect(count).toHaveText("3/3")
    expect(await inView(log, log.locator('mark[data-match="2"]'))).toBe(true)
    // Esc clears the search before anything else.
    await box.press("Escape")
    await expect(log.locator("mark")).toHaveCount(0)
  })

  test("wrap is a toggle, remembered", async ({ page }) => {
    const log = page.getByTestId("run-log")
    await expect(log).toHaveCSS("white-space", "pre-wrap")
    await page.getByLabel("wrap").uncheck()
    await expect(log).toHaveCSS("white-space", "pre")
    await page.reload()
    await expect(page.getByTestId("run-log")).toHaveCSS("white-space", "pre")
  })
})
