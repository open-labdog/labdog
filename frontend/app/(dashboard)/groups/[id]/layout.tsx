"use client"

import type { ReactNode } from "react"
import { useParams } from "next/navigation"
import { PermissionScope } from "@/components/permission-scope"

/** Buttons on every page under this group answer for this group (`useCan`). */
export default function Layout({ children }: { children: ReactNode }) {
  const params = useParams()
  const id = Number(params?.id)
  return <PermissionScope groupId={Number.isFinite(id) ? id : undefined}>{children}</PermissionScope>
}
