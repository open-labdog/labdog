'use client'

import { useEffect } from 'react'
import { usePathname } from 'next/navigation'
import { useAuth } from '@/lib/auth'

const PUBLIC_PATHS = ['/login', '/register']

export function AuthGuard({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const { user, loading } = useAuth()

  useEffect(() => {
    if (loading) return

    const isPublic = PUBLIC_PATHS.some(p => pathname.startsWith(p))

    if (!user && !isPublic) {
      window.location.replace('/login')
    }

    if (user && isPublic) {
      window.location.replace('/dashboard')
    }
  }, [user, loading, pathname])

  if (loading) return null

  return <>{children}</>
}
