'use client'

import { ReactNode, useState, useEffect, useCallback } from 'react'
import { ThemeProvider } from 'next-themes'
import { QueryClientProvider } from '@tanstack/react-query'
import { Toaster } from 'sonner'
import { AuthContext, User } from '@/lib/auth'
import { API_BASE } from '@/lib/api'
import { queryClient } from '@/lib/query-client'
import { AuthGuard } from '@/components/auth-guard'
import { SyncTrayProvider } from '@/lib/sync-tray'
import { SyncTray } from '@/components/sync-tray'

function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch(`${API_BASE}/api/users/me`, {
      credentials: 'include',
    })
      .then((res) => {
        if (res.ok) return res.json()
        return null
      })
      .then((data: User | null) => {
        setUser(data)
      })
      .catch(() => {
        setUser(null)
      })
      .finally(() => {
        setLoading(false)
      })
  }, [])

  const logout = useCallback(async () => {
    try {
      await fetch(`${API_BASE}/api/auth/jwt/logout`, {
        method: 'POST',
        credentials: 'include',
      })
    } finally {
      setUser(null)
      // Drop every cached response before leaving: without this the next
      // account to sign in on this browser renders the previous one's
      // data until each query refetches.
      queryClient.clear()
      // Deliberately a full page load rather than router.push. The cache
      // clear above covers TanStack Query, but a client-side navigation
      // would keep every component's local state — including anything a
      // signed-out user should no longer see — alive in the same tree.
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      window.location.href = '/login'
    }
  }, [])

  return (
    <AuthContext.Provider value={{ user, loading, logout }}>
      {children}
      <Toaster position="bottom-right" theme="dark" richColors closeButton />
    </AuthContext.Provider>
  )
}

export function Providers({ children }: { children: ReactNode }) {
  return (
    <ThemeProvider attribute="class" defaultTheme="dark" enableSystem>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <SyncTrayProvider>
            <AuthGuard>
              {children}
            </AuthGuard>
            <SyncTray />
          </SyncTrayProvider>
        </AuthProvider>
      </QueryClientProvider>
    </ThemeProvider>
  )
}
