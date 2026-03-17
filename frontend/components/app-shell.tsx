"use client"

import { usePathname } from "next/navigation"
import { Sidebar } from "@/components/sidebar"

const AUTH_ROUTES = ["/login", "/register"]

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const isAuthPage = AUTH_ROUTES.some((r) => pathname.startsWith(r))

  if (isAuthPage) {
    return <>{children}</>
  }

  return (
    <div className="flex h-screen">
      <Sidebar />
      <main className="flex-1 overflow-auto p-6">{children}</main>
    </div>
  )
}
