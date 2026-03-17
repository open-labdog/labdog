'use client'

import { ReactNode, useState, useEffect, useCallback } from 'react'
import { ThemeProvider } from 'next-themes'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AuthContext, User } from '@/lib/auth'
import { API_BASE } from '@/lib/api'

const queryClient = new QueryClient()

function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch(`${API_BASE}/users/me`, {
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
      await fetch(`${API_BASE}/auth/jwt/logout`, {
        method: 'POST',
        credentials: 'include',
      })
    } finally {
      setUser(null)
      window.location.href = '/login'
    }
  }, [])

  return (
    <AuthContext.Provider value={{ user, loading, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function Providers({ children }: { children: ReactNode }) {
  return (
    <ThemeProvider attribute="class" defaultTheme="dark" enableSystem>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          {children}
        </AuthProvider>
      </QueryClientProvider>
    </ThemeProvider>
  )
}
