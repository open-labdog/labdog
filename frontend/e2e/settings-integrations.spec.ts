import { test, expect } from "./fixtures"
import type { Page } from "@playwright/test"

// Settings › Integrations: the Alert intake card says whether Grafana
// alerts can get in, and the Git remotes card how many repositories take
// push webhooks. They replaced a Webhooks card that listed four paths, never
// changed colour, and linked to the same page as Git remotes.

type Intake = {
  intake_enabled: boolean
  token_set: boolean
  token_long_enough_for_full_auto: boolean
  full_auto_alertnames: number
  poll_minutes: number
  last_webhook_alert_at: string | null
  last_poll_alert_at: string | null
}

const OFF: Intake = {
  intake_enabled: false,
  token_set: false,
  token_long_enough_for_full_auto: false,
  full_auto_alertnames: 0,
  poll_minutes: 0,
  last_webhook_alert_at: null,
  last_poll_alert_at: null,
}

async function open(page: Page, intake: Partial<Intake>) {
  await page.route(/\/api\/ai\/alert-intake\/?$/, (r) =>
    r.fulfill({ json: { ...OFF, webhook_path: "/api/webhooks/grafana-alerts", ...intake } }),
  )
  await page.goto("/settings")
  return page.locator("section").filter({ has: page.getByText("Alert intake", { exact: true }) })
}

test.describe("Alert intake card", () => {
  test("off", async ({ page }) => {
    const card = await open(page, {})
    await expect(card).toContainText("off")
    await expect(card).toContainText("intake off")
    await expect(card.getByRole("link", { name: "Configure…" })).toHaveAttribute("href", /\/settings\/?\?section=ai/)
    await expect(card.getByRole("link", { name: "Alerts" })).toHaveAttribute("href", /\/alerts\/?$/)
    await expect(page.getByText("Webhooks", { exact: true })).toHaveCount(0)
  })

  test("on, but no token: every webhook is refused", async ({ page }) => {
    const card = await open(page, { intake_enabled: true })
    await expect(card).toContainText("needs attention")
    await expect(card).toContainText("the webhook refuses every alert: no token")
    await expect(card).toContainText("set [alerts] webhook_token in labdog.toml")
  })

  test("on, with a token: the URL for Grafana and the last alert", async ({ page }) => {
    const last = new Date(Date.now() - 3 * 3600_000).toISOString()
    const card = await open(page, { intake_enabled: true, token_set: true, token_long_enough_for_full_auto: true, last_webhook_alert_at: last })
    await expect(card).toContainText("connected")
    await expect(card).toContainText(/POST https?:\/\/[^ ]+\/api\/webhooks\/grafana-alerts/)
    await expect(card).toContainText("last alert 3h ago")
  })

  test("a short token with alerts on the full-auto list", async ({ page }) => {
    const card = await open(page, { intake_enabled: true, token_set: true, full_auto_alertnames: 2 })
    await expect(card).toContainText("needs attention")
    await expect(card).toContainText("token under 32 characters: no full auto")
  })
})

test("Git remotes counts the repositories that take push webhooks", async ({ page }) => {
  const repo = (id: number, name: string, secret: boolean) => ({
    id,
    name,
    url: `https://example.com/${name}.git`,
    branch: "main",
    auth_type: "none",
    ssh_key_id: null,
    has_webhook_secret: secret,
    last_commit_sha: "a".repeat(40),
    last_sync_at: new Date().toISOString(),
    has_pinned_host_key: false,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
  })
  await page.route(/\/api\/git-repos\/?$/, (r) => r.fulfill({ json: [repo(1, "one", true), repo(2, "two", false)] }))
  await page.goto("/settings")
  const card = page.locator("section").filter({ has: page.getByText("Git remotes", { exact: true }) })
  await expect(card).toContainText("push webhooks on 1 of 2")
})
