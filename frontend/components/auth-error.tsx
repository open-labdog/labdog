"use client"

import { forwardRef } from "react"
import { AlertCircle, WifiOff, ServerCrash } from "lucide-react"
import type { AuthErrorInfo } from "@/lib/auth-errors"

interface AuthErrorProps {
  error: AuthErrorInfo | null
  id?: string
}

export const AuthError = forwardRef<HTMLDivElement, AuthErrorProps>(
  function AuthError({ error, id = "auth-error" }, ref) {
    const Icon =
      error?.kind === "network"
        ? WifiOff
        : error?.kind === "server" || error?.kind === "unavailable"
          ? ServerCrash
          : AlertCircle

    return (
      <div
        ref={ref}
        id={id}
        role="alert"
        aria-live="assertive"
        aria-atomic="true"
        tabIndex={-1}
        className="focus-visible:outline-none"
      >
        {error && (
          <div className="flex items-start gap-2.5 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2.5 text-sm text-red-400">
            <Icon className="size-4 shrink-0 mt-0.5" aria-hidden="true" />
            <div className="flex-1">
              <p className="font-medium">{error.title}</p>
              <p className="text-red-400/80 mt-0.5">{error.body}</p>
            </div>
          </div>
        )}
      </div>
    )
  }
)
