import { queryClient } from "@/lib/query-client"

export const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? ""

/** Paths whose own 401 is the answer, not an expired session. */
const _AUTH_PATH_PREFIX = "/api/auth/"

/** Pages that are already the place an expired session sends you. */
const _PUBLIC_PATHS = ["/login", "/register"]

/** One redirect per page load, however many requests fail at once. */
let _redirecting = false

/**
 * Handle a 401 on a request that expected to be authenticated.
 *
 * The session cookie lasts 24 hours. When it expired mid-session nothing
 * noticed: every query and mutation just kept failing, and the UI showed
 * an error toast for each one, indefinitely (BUG-75). Now the cache is
 * dropped — so the next user cannot momentarily see the last one's data —
 * and the browser goes to the login page, once.
 *
 * Exported for tests; `apiFetch` calls it for you.
 */
export function handleUnauthorized(): void {
  if (_redirecting || typeof window === "undefined") return
  if (_PUBLIC_PATHS.some((p) => window.location.pathname.startsWith(p))) return
  _redirecting = true
  queryClient.clear()
  window.location.replace("/login")
}

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.name = "ApiError"
    this.status = status
  }
}

const _MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"])

/**
 * Read the labdog_csrf double-submit cookie value from document.cookie.
 * Returns an empty string when the cookie is absent (e.g. not logged in).
 * The value is base64url-safe so no URL-decoding is needed.
 */
function _readCsrfCookie(): string {
  if (typeof document === "undefined") return ""
  const match = document.cookie
    .split(";")
    .map((c) => c.trim())
    .find((c) => c.startsWith("labdog_csrf="))
  return match ? match.slice("labdog_csrf=".length) : ""
}

export async function apiFetch<T>(
  path: string,
  options?: RequestInit & { json?: unknown }
): Promise<T> {
  const { json, ...fetchOptions } = options ?? {}
  const method = (fetchOptions.method ?? "GET").toUpperCase()

  const csrfHeaders: Record<string, string> = {}
  if (_MUTATING_METHODS.has(method)) {
    const csrfToken = _readCsrfCookie()
    if (csrfToken) {
      csrfHeaders["X-CSRF-Token"] = csrfToken
    }
  }

  const res = await fetch(`${API_BASE}${path}`, {
    ...fetchOptions,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...csrfHeaders,
      ...fetchOptions?.headers,
    },
    ...(json !== undefined ? { body: JSON.stringify(json) } : {}),
  })
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith(_AUTH_PATH_PREFIX)) {
      handleUnauthorized()
    }
    let detail = `API error ${res.status}`
    try {
      const body = await res.json()
      if (Array.isArray(body.detail)) {
        detail = body.detail
          .map((e: { msg: string; loc?: (string | number)[] }) =>
            e.loc && e.loc.length > 1 ? `${e.loc.slice(1).join(".")}: ${e.msg}` : e.msg
          )
          .join(", ")
      } else if (body.detail) {
        detail = body.detail
      }
    } catch {}
    throw new ApiError(detail, res.status)
  }
  if (res.status === 204 || res.headers.get("content-length") === "0") return undefined as T
  return res.json()
}
