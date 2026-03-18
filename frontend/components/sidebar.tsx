"use client"

import { useState } from "react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { cn } from "@/lib/utils"
import { useAuth } from "@/lib/auth"
import { API_BASE } from "@/lib/api"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

export function Sidebar() {
  const pathname = usePathname()
  const { user, logout } = useAuth()

  const [passwordDialogOpen, setPasswordDialogOpen] = useState(false)
  const [newPassword, setNewPassword] = useState("")
  const [confirmPassword, setConfirmPassword] = useState("")
  const [passwordError, setPasswordError] = useState<string | null>(null)
  const [passwordSuccess, setPasswordSuccess] = useState<string | null>(null)
  const [passwordLoading, setPasswordLoading] = useState(false)

  const navItems = [
    { href: "/dashboard", label: "Dashboard" },
    { href: "/groups", label: "Groups" },
    { href: "/hosts", label: "Hosts" },
    { href: "/ssh-keys", label: "SSH Keys" },
    ...(user?.is_superuser ? [{ href: "/users", label: "Users" }] : []),
    { href: "/git-repos", label: "Git Repos" },
    { href: "/audit", label: "Audit Log" },
  ]

  async function handlePasswordChange(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setPasswordError(null)
    setPasswordSuccess(null)

    if (newPassword !== confirmPassword) {
      setPasswordError("Passwords do not match")
      return
    }

    setPasswordLoading(true)
    try {
      const res = await fetch(`${API_BASE}/api/users/me`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ password: newPassword }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => null)
        throw new Error(data?.detail || "Failed to update password")
      }
      setPasswordSuccess("Password updated successfully")
      setNewPassword("")
      setConfirmPassword("")
    } catch (err) {
      setPasswordError(err instanceof Error ? err.message : "Failed to update password")
    } finally {
      setPasswordLoading(false)
    }
  }

  return (
    <aside className="w-64 border-r border-slate-700 bg-slate-950 p-6 flex flex-col h-full">
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
              "block rounded-md px-4 py-2 text-sm font-medium transition-colors",
              (item.href === "/dashboard" ? pathname === "/dashboard" : pathname.startsWith(item.href))
                ? "bg-slate-800 text-white"
                : "text-slate-300 hover:bg-slate-800 hover:text-white"
            )}
          >
            {item.label}
          </Link>
        ))}
      </nav>

      <div className="mt-auto border-t border-slate-700 pt-4">
        <div className="text-sm text-slate-300 truncate">{user?.email}</div>
        <div className="flex gap-2 mt-2">
          <Button size="sm" variant="outline" onClick={() => {
            setPasswordError(null)
            setPasswordSuccess(null)
            setNewPassword("")
            setConfirmPassword("")
            setPasswordDialogOpen(true)
          }}>
            Change Password
          </Button>
          <Button size="sm" variant="ghost" onClick={logout}>
            Log Out
          </Button>
        </div>
      </div>

      <Dialog open={passwordDialogOpen} onOpenChange={(open) => {
        setPasswordDialogOpen(open)
        if (!open) {
          setPasswordError(null)
          setPasswordSuccess(null)
          setNewPassword("")
          setConfirmPassword("")
        }
      }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Change Password</DialogTitle>
          </DialogHeader>
          <form onSubmit={handlePasswordChange} className="space-y-4 mt-2">
            <div className="space-y-2">
              <Label htmlFor="new-password">New Password</Label>
              <Input id="new-password" type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} required />
            </div>
            <div className="space-y-2">
              <Label htmlFor="confirm-password">Confirm New Password</Label>
              <Input id="confirm-password" type="password" value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} required />
            </div>
            {passwordError && <p className="text-sm text-red-400">{passwordError}</p>}
            {passwordSuccess && <p className="text-sm text-green-400">{passwordSuccess}</p>}
            <div className="flex gap-3 pt-2">
              <Button type="submit" disabled={passwordLoading}>
                {passwordLoading ? "Updating..." : "Update Password"}
              </Button>
              <Button type="button" variant="outline" onClick={() => setPasswordDialogOpen(false)}>
                Cancel
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </aside>
  )
}
