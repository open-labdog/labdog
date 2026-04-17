export const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? ""

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.name = "ApiError"
    this.status = status
  }
}

export async function apiFetch<T>(
  path: string,
  options?: RequestInit & { json?: unknown }
): Promise<T> {
  const { json, ...fetchOptions } = options ?? {}
  const res = await fetch(`${API_BASE}${path}`, {
    ...fetchOptions,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
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
