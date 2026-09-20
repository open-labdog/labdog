"use client"

import { useEffect, useRef, useState } from "react"
import Link from "next/link"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { useAuth } from "@/lib/auth"
import { apiFetch } from "@/lib/api"
import { passwordChangeSchema, type PasswordChangeInput } from "@/lib/schemas"
import { showSuccess, showError } from "@/lib/toast"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

/**
 * The avatar at the foot of the rail. Account things — who you are,
 * changing your password, signing out — live here, where they cannot be
 * mistaken for navigation.
 */
export function AccountMenu({ compact }: { compact?: boolean }) {
  const { user, logout } = useAuth()
  const [open, setOpen] = useState(false)
  const [passwordDialogOpen, setPasswordDialogOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener("mousedown", h)
    return () => document.removeEventListener("mousedown", h)
  }, [open])

  const form = useForm<PasswordChangeInput>({
    resolver: zodResolver(passwordChangeSchema),
    defaultValues: { new_password: "", confirm_password: "" },
    mode: "onSubmit",
  })

  const onPasswordSubmit = form.handleSubmit(async (data) => {
    try {
      // Through apiFetch so the X-CSRF-Token header rides along —
      // /api/users/me is not in the CSRF middleware's exempt list.
      await apiFetch("/api/users/me", { method: "PATCH", json: { password: data.new_password } })
      form.reset()
      setPasswordDialogOpen(false)
      showSuccess("Password updated successfully")
    } catch (err) {
      showError(err instanceof Error ? err.message : "Failed to update password")
    }
  })

  const initial = (user?.email?.[0] ?? "?").toLowerCase()

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        title={user?.email ?? "Account"}
        aria-label="Account menu"
        aria-expanded={open}
        className="grid place-items-center rounded-full border border-line bg-surface-3 text-[9.5px] font-semibold text-text-2"
        style={{ width: compact ? 20 : 22, height: compact ? 20 : 22 }}
      >
        {initial}
      </button>
      {open && (
        <div
          role="menu"
          className="fade absolute z-50 flex w-[230px] flex-col gap-px rounded-r-lg border border-line-strong bg-surface p-1 shadow-ld"
          style={compact ? { bottom: "calc(100% + 8px)", right: 0 } : { bottom: 0, left: "calc(100% + 10px)" }}
        >
          <div className="px-2.5 pb-2 pt-1.5">
            <div className="tt">signed in as</div>
            <div className="mono trunc text-xs text-text">{user?.email}</div>
            {user?.is_superuser && <div className="tt mt-0.5 text-hold">superuser</div>}
          </div>
          <div className="mx-1 border-t border-line-faint" />
          <button
            role="menuitem"
            type="button"
            className="rounded-r px-2.5 py-1.5 text-left text-xs text-text-2 hover:bg-surface-2 hover:text-text"
            onClick={() => {
              setOpen(false)
              form.reset()
              setPasswordDialogOpen(true)
            }}
          >
            Change password…
          </button>
          <Link
            role="menuitem"
            href="/settings?section=system"
            onClick={() => setOpen(false)}
            className="rounded-r px-2.5 py-1.5 text-left text-xs text-text-2 hover:bg-surface-2 hover:text-text hover:no-underline"
          >
            About LabDog
          </Link>
          <div className="mx-1 border-t border-line-faint" />
          <button
            role="menuitem"
            type="button"
            className="rounded-r px-2.5 py-1.5 text-left text-xs text-danger hover:bg-danger-soft"
            onClick={() => {
              setOpen(false)
              void logout()
            }}
          >
            Log out
          </button>
        </div>
      )}

      <Dialog
        open={passwordDialogOpen}
        onOpenChange={(o) => {
          setPasswordDialogOpen(o)
          if (!o) form.reset()
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Change Password</DialogTitle>
          </DialogHeader>
          <form onSubmit={onPasswordSubmit} className="mt-2 space-y-4">
            <div className="space-y-2">
              <Label htmlFor="new-password">New Password</Label>
              <Input id="new-password" type="password" {...form.register("new_password")} />
              {form.formState.errors.new_password?.message && (
                <p className="text-sm text-danger">{form.formState.errors.new_password.message}</p>
              )}
            </div>
            <div className="space-y-2">
              <Label htmlFor="confirm-password">Confirm New Password</Label>
              <Input id="confirm-password" type="password" {...form.register("confirm_password")} />
              {form.formState.errors.confirm_password?.message && (
                <p className="text-sm text-danger">{form.formState.errors.confirm_password.message}</p>
              )}
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
    </div>
  )
}
