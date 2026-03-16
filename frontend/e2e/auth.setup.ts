import { test as setup, expect } from "@playwright/test"
import { execSync } from "child_process"
import path from "path"

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"
const AUTH_FILE = path.join(__dirname, "../playwright/.auth/user.json")

const TEST_EMAIL = process.env.TEST_USER_EMAIL || "e2e@barricade.io"
const TEST_PASSWORD = process.env.TEST_USER_PASSWORD || "E2eTestPass1"

function dbExec(sql: string) {
  const containers = ["barricade-postgres-1", "postgres"]
  for (const container of containers) {
    try {
      execSync(
        `docker exec ${container} psql -U barricade -d barricade -c '${sql.replace(/'/g, "'\\''")}'`,
        { stdio: "pipe" }
      )
      return
    } catch {
      continue
    }
  }
}

setup("authenticate", async ({ page, request }) => {
  await request.post(`${API_BASE}/auth/register`, {
    data: { email: TEST_EMAIL, password: TEST_PASSWORD },
  })

  dbExec(`UPDATE users SET is_superuser = TRUE, is_verified = TRUE WHERE email = '${TEST_EMAIL}'`)

  // Clean up stale E2E data from previous runs to avoid 409 conflicts
  dbExec("DELETE FROM firewall_rules WHERE group_id IN (SELECT id FROM host_groups WHERE name LIKE 'e2e-%')")
  dbExec("DELETE FROM host_group_memberships WHERE group_id IN (SELECT id FROM host_groups WHERE name LIKE 'e2e-%')")
  dbExec("DELETE FROM host_group_memberships WHERE host_id IN (SELECT id FROM hosts WHERE hostname LIKE 'e2e-%')")
  dbExec("DELETE FROM sync_jobs WHERE group_id IN (SELECT id FROM host_groups WHERE name LIKE 'e2e-%')")
  dbExec("DELETE FROM host_groups WHERE name LIKE 'e2e-%'")
  dbExec("DELETE FROM hosts WHERE hostname LIKE 'e2e-%'")
  dbExec("DELETE FROM ssh_keys WHERE name LIKE 'e2e-%'")

  await page.goto("/login")
  await page.locator("#email").fill(TEST_EMAIL)
  await page.locator("#password").fill(TEST_PASSWORD)
  await page.getByRole("button", { name: "Sign In" }).click()

  await page.waitForURL("**/dashboard")
  await expect(page).toHaveURL(/\/dashboard/)

  await page.context().storageState({ path: AUTH_FILE })
})
