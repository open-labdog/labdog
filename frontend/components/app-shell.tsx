"use client"

import { useState } from "react"
import { usePathname } from "next/navigation"
import { MenuIcon } from "lucide-react"
import { Sidebar } from "@/components/sidebar"
import { CommandPalette } from "@/components/command-palette"

const AUTH_ROUTES = ["/login", "/register"]

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const isAuthPage = AUTH_ROUTES.some((r) => pathname.startsWith(r))

  if (isAuthPage) {
    return <>{children}</>
  }

  return (
    <>
      <div className="flex h-screen">
        <div className="hidden md:block">
          <Sidebar />
        </div>

        <div className="flex flex-col flex-1 min-w-0">
          <div className="md:hidden flex items-center justify-between px-4 py-3 border-b border-slate-700 bg-slate-950">
            <button
              onClick={() => setSidebarOpen(true)}
              className="text-slate-300 hover:text-white"
              aria-label="Open menu"
            >
              <MenuIcon className="w-6 h-6" />
            </button>
            <span className="text-white font-bold">Barricade</span>
            {/* width spacer to center title */}
            <div className="w-6" />
          </div>

          <main className="flex-1 overflow-auto p-6">{children}</main>
        </div>
      </div>

      {sidebarOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/50 md:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      <div
        className={`fixed inset-y-0 left-0 z-50 md:hidden transition-transform duration-200 ${
          sidebarOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <Sidebar onNavigation={() => setSidebarOpen(false)} />
      </div>

      <CommandPalette />
    </>
  )
}
