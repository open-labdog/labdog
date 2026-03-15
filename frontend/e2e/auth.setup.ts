import { test as setup, expect } from "@playwright/test"
import { execSync } from "child_process"
import path from "path"

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"
const AUTH_FILE = path.join(__dirname, "../playwright/.auth/user.json")

const TEST_EMAIL = process.env.TEST_USER_EMAIL || "e2e@barricade.io"
const TEST_PASSWORD = process.env.TEST_USER_PASSWORD || "E2eTestPass1"

setup("authenticate", async ({ page, request }) => {
  // Register test user (ignore 400 if already exists)
  await request.post(`${API_BASE}/auth/register`, {
    data: { email: TEST_EMAIL, password: TEST_PASSWORD },
  })

  // Promote to superuser so E2E tests can create/update/delete resources
  try {
    execSync(
      `docker exec barricade-postgres-1 psql -U barricade -d barricade -c "UPDATE users SET is_superuser = TRUE, is_verified = TRUE WHERE email = '${TEST_EMAIL}'"`,
      { stdio: "pipe" }
    )
  } catch {
    // If docker exec fails (e.g., not using docker), try alternative container name
    try {
      execSync(
        `docker exec postgres psql -U barricade -d barricade -c "UPDATE users SET is_superuser = TRUE, is_verified = TRUE WHERE email = '${TEST_EMAIL}'"`,
        { stdio: "pipe" }
      )
    } catch {
      console.warn("Could not promote test user to superuser via docker exec")
    }
  }

  // Login via UI
  await page.goto("/login")
  await page.locator("#email").fill(TEST_EMAIL)
  await page.locator("#password").fill(TEST_PASSWORD)
  await page.getByRole("button", { name: "Sign In" }).click()

  await page.waitForURL("**/dashboard")
  await expect(page).toHaveURL(/\/dashboard/)

  await page.context().storageState({ path: AUTH_FILE })
})
