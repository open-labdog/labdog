import { test, expect } from "./fixtures"

// BUG-75: nothing handled a 401 once the session cookie expired. Every
// query and mutation kept failing and the UI raised an error toast for
// each one, indefinitely, with no way back to the login page short of
// typing the URL.
//
// The lapse is simulated mid-session rather than across a reload: on a
// full page load the auth provider's own `/api/users/me` call already
// notices and the route guard redirects, so a reload would pass with or
// without the fix. What was missing is the case where the session dies
// while the app is running and only React Query notices.
test.describe("Expired session", () => {
  test("a 401 mid-session sends the browser to the login page", async ({ page }) => {
    await page.goto("/groups")
    await expect(page.getByRole("heading", { name: /Groups/i })).toBeVisible({ timeout: 15000 })

    // From here on, every authenticated call answers 401 — as they would
    // once the 24-hour cookie lapses.
    await page.route("**/api/**", (route) => {
      if (route.request().url().includes("/api/auth/")) return route.continue()
      return route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Unauthorized" }),
      })
    })

    // Client-side navigation: no document load, so nothing but the app's
    // own fetches can notice.
    await page.getByRole("link", { name: "Hosts", exact: true }).click()

    await expect(page).toHaveURL(/\/login/, { timeout: 15000 })
  })
})
