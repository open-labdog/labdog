"use client"

import { useState, useEffect, useRef, FormEvent } from "react"
import { useRouter } from "next/navigation"
import { AuthCard, AuthChecking, AuthHeading } from "@/components/auth-background"
import { AuthError } from "@/components/auth-error"
import { Field } from "@/components/ld"
import { classifyAuthError, type AuthErrorInfo } from "@/lib/auth-errors"
import { API_BASE } from "@/lib/api"

export function LoginForm() {
  const router = useRouter()
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [showPassword, setShowPassword] = useState(false)
  const [authError, setAuthError] = useState<AuthErrorInfo | null>(null)
  const [loading, setLoading] = useState(false)
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null)
  const errorRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let cancelled = false
    fetch(`${API_BASE}/api/auth/setup-status`, { credentials: "include" })
      .then((res) => res.json())
      .then((data) => {
        if (cancelled) return
        if (data.needs_setup === true) {
          router.replace("/register")
        } else {
          setNeedsSetup(false)
        }
      })
      .catch(() => {
        if (!cancelled) setNeedsSetup(false)
      })
    return () => {
      cancelled = true
    }
  }, [router])

  useEffect(() => {
    if (authError) errorRef.current?.focus()
  }, [authError])

  function clearErrorOnInput() {
    if (authError) setAuthError(null)
  }

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (loading) return
    setAuthError(null)
    setLoading(true)

    try {
      const res = await fetch(`${API_BASE}/api/auth/jwt/login`, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        credentials: "include",
        body: `username=${encodeURIComponent(email)}&password=${encodeURIComponent(password)}`,
      })
      if (res.ok) {
        // A full page load, not router.push: the auth provider reads the
        // session cookie once on mount, so a client-side navigation would
        // land on /overview still holding the logged-out user object.
        // eslint-disable-next-line @next/next/no-location-assign-relative-destination
        window.location.href = "/overview"
        return
      }
      setAuthError(classifyAuthError(res.status))
    } catch {
      setAuthError(classifyAuthError(null))
    } finally {
      setLoading(false)
    }
  }

  if (needsSetup === null) {
    return (
      <AuthCard>
        <AuthChecking />
      </AuthCard>
    )
  }

  const fieldInvalid = authError?.fieldLevel ? true : undefined

  return (
    <AuthCard>
      <div className="flex flex-col gap-5 p-7">
        <AuthHeading title="LabDog" sub="Sign in to your account" />

        <form onSubmit={handleSubmit} action="" method="post" className="flex flex-col gap-3" aria-busy={loading} noValidate>
          <AuthError ref={errorRef} error={authError} id="login-error" />

          <Field label="email" htmlFor="email">
            <input
              id="email"
              name="email"
              type="email"
              inputMode="email"
              className="inp"
              placeholder="you@example.com"
              value={email}
              onChange={(e) => {
                setEmail(e.target.value)
                clearErrorOnInput()
              }}
              required
              autoComplete="email"
              aria-invalid={fieldInvalid}
              aria-describedby={authError ? "login-error" : undefined}
            />
          </Field>

          <Field label="password" htmlFor="password">
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
                autoComplete="current-password"
                aria-invalid={fieldInvalid}
                aria-describedby={authError ? "login-error" : undefined}
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

          <button type="submit" className="btn btn-primary mt-1 w-full justify-center py-[7px]" aria-disabled={loading}>
            {loading ? "Signing in…" : "Sign In"}
          </button>
        </form>

        <p className="m-0 text-center text-[11.5px] text-text-3">
          Locked out? Another admin can reset your password from <span className="text-text-2">Settings → Access → Users</span>.
        </p>
      </div>
    </AuthCard>
  )
}
