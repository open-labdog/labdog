"use client"

import { useEffect } from "react"
import Link from "next/link"
import { AlertTriangleIcon } from "lucide-react"
import { Button } from "@/components/ui/button"

export default function DashboardError({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  useEffect(() => {
    console.error(error)
  }, [error])

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-950">
      <div className="rounded-xl border border-slate-700 bg-slate-900 p-8 max-w-md w-full text-center space-y-4">
        <AlertTriangleIcon className="w-12 h-12 text-red-400 mx-auto" />
        <h2 className="text-xl font-bold text-white">Something went wrong</h2>
        <p className="text-slate-400 text-sm">
          {error.message || "An unexpected error occurred."}
        </p>
        <div className="flex gap-3 justify-center pt-2">
          <Button onClick={reset}>Try Again</Button>
          <Link href="/dashboard">
            <Button variant="outline">Go to Dashboard</Button>
          </Link>
        </div>
      </div>
    </div>
  )
}
