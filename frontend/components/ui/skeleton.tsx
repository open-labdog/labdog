"use client"

import * as React from "react"
import { cn } from "@/lib/utils"

function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      className={cn("animate-pulse rounded-md bg-slate-800", className)}
      {...props}
    />
  )
}

function TableSkeleton({ rows = 5, columns = 4 }: { rows?: number; columns?: number }) {
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900">
      <div className="p-4 space-y-3">
        {Array.from({ length: rows }).map((_, rowIdx) => (
          <div key={rowIdx} className="flex gap-4">
            {Array.from({ length: columns }).map((_, colIdx) => (
              <Skeleton
                key={colIdx}
                className="h-4"
                style={{ width: `${[60, 80, 40, 70][colIdx % 4]}%` }}
              />
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}

function CardSkeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900 p-4 space-y-3">
      <Skeleton className="h-5 w-1/3" />
      {Array.from({ length: lines }).map((_, idx) => (
        <Skeleton key={idx} className="h-4" style={{ width: `${[80, 60, 70][idx % 3]}%` }} />
      ))}
    </div>
  )
}

export { Skeleton, TableSkeleton, CardSkeleton }
