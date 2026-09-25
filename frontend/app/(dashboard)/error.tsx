"use client"

import { useEffect } from "react"
import Link from "next/link"
import { Banner } from "@/components/ld"

/**
 * A screen that threw while rendering. The shell around it still works —
 * rail, pane and palette are outside this boundary — so this only has to
 * say what broke and offer the two ways on: try the screen again, or go
 * somewhere that works.
 */
export default function DashboardError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error)
  }, [error])

  return (
    <div className="flex flex-1 items-center justify-center overflow-y-auto p-6">
      <div className="flex w-full max-w-[440px] flex-col gap-3.5 rounded-r-lg border border-line-strong bg-surface p-6 shadow-ld">
        <h1 className="m-0 text-[15px] font-semibold text-text">Something went wrong</h1>
        <Banner tone="danger">{error.message || "An unexpected error occurred."}</Banner>
        {error.digest && <span className="mono text-[11px] text-text-3">digest {error.digest}</span>}
        <div className="flex flex-wrap gap-[7px]">
          <button type="button" className="btn btn-primary" onClick={reset}>
            Try again
          </button>
          <Link href="/overview" className="btn hover:no-underline">
            Go to Overview
          </Link>
        </div>
      </div>
    </div>
  )
}
