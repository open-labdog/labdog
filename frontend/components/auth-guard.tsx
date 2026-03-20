'use client'

import { useEffect, useState } from 'react'
import { usePathname } from 'next/navigation'

const PUBLIC_PATHS = ['/login', '/register']

export function AuthGuard({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const [checked, setChecked] = useState(false)

  useEffect(() => {
    const hasAuth = document.cookie.split(';').some(c => c.trim().startsWith('barricade_auth='))
    const isPublic = PUBLIC_PATHS.some(p => pathname.startsWith(p))

    if (!hasAuth && !isPublic) {
      window.location.replace('/login')
      return
    }

    if (hasAuth && isPublic) {
      window.location.replace('/dashboard')
      return
    }

    setChecked(true)
  }, [pathname])

  if (!checked) return null

  return <>{children}</>
}
