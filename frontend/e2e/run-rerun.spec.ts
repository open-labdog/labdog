import { test, expect } from "./fixtures"
import { mockRun } from "./run-mock"

// "Re-run…" on a finished run opens the run dialog against the same target,
// filled in from the run's own settings, and every field stays editable.
// The catalog and the create call are mocked, so no worker or pack is needed.

const ACTION = {
  key: "linux-upgrade",
  name: "Linux upgrade",
  description: "apt/dnf upgrade",
  icon: "package",
  version: "1.1",
  estimated_duration: "5m",
  destructive: false,
  supports_group: true,
  supports_host: true,
  supports_fleet: false,
  parameters: [
    { key: "reboot", label: "Reboot if needed", type: "bool", default: false, required: false, choices: null, help_text: null },
    { key: "mode", label: "Mode", type: "choice", default: "safe", required: false, choices: ["safe", "full"], help_text: null },
    { key: "hold", label: "Hold packages", type: "string", default: null, required: false, choices: null, help_text: null },
  ],
  pack_name: "labdog-playbooks",
  winning_pack_id: 1,
  unresolved: false,
  overridden_from: [],
  post_run_sync: [],
  post_run_register: {},
  metrics_backend: null,
}

async function mockCreate(page: import("@playwright/test").Page) {
  const bodies: Record<string, unknown>[] = []
  await page.route(/\/api\/actions\/?$/, (r) => r.fulfill({ json: [ACTION] }))
  await page.route(/\/api\/actions\/runs\/?$/, (r) => {
    if (r.request().method() !== "POST") return r.fallback()
    bodies.push(r.request().postDataJSON())
    return r.fulfill({ status: 201, json: { id: 9400, status: "queued", host_runs: [], action_key: ACTION.key } })
  })
  return bodies
}

test.describe("Re-run an action", () => {
  test("same host and settings, edited before running", async ({ page }) => {
    const bodies = await mockCreate(page)
    await page.goto(
      await mockRun(page, {
        log: "ok",
        actionVersion: "1.1",
        parameters: { reboot: true, mode: "full", hold: "nginx", __dry_run: true },
      }),
    )

    await page.getByTestId("rerun-action").click()
    const dialog = page.getByRole("dialog")
    await expect(dialog).toContainText("Re-run Linux upgrade")
    await expect(dialog).toContainText("host: web1")
    await expect(dialog.getByTestId("rerun-note")).toContainText("#9301 (a dry-run preview)")
    await expect(dialog.locator("#param-reboot")).toBeChecked()
    await expect(dialog.locator("#param-mode")).toHaveValue("full")
    await expect(dialog.locator("#param-hold")).toHaveValue("nginx")

    await dialog.locator("#param-hold").fill("nginx,postgresql")
    await dialog.getByRole("button", { name: "Run", exact: true }).click()

    await expect(page).toHaveURL(/\/hosts\/8101\/actions\/runs\/9400\/?$/)
    expect(bodies).toEqual([
      {
        action_key: "linux-upgrade",
        parameters: { reboot: true, mode: "full", hold: "nginx,postgresql" },
        dry_run: false,
        host_id: 8101,
      },
    ])
  })

  test("a group run keeps its group and parallelism", async ({ page }) => {
    const bodies = await mockCreate(page)
    await page.goto(await mockRun(page, { log: "ok", hosts: 3, parallelism: 3, actionVersion: "1.1", parameters: { mode: "safe" } }))

    await page.getByTestId("rerun-action").click()
    const dialog = page.getByRole("dialog")
    await expect(dialog).toContainText("group: web")
    await expect(dialog.locator("#run-parallelism")).toHaveValue("3")
    await dialog.getByRole("button", { name: "Preview (dry-run)" }).click()

    await expect(page).toHaveURL(/\/groups\/7101\/actions\/runs\/9400\/?$/)
    expect(bodies[0]).toMatchObject({ group_id: 7101, parallelism: 3, dry_run: true, parameters: { mode: "safe" } })
  })

  test("says what changed when the action's manifest moved on", async ({ page }) => {
    await mockCreate(page)
    await page.goto(await mockRun(page, { log: "ok", actionVersion: "1.0", parameters: { mode: "full", legacy_flag: true } }))

    await page.getByTestId("rerun-action").click()
    const dialog = page.getByRole("dialog")
    await expect(dialog).toContainText("v1.0 → v1.1")
    await expect(dialog).toContainText("no longer takes legacy_flag")
    await expect(dialog.locator("#param-mode")).toHaveValue("full")
  })
})
