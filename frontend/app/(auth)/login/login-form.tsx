"use client"

import { useState, useEffect, useRef, FormEvent } from "react"
import { useRouter } from "next/navigation"
import { Dog, Eye, EyeOff, Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { AuthError } from "@/components/auth-error"
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
        window.location.href = "/dashboard"
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

  const fieldInvalid = authError?.fieldLevel ? true : undefined

  return (
    <AuthCardShell>
      <div className="p-8 space-y-6">
        <div className="flex flex-col items-center gap-3">
          <div className="rounded-xl bg-blue-600/10 p-3 ring-1 ring-blue-600/20">
            <Dog
              className="size-8 text-blue-400"
              aria-hidden="true"
            />
          </div>
          <div className="text-center space-y-1">
            <h1 className="text-2xl font-bold text-white">LabDog</h1>
            <p className="text-sm text-slate-400">Sign in to your account</p>
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
          <AuthError ref={errorRef} error={authError} id="login-error" />

          <div className="space-y-2">
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              name="email"
              type="email"
              inputMode="email"
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
                autoComplete="current-password"
                aria-invalid={fieldInvalid}
                aria-describedby={authError ? "login-error" : undefined}
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
                Signing in…
              </>
            ) : (
              "Sign In"
            )}
          </Button>
        </form>

        <p className="text-xs text-slate-500 text-center">
          Locked out? Another admin can reset your password from{" "}
          <span className="text-slate-400">Settings → Users</span>.
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
