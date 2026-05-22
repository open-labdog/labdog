"use client"

import { useEffect } from "react"

import { API_BASE } from "@/lib/api"

export function CsrfGuard() {
  useEffect(() => {
    const hasCsrf = document.cookie
      .split(";")
      .some((c) => c.trim().startsWith("labdog_csrf="))
    if (!hasCsrf) {
      fetch(`${API_BASE}/api/auth/csrf-token`, { credentials: "include" }).catch(() => {})
    }
  }, [])

  return null
}
