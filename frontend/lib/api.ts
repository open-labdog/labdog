const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"

export async function apiFetch<T>(
  path: string,
  options?: RequestInit
): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  })
  if (!res.ok) {
    let detail = `API error ${res.status}`
    try {
      const body = await res.json()
      if (Array.isArray(body.detail)) {
        detail = body.detail.map((e: { msg: string }) => e.msg).join(", ")
      } else if (body.detail) {
        detail = body.detail
      }
    } catch {}
    throw new Error(detail)
  }
  if (res.status === 204) return undefined as T
  return res.json()
}
