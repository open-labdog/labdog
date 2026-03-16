'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { cn } from '@/lib/utils'

const navItems = [
  { href: '/dashboard', label: 'Dashboard' },
  { href: '/groups', label: 'Groups' },
  { href: '/hosts', label: 'Hosts' },
  { href: '/ssh-keys', label: 'SSH Keys' },
  { href: '/git-repos', label: 'Git Repos' },
  { href: '/audit', label: 'Audit Log' },
]

export function Sidebar() {
  const pathname = usePathname()

  return (
    <aside className="w-64 border-r border-slate-700 bg-slate-950 p-6">
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-white">Barricade</h1>
        <p className="text-sm text-slate-400">Firewall Management</p>
      </div>

      <nav className="space-y-2">
        {navItems.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className={cn(
              'block rounded-md px-4 py-2 text-sm font-medium transition-colors',
              pathname === item.href
                ? 'bg-slate-800 text-white'
                : 'text-slate-300 hover:bg-slate-800 hover:text-white'
            )}
          >
            {item.label}
          </Link>
        ))}
      </nav>
    </aside>
  )
}
