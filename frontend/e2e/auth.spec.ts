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
    await expect(page.getByRole("link", { name: "Register" })).toBeVisible()
  })

  test("successful login redirects to /dashboard", async ({ page, request }) => {
    // Ensure test user exists
    await request.post(`${API_BASE}/auth/register`, {
      data: { email: TEST_EMAIL, password: TEST_PASSWORD },
    })

    await page.goto("/login")
    await page.locator("#email").fill(TEST_EMAIL)
    await page.locator("#password").fill(TEST_PASSWORD)
    await page.getByRole("button", { name: "Sign In" }).click()

    await page.waitForURL("**/dashboard")
    await expect(page).toHaveURL(/\/dashboard/)
  })

  test("invalid credentials shows error message", async ({ page }) => {
    await page.goto("/login")
    await page.locator("#email").fill("wrong@example.com")
    await page.locator("#password").fill("wrongpassword")
    await page.getByRole("button", { name: "Sign In" }).click()

    await expect(page.getByText("Invalid email or password")).toBeVisible()
    await expect(page).toHaveURL(/\/login/)
  })

  test("navigates to register page via link", async ({ page }) => {
    await page.goto("/login")
    await page.getByRole("link", { name: "Register" }).click()
    await expect(page).toHaveURL(/\/register/)
  })
})

test.describe("Register page", () => {
  test("renders register form correctly", async ({ page }) => {
    await page.goto("/register")
    await expect(page.getByRole("heading", { name: "Create Account" })).toBeVisible()
    await expect(page.locator("#email")).toBeVisible()
    await expect(page.locator("#password")).toBeVisible()
    await expect(page.locator("#confirmPassword")).toBeVisible()
    await expect(page.getByRole("button", { name: "Create Account" })).toBeVisible()
  })

  test("password mismatch shows error", async ({ page }) => {
    await page.goto("/register")
    await page.locator("#email").fill("newuser@example.com")
    await page.locator("#password").fill("Password123")
    await page.locator("#confirmPassword").fill("DifferentPass123")
    await page.getByRole("button", { name: "Create Account" }).click()

    await expect(page.getByText("Passwords do not match")).toBeVisible()
  })

  test("successful registration redirects to /login", async ({ page }) => {
    const uniqueEmail = `e2e-reg-${Date.now()}@barricade.io`
    await page.goto("/register")
    await page.locator("#email").fill(uniqueEmail)
    await page.locator("#password").fill("E2eTestPass1")
    await page.locator("#confirmPassword").fill("E2eTestPass1")
    await page.getByRole("button", { name: "Create Account" }).click()

    await page.waitForURL("**/login")
    await expect(page).toHaveURL(/\/login/)
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
  test("logout clears session and redirects to /login", async ({ page, request }) => {
    // Ensure test user exists
    await request.post(`${API_BASE}/auth/register`, {
      data: { email: TEST_EMAIL, password: TEST_PASSWORD },
    })

    // Login first
    await page.goto("/login")
    await page.locator("#email").fill(TEST_EMAIL)
    await page.locator("#password").fill(TEST_PASSWORD)
    await page.getByRole("button", { name: "Sign In" }).click()
    await page.waitForURL("**/dashboard")

    // Logout via API
    await page.request.post(`${API_BASE}/auth/jwt/logout`, {
      headers: { "Content-Type": "application/json" },
    })

    // Navigate to protected route — should redirect to login
    await page.goto("/dashboard")
    await expect(page).toHaveURL(/\/login/)
  })
})
