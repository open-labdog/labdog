import { test, expect } from "@playwright/test"

// BUG-75: nothing handled a 401 once the session cookie expired. Every
// query and mutation kept failing and the UI showed an error toast for
// each one, indefinitely, with no way back to the login page short of
// typing the URL.
test.describe("Expired session", () => {
  test("a 401 sends the browser to the login page once", async ({ page }) => {
    await page.goto("/hosts")
    await expect(page).toHaveURL(/\/hosts/)

    // Every authenticated call now answers 401, as it would once the
    // 24-hour cookie lapses mid-session.
    await page.route("**/api/**", (route) => {
      if (route.request().url().includes("/api/auth/")) return route.continue()
      return route.fulfill({ status: 401, body: JSON.stringify({ detail: "Unauthorized" }) })
    })

    await page.reload()

    await expect(page).toHaveURL(/\/login/, { timeout: 15000 })
  })
})
