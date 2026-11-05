import { test, expect } from "./fixtures"
import type { Page } from "@playwright/test"

// BUG-124: a session that had spent its token budget took a follow-up, was
// stopped at the model's first response, and replied with a summary. Nothing
// near the conversation said the question had not been worked on.

const REFUSAL =
  "This session has used up its token budget (136,162 of 100,000), so the assistant cannot answer another message here. " +
  "Raise the limit under Settings → AI to continue it, or start a new session."

const PROVIDER = {
  id: 2,
  name: "subscription",
  provider_type: "claude_agent",
  model: "claude",
  enabled: true,
  is_default: true,
  supports_tools: true,
}

const SESSION = {
  id: 39,
  provider_id: 2,
  mode: "chat",
  title: "Why did the upgrade fail?",
  mission: "Why did the upgrade fail?",
  autonomy_level: "full_auto",
  status: "succeeded",
  target_host_ids: [],
  skip_snapshots: false,
  action_run_id: null,
  iterations: 2,
  prompt_tokens: 128153,
  completion_tokens: 8009,
  cost: 0,
  cost_unknown: true,
  command_count: 8,
  report_markdown: "## Session summary",
  error_message: null,
  stopped_reason: "token budget (100000)",
  created_at: "2026-11-05T10:32:28Z",
  started_at: "2026-11-05T10:32:29Z",
  finished_at: "2026-11-05T10:38:53Z",
  messages: [
    { id: 1, seq: 1, role: "user", content: "Why did the upgrade fail?", tool_calls: null, tool_call_id: null, tokens: 0, created_at: "2026-11-05T10:32:28Z" },
    { id: 2, seq: 2, role: "assistant", content: "## Session summary", tool_calls: null, tool_call_id: null, tokens: 0, created_at: "2026-11-05T10:38:53Z" },
  ],
  tool_calls: [],
  approvals: [],
  rollbacks: [],
  rollback_targets: [],
  follow_up_refused: REFUSAL,
}

const json = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) })

async function open(page: Page, session: Record<string, unknown>) {
  await page.route("**/api/ai/providers**", (route) => route.fulfill(json([PROVIDER])))
  await page.route("**/api/ai/sessions", (route) => route.fulfill(json([session])))
  await page.route("**/api/ai/sessions/39", (route) => route.fulfill(json(session)))
  await page.goto("/assistant?session=39")
  await expect(page.getByLabel("follow-up message")).toBeVisible({ timeout: 15000 })
  await expect(page.getByText("Why did the upgrade fail?").first()).toBeVisible()
}

test.describe("A session that cannot take another message", () => {
  test("says which limit it reached, and locks the message box", async ({ page }) => {
    await open(page, SESSION)

    await expect(page.getByText(REFUSAL)).toBeVisible()
    await expect(page.getByRole("link", { name: "Settings → AI" })).toBeVisible()
    await expect(page.getByLabel("follow-up message")).toBeDisabled()
    await expect(page.getByRole("button", { name: "Send" })).toBeDisabled()
  })

  test("says its last message may not answer what was asked", async ({ page }) => {
    await open(page, SESSION)

    await expect(page.getByText(/stopped early: token budget \(100000\)\. Its last message/)).toBeVisible()
  })
})

test.describe("A session with room left", () => {
  test("takes a follow-up, and says nothing about limits", async ({ page }) => {
    await open(page, { ...SESSION, stopped_reason: null, follow_up_refused: null })

    await expect(page.getByLabel("follow-up message")).toBeEnabled()
    await expect(page.getByText(/stopped early/)).toHaveCount(0)
    await expect(page.getByText(/used up its/)).toHaveCount(0)
  })

  test("still warns after a stop that does not lock it", async ({ page }) => {
    await open(page, { ...SESSION, stopped_reason: "time limit (900s)", follow_up_refused: null })

    await expect(page.getByText(/stopped early: time limit \(900s\)\. Its last message/)).toBeVisible()
    await expect(page.getByLabel("follow-up message")).toBeEnabled()
  })
})
