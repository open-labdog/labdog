"use client"

import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { useAuth } from "@/lib/auth"
import { apiFetch } from "@/lib/api"
import { passwordChangeSchema, type PasswordChangeInput } from "@/lib/schemas"
import { showError } from "@/lib/toast"
import { Field, Modal } from "@/components/ld"

/**
 * Shown, and not dismissable, while an administrator-set password is
 * still in place. The backend refuses everything but this change until
 * it is made (403 `password_change_required`), so the rest of the app
 * would only show errors behind it.
 */
export function ForcedPasswordChange() {
  const { user, logout } = useAuth()
  const form = useForm<PasswordChangeInput>({
    resolver: zodResolver(passwordChangeSchema),
    defaultValues: { current_password: "", new_password: "", confirm_password: "" },
    mode: "onSubmit",
  })

  const onSubmit = form.handleSubmit(async (data) => {
    try {
      await apiFetch("/api/users/me", {
        method: "PATCH",
        json: { current_password: data.current_password, password: data.new_password },
      })
      // A password change ends every session, this one included; logout
      // clears what is cached and lands on the sign-in page.
      await logout()
    } catch (err) {
      showError(err instanceof Error ? err.message : "Failed to update password")
    }
  })

  if (!user?.must_change_password) return null

  return (
    <Modal
      open
      onClose={() => {}}
      onSubmit={onSubmit}
      title="Choose your own password"
      meta={user.email}
      w={420}
      footer={
        <>
          <span className="tt mr-auto">then sign in again</span>
          <button type="submit" className="btn btn-primary" disabled={form.formState.isSubmitting}>
            {form.formState.isSubmitting ? "Updating…" : "Set password"}
          </button>
        </>
      }
    >
      <p className="mb-3 text-xs text-text-2">
        An administrator set your current password. Replace it with one only you know before you continue.
      </p>
      <Field label="current password" htmlFor="forced-current" error={form.formState.errors.current_password?.message}>
        <input id="forced-current" type="password" className="inp mono" autoComplete="current-password" {...form.register("current_password")} />
      </Field>
      <Field label="new password" htmlFor="forced-new" error={form.formState.errors.new_password?.message}>
        <input id="forced-new" type="password" className="inp mono" autoComplete="new-password" {...form.register("new_password")} />
      </Field>
      <Field label="confirm new password" htmlFor="forced-confirm" error={form.formState.errors.confirm_password?.message}>
        <input id="forced-confirm" type="password" className="inp mono" autoComplete="new-password" {...form.register("confirm_password")} />
      </Field>
    </Modal>
  )
}
