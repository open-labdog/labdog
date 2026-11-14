"use client"

import { useState, useEffect, useRef, FormEvent } from "react"
import { useRouter } from "next/navigation"
import { AuthCard, AuthChecking, AuthHeading } from "@/components/auth-background"
import { BrandWordmark } from "@/components/brand-wordmark"
import { AuthError } from "@/components/auth-error"
import { Field } from "@/components/ld"
import { classifyAuthError, classifyExternalLoginError, type AuthErrorInfo } from "@/lib/auth-errors"
import { API_BASE } from "@/lib/api"

/** A sign-in method an extension (Enterprise SSO) offers, from
 *  `GET /api/auth/providers`. Community has none, and the page is then
 *  exactly the email and password form. */
interface LoginProvider {
  key: string
  name: string
  kind: "redirect" | "password"
  url: string
}

const LOCAL = "local"

/** Same-origin API paths only; the backend checks this too. */
function isSafePath(url: unknown): url is string {
  return typeof url === "string" && url.startsWith("/") && !url.startsWith("//")
}

function parseProviders(data: unknown): LoginProvider[] {
  const list = (data as { providers?: unknown })?.providers
  if (!Array.isArray(list)) return []
  return list.filter(
    (p): p is LoginProvider =>
      typeof p?.key === "string" &&
      typeof p?.name === "string" &&
      (p?.kind === "redirect" || p?.kind === "password") &&
      isSafePath(p?.url),
  )
}

export function LoginForm() {
  const router = useRouter()
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [showPassword, setShowPassword] = useState(false)
  // An SSO callback that refused the sign-in sends the browser back here
  // with ?sso_error=<code>. Read once: the first render is the "checking"
  // card on the server and the client alike, so this cannot mismatch.
  const [authError, setAuthError] = useState<AuthErrorInfo | null>(() => {
    if (typeof window === "undefined") return null
    const code = new URLSearchParams(window.location.search).get("sso_error")
    return code ? classifyExternalLoginError(code) : null
  })
  const [loading, setLoading] = useState(false)
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null)
  const [providers, setProviders] = useState<LoginProvider[]>([])
  const [method, setMethod] = useState<string>(LOCAL)
  const errorRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let cancelled = false
    fetch(`${API_BASE}/api/auth/providers`, { credentials: "include" })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (!cancelled) setProviders(parseProviders(data))
      })
      .catch(() => {
        // No providers is the Community default; the password form still works.
      })
    return () => {
      cancelled = true
    }
  }, [])

  const redirectProviders = providers.filter((p) => p.kind === "redirect")
  const passwordProviders = providers.filter((p) => p.kind === "password")
  const directory = passwordProviders.find((p) => p.key === method)

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
      const res = await fetch(`${API_BASE}${directory ? directory.url : "/api/auth/jwt/login"}`, {
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
      // A directory login says why it refused (no access, directory down)
      // with a code in `detail`; a wrong password stays a 400.
      if (directory && res.status !== 400) {
        const detail = await res.json().then((b) => b?.detail, () => null)
        if (typeof detail === "string") {
          setAuthError(classifyExternalLoginError(detail))
          return
        }
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
        <AuthHeading title={<BrandWordmark size={24} />} sub="Sign in to your account" />

        {redirectProviders.length > 0 && (
          <div className="flex flex-col gap-2">
            {redirectProviders.map((p) => (
              <a key={p.key} href={`${API_BASE}${p.url}`} className="btn w-full justify-center py-[7px]">
                Sign in with {p.name}
              </a>
            ))}
            <p className="m-0 text-center text-[11.5px] text-text-3">or</p>
          </div>
        )}

        <form onSubmit={handleSubmit} action="" method="post" className="flex flex-col gap-3" aria-busy={loading} noValidate>
          <AuthError ref={errorRef} error={authError} id="login-error" />

          {passwordProviders.length > 0 && (
            <Field label="sign in with" htmlFor="login-method">
              <select
                id="login-method"
                className="inp"
                value={method}
                onChange={(e) => {
                  setMethod(e.target.value)
                  clearErrorOnInput()
                }}
              >
                <option value={LOCAL}>Local account</option>
                {passwordProviders.map((p) => (
                  <option key={p.key} value={p.key}>
                    {p.name}
                  </option>
                ))}
              </select>
            </Field>
          )}

          <Field label={directory ? "username" : "email"} htmlFor="email">
            <input
              id="email"
              name="email"
              type={directory ? "text" : "email"}
              inputMode={directory ? "text" : "email"}
              className="inp"
              placeholder={directory ? "your directory username" : "you@example.com"}
              value={email}
              onChange={(e) => {
                setEmail(e.target.value)
                clearErrorOnInput()
              }}
              required
              autoComplete={directory ? "username" : "email"}
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
