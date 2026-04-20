import { test, expect } from "@playwright/test"

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"
const TEST_EMAIL = process.env.TEST_USER_EMAIL || "e2e@barricade.io"
const TEST_PASSWORD = process.env.TEST_USER_PASSWORD || "E2eTestPass1"

// These tests run WITHOUT auth state (unauthenticated)
test.use({ storageState: { cookies: [], origins: [] } })

test.describe("Login page", () => {
  test("renders login form correctly", async ({ page }) => {
    await page.goto("/login")
    await expect(page.getByRole("heading", { name: "Barricade" })).toBeVisible()
    await expect(page.locator("#email")).toBeVisible()
    await expect(page.locator("#password")).toBeVisible()
    await expect(page.getByRole("button", { name: "Sign In" })).toBeVisible()
  })

  test("successful login redirects to /dashboard", async ({ page }) => {
    // Test user is seeded by auth.setup.ts (runs via project dependency)
    await page.goto("/login")
    await page.locator("#email").fill(TEST_EMAIL)
    await page.locator("#password").fill(TEST_PASSWORD)
    await page.getByRole("button", { name: "Sign In" }).click()

    await page.waitForURL(/\/dashboard\/?$/)
    await expect(page).toHaveURL(/\/dashboard/)
  })

  test("invalid credentials shows error message", async ({ page }) => {
    await page.goto("/login")
    await page.locator("#email").fill("wrong@example.com")
    await page.locator("#password").fill("wrongpassword")
    await page.getByRole("button", { name: "Sign In" }).click()

    await expect(page.getByText("Incorrect email or password")).toBeVisible()
    await expect(page.locator("#email")).toHaveAttribute("aria-invalid", "true")
    await expect(page).toHaveURL(/\/login/)
  })
})

test.describe("Register page", () => {
  test("shows registration closed after initial setup", async ({ page }) => {
    await page.goto("/register")
    await expect(
      page.getByRole("heading", { name: "Registration Closed" })
    ).toBeVisible()
    await expect(page.getByRole("link", { name: "Back to sign in" })).toBeVisible()
  })
})

test.describe("Auth guards", () => {
  test("unauthenticated access to /dashboard redirects to /login", async ({ page }) => {
    await page.goto("/dashboard")
    await expect(page).toHaveURL(/\/login/)
  })

  test("unauthenticated access to /groups redirects to /login", async ({ page }) => {
    await page.goto("/groups")
    await expect(page).toHaveURL(/\/login/)
  })
})

test.describe("Logout", () => {
  test("logout clears session and redirects to /login", async ({ page }) => {
    // Test user is seeded by auth.setup.ts
    await page.goto("/login")
    await page.locator("#email").fill(TEST_EMAIL)
    await page.locator("#password").fill(TEST_PASSWORD)
    await page.getByRole("button", { name: "Sign In" }).click()
    await page.waitForURL(/\/dashboard\/?$/)

    await page.request.post(`${API_BASE}/api/auth/jwt/logout`, {
      headers: { "Content-Type": "application/json" },
    })

    await page.goto("/dashboard")
    await expect(page).toHaveURL(/\/login/)
  })
})
