/**
 * Shared Playwright fixtures.
 *
 * Specs must import `test` and `expect` from here rather than from
 * `@playwright/test` directly, because the bare `request` fixture cannot
 * make a mutating API call against this backend.
 *
 * Every POST/PUT/PATCH/DELETE outside a handful of auth paths goes through
 * `CSRFMiddleware`, which requires the `labdog_csrf` cookie *and* an
 * `X-CSRF-Token` header carrying the same value (double-submit). The
 * browser gets the header from `lib/api.ts`, which reads the cookie itself;
 * Playwright's `request` fixture carries the cookie from `storageState` but
 * sends no header, so every fixture-driven setup call would 403 — and the
 * specs read `.json()` off that 403 and carry on with an `undefined` id.
 *
 * The override below rebuilds `request` with the header attached, mirroring
 * `_CsrfAutoTransport` in `backend/tests/conftest.py`.
 */
import { test as base, expect } from "@playwright/test"
import fs from "fs"
import path from "path"

export const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"

const AUTH_FILE = path.join(__dirname, "../playwright/.auth/user.json")

/** Pull the double-submit token out of the saved storage state. */
function readCsrfToken(): string {
  try {
    const state = JSON.parse(fs.readFileSync(AUTH_FILE, "utf-8"))
    const cookie = (state.cookies ?? []).find(
      (c: { name: string }) => c.name === "labdog_csrf"
    )
    return cookie?.value ?? ""
  } catch {
    return ""
  }
}

export const test = base.extend({
  // The fixture callback is named `provide` rather than Playwright's usual
  // `use`: eslint's react-hooks/rules-of-hooks matches on the name alone and
  // reports `await use(...)` as a misplaced React hook.
  request: async ({ playwright }, provide) => {
    const context = await playwright.request.newContext({
      baseURL: API_BASE,
      storageState: AUTH_FILE,
      extraHTTPHeaders: { "X-CSRF-Token": readCsrfToken() },
    })
    await provide(context)
    await context.dispose()
  },
})

export { expect }
