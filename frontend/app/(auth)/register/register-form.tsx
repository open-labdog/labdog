"use client"

import { useState, useEffect, useRef, FormEvent } from "react"
import Link from "next/link"
import { AuthCard, AuthChecking, AuthHeading } from "@/components/auth-background"
import { AuthError } from "@/components/auth-error"
import { Field } from "@/components/ld"
import { classifyAuthError, type AuthErrorInfo } from "@/lib/auth-errors"
import { API_BASE } from "@/lib/api"

export function RegisterForm() {
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [confirmPassword, setConfirmPassword] = useState("")
  const [showPassword, setShowPassword] = useState(false)
  const [authError, setAuthError] = useState<AuthErrorInfo | null>(null)
  const [loading, setLoading] = useState(false)
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null)
  const errorRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    fetch(`${API_BASE}/api/auth/setup-status`, { credentials: "include" })
      .then((res) => res.json())
      .then((data) => setNeedsSetup(data.needs_setup === true))
      .catch(() => setNeedsSetup(false))
  }, [])

  useEffect(() => {
    if (authError) errorRef.current?.focus()
  }, [authError])

  function clearErrorOnInput() {
    if (authError) setAuthError(null)
  }

  if (needsSetup === null) {
    return (
      <AuthCard>
        <AuthChecking />
      </AuthCard>
    )
  }

  if (!needsSetup) {
    return (
      <AuthCard>
        <div className="flex flex-col gap-5 p-7">
          <AuthHeading title="Registration Closed" sub="Registration is closed. Contact your administrator to get an account." />
          <Link href="/login" className="btn w-full justify-center py-[7px] hover:no-underline">
            Back to sign in
          </Link>
        </div>
      </AuthCard>
    )
  }

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (loading) return
    setAuthError(null)

    if (password.length < 8) {
      setAuthError({
        kind: "credentials",
        title: "Password too short",
        body: "Password must be at least 8 characters.",
        fieldLevel: true,
      })
      return
    }

    if (password !== confirmPassword) {
      setAuthError({
        kind: "credentials",
        title: "Passwords do not match",
        body: "Make sure both password fields are identical.",
        fieldLevel: true,
      })
      return
    }

    setLoading(true)
    try {
      const res = await fetch(`${API_BASE}/api/auth/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ email, password }),
      })

      if (res.ok) {
        // Full page load so the login page re-reads /api/auth/setup-status
        // and stops offering registration, which is now closed.
        // eslint-disable-next-line @next/next/no-location-assign-relative-destination
        window.location.href = "/login"
        return
      }

      const data = await res.json().catch(() => null)
      const detail = data?.detail
      const body = Array.isArray(detail)
        ? detail
            .map((e: { msg: string; loc?: (string | number)[] }) =>
              e.loc && e.loc.length > 1 ? `${e.loc.slice(1).join(".")}: ${e.msg}` : e.msg
            )
            .join(", ")
        : typeof detail === "string"
          ? detail
          : null

      if (body) {
        setAuthError({
          kind: "credentials",
          title: "Registration failed",
          body,
          fieldLevel: true,
        })
      } else {
        setAuthError(classifyAuthError(res.status))
      }
    } catch {
      setAuthError(classifyAuthError(null))
    } finally {
      setLoading(false)
    }
  }

  const fieldInvalid = authError?.fieldLevel ? true : undefined

  return (
    <AuthCard>
      <div className="flex flex-col gap-5 p-7">
        <AuthHeading title="Welcome to LabDog" sub="This is a fresh instance. Create the admin account to get started." />

        <form onSubmit={handleSubmit} action="" method="post" className="flex flex-col gap-3" aria-busy={loading} noValidate>
          <AuthError ref={errorRef} error={authError} id="register-error" />

          <Field label="email" htmlFor="email">
            <input
              id="email"
              name="email"
              type="email"
              inputMode="email"
              className="inp"
              placeholder="admin@example.com"
              value={email}
              onChange={(e) => {
                setEmail(e.target.value)
                clearErrorOnInput()
              }}
              required
              autoComplete="email"
              aria-invalid={fieldInvalid}
              aria-describedby={authError ? "register-error" : undefined}
            />
          </Field>

          <Field label="password" htmlFor="password" hint="at least 8 characters">
            <span className="relative block">
              <input
                id="password"
                name="password"
                type={showPassword ? "text" : "password"}
                className="inp pr-14"
                value={password}
                onChange={(e) => {
                  setPassword(e.target.value)
                  clearErrorOnInput()
                }}
                required
                autoComplete="new-password"
                aria-invalid={fieldInvalid}
                aria-describedby={authError ? "register-error" : undefined}
              />
              <button
                type="button"
                onClick={() => setShowPassword((v) => !v)}
                aria-label={showPassword ? "Hide password" : "Show password"}
                aria-pressed={showPassword}
                className="btn btn-sm btn-ghost absolute right-1 top-1/2 -translate-y-1/2"
              >
                {showPassword ? "hide" : "show"}
              </button>
            </span>
          </Field>

          <Field label="confirm password" htmlFor="confirm-password">
            <input
              id="confirm-password"
              name="confirm-password"
              type={showPassword ? "text" : "password"}
              className="inp"
              value={confirmPassword}
              onChange={(e) => {
                setConfirmPassword(e.target.value)
                clearErrorOnInput()
              }}
              required
              autoComplete="new-password"
              aria-invalid={fieldInvalid}
              aria-describedby={authError ? "register-error" : undefined}
            />
          </Field>

          <button type="submit" className="btn btn-primary mt-1 w-full justify-center py-[7px]" aria-disabled={loading}>
            {loading ? "Creating account…" : "Create Admin Account"}
          </button>
        </form>

        <p className="m-0 text-center text-[11.5px] text-text-3">
          Already have an account?{" "}
          <Link href="/login" className="text-ld-accent underline-offset-4 hover:underline">
            Sign in
          </Link>
        </p>
      </div>
    </AuthCard>
  )
}
