"use client"

import { useState } from "react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { cn } from "@/lib/utils"
import { useAuth } from "@/lib/auth"
import { API_BASE } from "@/lib/api"
import { passwordChangeSchema, type PasswordChangeInput } from "@/lib/schemas"
import { showSuccess, showError } from "@/lib/toast"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

export function Sidebar({ onNavigation }: { onNavigation?: () => void } = {}) {
  const pathname = usePathname()
  const { user, logout } = useAuth()

  const [passwordDialogOpen, setPasswordDialogOpen] = useState(false)

  const form = useForm<PasswordChangeInput>({
    resolver: zodResolver(passwordChangeSchema),
    defaultValues: { new_password: "", confirm_password: "" },
    mode: "onSubmit",
  })

  const navGroups: { label?: string; items: { href: string; label: string }[] }[] = [
    {
      items: [{ href: "/dashboard", label: "Dashboard" }],
    },
    {
      label: "MANAGE",
      items: [
        { href: "/hosts", label: "Hosts" },
        { href: "/groups", label: "Groups" },
        { href: "/schedules", label: "Schedules" },
      ],
    },
    {
      label: "CONFIG",
      items: [
        { href: "/ssh-keys", label: "SSH Keys" },
        { href: "/git-repos", label: "Git Repos" },
        { href: "/hypervisors", label: "Hypervisors" },
      ],
    },
    {
      label: "ADMIN",
      items: [
        ...(user?.is_superuser ? [{ href: "/users", label: "Users" }] : []),
        { href: "/audit", label: "Audit Log" },
        ...(user?.is_superuser ? [{ href: "/settings", label: "Settings" }] : []),
      ],
    },
  ]

  const onPasswordSubmit = form.handleSubmit(async (data) => {
    try {
      const res = await fetch(`${API_BASE}/api/users/me`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ password: data.new_password }),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => null)
        throw new Error(err?.detail || "Failed to update password")
      }
      form.reset()
      setPasswordDialogOpen(false)
      showSuccess("Password updated successfully")
    } catch (err) {
      showError(err instanceof Error ? err.message : "Failed to update password")
    }
  })

  return (
    <aside className="w-64 border-r border-slate-700 bg-slate-950 p-6 flex flex-col h-full">
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-white">Barricade</h1>
        <p className="text-sm text-slate-400">Firewall Management</p>
      </div>

      <nav className="space-y-4">
        {navGroups.map((group, gi) => (
          <div key={gi}>
            {group.label && (
              <p className="px-4 mb-1 text-[10px] font-semibold tracking-widest text-slate-500 uppercase">
                {group.label}
              </p>
            )}
            <div className="space-y-0.5">
              {group.items.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  onClick={onNavigation}
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
            </div>
          </div>
        ))}
      </nav>

      <div className="mt-auto border-t border-slate-700 pt-4 space-y-3">
        <div className="flex items-center justify-between">
          <div className="text-sm text-slate-300 truncate">{user?.email}</div>
          <Button
            variant="ghost"
            size="xs"
            onClick={() => { form.reset(); setPasswordDialogOpen(true) }}
            className="shrink-0"
          >
            Change Password
          </Button>
        </div>
        <Button size="sm" variant="destructive" className="w-full" onClick={logout}>
          Log Out
        </Button>
      </div>

      <Dialog open={passwordDialogOpen} onOpenChange={(open) => {
        setPasswordDialogOpen(open)
        if (!open) form.reset()
      }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Change Password</DialogTitle>
          </DialogHeader>
          <form onSubmit={onPasswordSubmit} className="space-y-4 mt-2">
            <div className="space-y-2">
              <Label htmlFor="new-password">New Password</Label>
              <Input id="new-password" type="password" {...form.register("new_password")} />
              {form.formState.errors.new_password?.message && <p className="text-sm text-red-400">{form.formState.errors.new_password.message}</p>}
            </div>
             <div className="space-y-2">
               <Label htmlFor="confirm-password">Confirm New Password</Label>
               <Input id="confirm-password" type="password" {...form.register("confirm_password")} />
               {form.formState.errors.confirm_password?.message && <p className="text-sm text-red-400">{form.formState.errors.confirm_password.message}</p>}
             </div>
             <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setPasswordDialogOpen(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={form.formState.isSubmitting}>
                {form.formState.isSubmitting ? "Updating..." : "Update Password"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </aside>
  )
}
