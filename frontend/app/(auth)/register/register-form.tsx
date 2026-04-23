"use client"

import { useState, useEffect, useRef, FormEvent } from "react"
import Link from "next/link"
import { Dog, Eye, EyeOff, Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { AuthError } from "@/components/auth-error"
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
      <AuthCardShell>
        <div className="flex items-center justify-center gap-3 py-10">
          <Loader2
            className="size-4 animate-spin text-slate-400"
            aria-hidden="true"
          />
          <p className="text-sm text-slate-400">Checking instance status…</p>
        </div>
      </AuthCardShell>
    )
  }

  if (!needsSetup) {
    return (
      <AuthCardShell>
        <div className="p-8 space-y-6">
          <div className="text-center space-y-1">
            <h1 className="text-2xl font-bold text-white">Registration Closed</h1>
            <p className="text-sm text-slate-400">
              Registration is closed. Contact your administrator to get an account.
            </p>
          </div>
          <Link
            href="/login"
            className="block text-center text-sm text-blue-400 hover:underline underline-offset-4"
          >
            Back to sign in
          </Link>
        </div>
      </AuthCardShell>
    )
  }

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (loading) return
    setAuthError(null)

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
        window.location.href = "/login"
        return
      }

      const data = await res.json().catch(() => null)
      const detail = data?.detail
      const body = Array.isArray(detail)
        ? detail
            .map((e: { msg: string; loc?: (string | number)[] }) =>
              e.loc && e.loc.length > 1
                ? `${e.loc.slice(1).join(".")}: ${e.msg}`
                : e.msg
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
    <AuthCardShell>
      <div className="p-8 space-y-6">
        <div className="flex flex-col items-center gap-3">
          <div className="rounded-xl bg-amber-500/10 p-3 ring-1 ring-amber-500/20">
            <Dog
              className="size-8 text-amber-400"
              aria-hidden="true"
            />
          </div>
          <div className="text-center space-y-1">
            <h1 className="text-2xl font-bold text-white">Welcome to LabDog</h1>
            <p className="text-sm text-slate-400">
              This is a fresh instance. Create the admin account to get started.
            </p>
          </div>
        </div>

        <form
          onSubmit={handleSubmit}
          action=""
          method="post"
          className="space-y-4"
          aria-busy={loading}
          noValidate
        >
          <AuthError ref={errorRef} error={authError} id="register-error" />

          <div className="space-y-2">
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              name="email"
              type="email"
              inputMode="email"
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
          </div>

          <div className="space-y-2">
            <Label htmlFor="password">Password</Label>
            <div className="relative">
              <Input
                id="password"
                name="password"
                type={showPassword ? "text" : "password"}
                value={password}
                onChange={(e) => {
                  setPassword(e.target.value)
                  clearErrorOnInput()
                }}
                required
                autoComplete="new-password"
                aria-invalid={fieldInvalid}
                aria-describedby={authError ? "register-error" : undefined}
                className="pr-10"
              />
              <button
                type="button"
                onClick={() => setShowPassword((v) => !v)}
                aria-label={showPassword ? "Hide password" : "Show password"}
                aria-pressed={showPassword}
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-slate-400 hover:text-slate-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/50"
              >
                {showPassword ? (
                  <EyeOff className="size-4" aria-hidden="true" />
                ) : (
                  <Eye className="size-4" aria-hidden="true" />
                )}
              </button>
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="confirm-password">Confirm Password</Label>
            <Input
              id="confirm-password"
              name="confirm-password"
              type={showPassword ? "text" : "password"}
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
          </div>

          <Button
            type="submit"
            className="w-full"
            aria-disabled={loading}
          >
            {loading ? (
              <>
                <Loader2
                  className="size-4 animate-spin"
                  aria-hidden="true"
                />
                Creating account…
              </>
            ) : (
              "Create Admin Account"
            )}
          </Button>
        </form>

        <p className="text-center text-sm text-slate-400">
          Already have an account?{" "}
          <Link
            href="/login"
            className="text-blue-400 hover:underline underline-offset-4"
          >
            Sign in
          </Link>
        </p>
      </div>
    </AuthCardShell>
  )
}

function AuthCardShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-slate-700 bg-slate-900 shadow-2xl shadow-black/40">
      {children}
    </div>
  )
}
