import { cn } from "@/lib/utils"

export function EmptyState({
  message,
  className,
  children,
}: {
  message: string
  className?: string
  children?: React.ReactNode
}) {
  return (
    <div className={cn("text-slate-400 py-8 text-center", className)}>
      <p>{message}</p>
      {children}
    </div>
  )
}
