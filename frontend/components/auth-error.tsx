"use client"

import { forwardRef } from "react"
import { Banner } from "@/components/ld"
import type { AuthErrorInfo } from "@/lib/auth-errors"

interface AuthErrorProps {
  error: AuthErrorInfo | null
  id?: string
}

/**
 * The sign-in and setup forms' error. The wrapper is the live region and
 * the focus target, and it is always in the DOM — a region that appears
 * with its first message is one some screen readers never announce — so
 * the banner inside it gives up its own alert role.
 */
export const AuthError = forwardRef<HTMLDivElement, AuthErrorProps>(function AuthError({ error, id = "auth-error" }, ref) {
  return (
    <div ref={ref} id={id} role="alert" aria-live="assertive" aria-atomic="true" tabIndex={-1} className="outline-none">
      {error && (
        <Banner tone="danger" role={null}>
          <span className="block font-semibold">{error.title}</span>
          <span className="mt-0.5 block text-text-2">{error.body}</span>
        </Banner>
      )}
    </div>
  )
})
