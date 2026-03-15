import { test as setup, expect } from "@playwright/test"
import path from "path"

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"
const AUTH_FILE = path.join(__dirname, "../playwright/.auth/user.json")

const TEST_EMAIL = process.env.TEST_USER_EMAIL || "e2e@barricade.test"
const TEST_PASSWORD = process.env.TEST_USER_PASSWORD || "E2eTestPass1"

setup("authenticate", async ({ page, request }) => {
  // Register test user (ignore 400 if already exists)
  await request.post(`${API_BASE}/auth/register`, {
    data: { email: TEST_EMAIL, password: TEST_PASSWORD },
  })

  // Login via UI
  await page.goto("/login")
  await page.locator("#email").fill(TEST_EMAIL)
  await page.locator("#password").fill(TEST_PASSWORD)
  await page.getByRole("button", { name: "Sign In" }).click()

  await page.waitForURL("**/dashboard")
  await expect(page).toHaveURL(/\/dashboard/)

  await page.context().storageState({ path: AUTH_FILE })
})
