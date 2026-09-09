/**
 * Provision the e2e account and clear leftover fixtures, then save the
 * logged-in browser state for every other spec to reuse.
 *
 * Everything here goes over HTTP. The previous version shelled out to
 * `docker exec` against three guessed container names and to
 * `backend/.venv/bin/python` for a password hash, which meant the suite
 * could only run on a machine laid out exactly like the author's — and
 * not on a CI runner, where the database is a service container and there
 * is no venv. That is a large part of why nothing ran these specs.
 */
import { test as setup, expect, type APIRequestContext } from "@playwright/test"
import path from "path"

const AUTH_FILE = path.join(__dirname, "../playwright/.auth/user.json")
const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"

const TEST_EMAIL = process.env.TEST_USER_EMAIL || "e2e@labdog.io"
const TEST_PASSWORD = process.env.TEST_USER_PASSWORD || "E2eTestPass1"

/** Prefix every fixture this suite creates carries, so cleanup can find it. */
const FIXTURE_PREFIX = "e2e-"

/**
 * Create the account when the instance has no users yet.
 *
 * `POST /api/auth/register` is only open while the user count is zero —
 * it is the first-run setup route, not an open registration endpoint — so
 * on a fresh CI database this succeeds and on a populated dev database it
 * is correctly refused. Either way the login below is what decides.
 */
async function registerIfFirstRun(request: APIRequestContext) {
  const status = await request.get(`${API_BASE}/api/auth/setup-status`)
  if (!status.ok()) return
  const { needs_setup: needsSetup } = await status.json()
  if (!needsSetup) return
  await request.post(`${API_BASE}/api/auth/register`, {
    data: { email: TEST_EMAIL, password: TEST_PASSWORD },
  })
}

/**
 * Delete anything left behind by an earlier run.
 *
 * Specs create groups, hosts and SSH keys with unique names but a shared
 * `e2e-` prefix; without this a re-run collides on the unique constraints
 * (and, since BUG-69, on `host_groups.priority`). Hosts go before SSH keys
 * because a host references its key.
 */
async function deleteFixtures(request: APIRequestContext, csrf: string) {
  const headers = { "X-CSRF-Token": csrf }
  const sweep = async (collection: string, nameKey: string) => {
    const res = await request.get(`${API_BASE}/api/${collection}`)
    if (!res.ok()) return
    const rows: Array<Record<string, unknown>> = await res.json()
    for (const row of rows) {
      if (String(row[nameKey] ?? "").startsWith(FIXTURE_PREFIX)) {
        await request.delete(`${API_BASE}/api/${collection}/${row.id}`, { headers })
      }
    }
  }
  await sweep("hosts", "hostname")
  await sweep("groups", "name")
  await sweep("ssh-keys", "name")
}

setup("authenticate", async ({ page, request }) => {
  await registerIfFirstRun(request)

  await page.goto("/login")
  await page.locator("#email").fill(TEST_EMAIL)
  await page.locator("#password").fill(TEST_PASSWORD)
  await page.getByRole("button", { name: "Sign In" }).click()

  await page.waitForURL(/\/dashboard\/?$/, { timeout: 15000 })
  await expect(page).toHaveURL(/\/dashboard/)

  const cookies = await page.context().cookies()
  const csrf = cookies.find((c) => c.name === "labdog_csrf")?.value ?? ""
  expect(csrf, "login should set the labdog_csrf cookie").not.toBe("")

  await deleteFixtures(page.request, csrf)

  await page.context().storageState({ path: AUTH_FILE })
})
