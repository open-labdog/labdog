"use client"

import { useState } from "react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { ChevronRightIcon } from "lucide-react"
import { Collapsible as CollapsiblePrimitive } from "@base-ui/react/collapsible"
import { cn } from "@/lib/utils"
import { useAuth } from "@/lib/auth"
import { API_BASE, apiFetch } from "@/lib/api"
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

type NavChild = { href: string; label: string }
type NavItem = { href: string; label: string; children?: NavChild[] }
type NavGroup = { label?: string; items: NavItem[] }

function CollapsibleNavItem({
  item,
  activeHref,
  pendingTotal,
  onNavigation,
  pathname,
}: {
  item: NavItem & { children: NavChild[] }
  activeHref: string
  pendingTotal: number
  onNavigation?: () => void
  pathname: string
}) {
  const childActive = item.children.some(
    (c) => pathname === c.href || pathname.startsWith(c.href + "/")
  )
  const [open, setOpen] = useState(childActive)

  return (
    <CollapsiblePrimitive.Root open={open} onOpenChange={setOpen}>
      <div className="flex items-center">
        <Link
          href={item.href}
          onClick={onNavigation}
          className={cn(
            "flex flex-1 items-center justify-between rounded-md px-4 py-2 text-sm font-medium transition-colors",
            item.href === activeHref
              ? "bg-slate-800 text-white"
              : "text-slate-300 hover:bg-slate-800 hover:text-white"
          )}
        >
          <span>{item.label}</span>
        </Link>
        <CollapsiblePrimitive.Trigger
          className="flex items-center justify-center rounded-md p-1.5 text-slate-500 hover:bg-slate-800 hover:text-slate-300 transition-colors shrink-0"
          aria-label={open ? "Collapse" : "Expand"}
        >
          <ChevronRightIcon
            className={cn(
              "w-3.5 h-3.5 transition-transform duration-150",
              open && "rotate-90"
            )}
          />
        </CollapsiblePrimitive.Trigger>
      </div>
      <CollapsiblePrimitive.Panel className="overflow-hidden">
        <div className="mt-0.5 space-y-0.5 pl-4">
          {item.children.map((child) => {
            const childPendingTotal = child.href === "/hosts/pending" ? pendingTotal : 0
            const childShowBadge = childPendingTotal > 0
            const childBadgeCount = childPendingTotal >= 100 ? "99+" : String(childPendingTotal)
            return (
              <Link
                key={child.href}
                href={child.href}
                onClick={onNavigation}
                className={cn(
                  "flex items-center justify-between rounded-md px-4 py-1.5 text-sm font-medium transition-colors",
                  child.href === activeHref
                    ? "bg-slate-800 text-white"
                    : "text-slate-400 hover:bg-slate-800 hover:text-white"
                )}
              >
                <span>{child.label}</span>
                <span className="flex items-center gap-1 w-10 justify-end shrink-0">
                  {childShowBadge && (
                    <>
                      <span className="inline-block w-1.5 h-1.5 rounded-full bg-amber-500 shrink-0" />
                      <span className="text-xs text-amber-500 tabular-nums">{childBadgeCount}</span>
                    </>
                  )}
                </span>
              </Link>
            )
          })}
        </div>
      </CollapsiblePrimitive.Panel>
    </CollapsiblePrimitive.Root>
  )
}

export function Sidebar({ onNavigation }: { onNavigation?: () => void } = {}) {
  const pathname = usePathname()
  const { user, logout } = useAuth()

  const [passwordDialogOpen, setPasswordDialogOpen] = useState(false)

  const { data: pendingSummary } = useQuery<{ total: number }>({
    queryKey: ["scans", "pending-summary"],
    queryFn: () => apiFetch<{ total: number }>("/api/scans/pending-summary"),
    refetchInterval: 30_000,
    retry: false,
  })

  const form = useForm<PasswordChangeInput>({
    resolver: zodResolver(passwordChangeSchema),
    defaultValues: { new_password: "", confirm_password: "" },
    mode: "onSubmit",
  })

  const pendingTotal = pendingSummary?.total ?? 0
  const hostsChildren: NavChild[] = [
    { href: "/hosts/discovery", label: "Discovery" },
    ...(pendingTotal > 0 ? [{ href: "/hosts/pending", label: "Pending" }] : []),
  ]

  const navGroups: NavGroup[] = [
    {
      items: [{ href: "/dashboard", label: "Dashboard" }],
    },
    {
      label: "MANAGE",
      items: [
        { href: "/hosts", label: "Hosts", children: hostsChildren },
        { href: "/groups", label: "Groups" },
        { href: "/schedules", label: "Update Workflows" },
      ],
    },
    {
      label: "INTEGRATIONS",
      items: [
        { href: "/ssh-keys", label: "SSH Keys" },
        { href: "/git-repos", label: "Git Repos" },
        { href: "/hypervisors", label: "Proxmox" },
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

  // Pick the nav item (or child) whose href is the longest prefix of the current pathname.
  // Prevents double-highlighting when nested routes (/hosts/discovery) share a prefix
  // with a parent item (/hosts).
  const allCandidates = navGroups.flatMap((g) =>
    g.items.flatMap((it) => [it, ...(it.children ?? [])])
  )
  const activeHref = allCandidates.reduce(
    (best, it) =>
      (pathname === it.href || pathname.startsWith(it.href + "/")) &&
      it.href.length > best.length
        ? it.href
        : best,
    "",
  )

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
        <h1 className="text-2xl font-bold text-white">LabDog</h1>
        <p className="text-xs italic text-slate-400">A homelabber&apos;s best friend</p>
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
              {group.items.map((item) => {
                if (item.children && item.children.length > 0) {
                  return (
                    <CollapsibleNavItem
                      key={item.href}
                      item={item as NavItem & { children: NavChild[] }}
                      activeHref={activeHref}
                      pendingTotal={item.href === "/hosts" ? pendingTotal : 0}
                      onNavigation={onNavigation}
                      pathname={pathname}
                    />
                  )
                }

                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    onClick={onNavigation}
                    className={cn(
                      "flex items-center rounded-md px-4 py-2 text-sm font-medium transition-colors",
                      item.href === activeHref
                        ? "bg-slate-800 text-white"
                        : "text-slate-300 hover:bg-slate-800 hover:text-white"
                    )}
                  >
                    <span>{item.label}</span>
                  </Link>
                )
              })}
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
