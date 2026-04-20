import { test as setup, expect } from "@playwright/test"
import { execSync } from "child_process"
import path from "path"

const REPO_ROOT = path.join(__dirname, "../..")
const VENV_PYTHON = path.join(REPO_ROOT, "backend/.venv/bin/python")
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
  throw new Error("Could not exec SQL — no postgres container reachable")
}

function hashPassword(plain: string): string {
  const script = `from fastapi_users.password import PasswordHelper; print(PasswordHelper().password_hash.hash(${JSON.stringify(plain)}))`
  const out = execSync(`${VENV_PYTHON} -c ${JSON.stringify(script)}`, {
    stdio: ["pipe", "pipe", "pipe"],
  })
  return out.toString().trim()
}

function upsertTestUser(email: string, plainPassword: string) {
  const hash = hashPassword(plainPassword).replace(/'/g, "''")
  const e = email.replace(/'/g, "''")
  dbExec(
    `INSERT INTO users (email, hashed_password, is_active, is_superuser, is_verified, created_at, updated_at)
     VALUES ('${e}', '${hash}', TRUE, TRUE, TRUE, NOW(), NOW())
     ON CONFLICT (email) DO UPDATE
       SET hashed_password = EXCLUDED.hashed_password,
           is_active = TRUE, is_superuser = TRUE, is_verified = TRUE,
           updated_at = NOW()`
  )
}

setup("authenticate", async ({ page }) => {
  upsertTestUser(TEST_EMAIL, TEST_PASSWORD)

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

  await page.waitForURL(/\/dashboard\/?$/)
  await expect(page).toHaveURL(/\/dashboard/)

  await page.context().storageState({ path: AUTH_FILE })
})
