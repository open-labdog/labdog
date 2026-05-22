export const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? ""

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
