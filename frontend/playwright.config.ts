import { defineConfig, devices } from "@playwright/test"

/**
 * Where the app is being served.
 *
 * Locally this is `next dev` on :3000, started by the `webServer` block
 * below and talking to a separately-run backend on :8000. In CI the
 * static export is served by the backend itself on :8000 — the same
 * single-origin topology a real install runs, which also means the
 * session cookie and the CSRF double-submit behave as they do in
 * production rather than cross-origin.
 */
const BASE_URL = process.env.PLAYWRIGHT_BASE_URL || "http://localhost:3000"

/** Nothing to start when the caller already brought the stack up. */
const MANAGED_SERVER = !process.env.PLAYWRIGHT_BASE_URL

export default defineConfig({
  testDir: "./e2e",
  timeout: 30000,
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "html",
  use: {
    baseURL: BASE_URL,
    screenshot: "only-on-failure",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "setup",
      testMatch: /auth\.setup\.ts/,
    },
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        storageState: "playwright/.auth/user.json",
      },
      dependencies: ["setup"],
      testIgnore: /auth\.setup\.ts/,
    },
  ],
  ...(MANAGED_SERVER
    ? {
        webServer: {
          command: "npm run dev",
          url: "http://localhost:3000",
          reuseExistingServer: !process.env.CI,
          timeout: 120000,
        },
      }
    : {}),
})
