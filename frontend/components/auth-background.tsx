import type { ReactNode } from "react"
import { Dot } from "@/components/ld"

/**
 * The page behind the sign-in and setup cards: the theme's background
 * with a dot grid in the border tone and a soft accent glow at the top.
 *
 * It is its own scroll box. The shell sets `overflow: hidden` on the body
 * (it owns scrolling everywhere else), so a card taller than the window —
 * the setup form on a phone held sideways — would otherwise be cut off
 * with no way to reach its button.
 */
export function AuthBackground({ children }: { children: ReactNode }) {
  return (
    <div
      className="relative h-[100dvh] overflow-y-auto bg-bg"
      style={{ backgroundImage: "radial-gradient(circle, var(--border) 1px, transparent 1px)", backgroundSize: "24px 24px" }}
    >
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 top-0 h-[500px]"
        style={{ backgroundImage: "radial-gradient(ellipse 600px 400px at 50% 0%, var(--accent-soft) 0%, transparent 70%)" }}
      />
      <div className="relative flex min-h-full items-center justify-center px-4 py-8">
        <div className="w-full max-w-[400px]">{children}</div>
      </div>
    </div>
  )
}

/** The card both auth forms sit in. */
export function AuthCard({ children }: { children: ReactNode }) {
  return <div className="rounded-r-lg border border-line-strong bg-surface shadow-ld">{children}</div>
}

/** The mark and heading over each auth form. */
export function AuthHeading({ title, sub }: { title: string; sub: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-3 text-center">
      {/* eslint-disable-next-line @next/next/no-img-element -- static svg, no optimisation needed */}
      <img src="/logo.svg" alt="" width={40} height={40} className="block" />
      <div className="flex flex-col gap-1">
        <h1 className="m-0 text-[19px] font-semibold tracking-[-0.015em] text-text">{title}</h1>
        <p className="m-0 text-[12.5px] text-text-2">{sub}</p>
      </div>
    </div>
  )
}

/** The pulsing dot and sentence a card shows while it asks the backend
 *  whether this instance still needs its first account. */
export function AuthChecking() {
  return (
    <div className="flex items-center justify-center gap-2.5 py-10 text-xs text-text-3">
      <Dot tone="idle" pulse />
      Checking instance status…
    </div>
  )
}
