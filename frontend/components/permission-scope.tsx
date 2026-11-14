"use client"

import type { ReactNode } from "react"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { PermissionScopeContext, isScoped, useAuth } from "@/lib/auth"

interface ScopedPermissions {
  scope: string
  id: number | null
  permissions: string[]
}

/**
 * Makes `useCan` answer for one host or group inside its page.
 *
 * Only a user whose rights are limited to some hosts or groups needs
 * this (an RBAC grant from the Enterprise edition); for everyone else the
 * global list already says everything and nothing is fetched.
 */
export function PermissionScope({
  hostId,
  groupId,
  children,
}: {
  hostId?: number
  groupId?: number
  children: ReactNode
}) {
  const { user } = useAuth()
  const scoped = isScoped(user)
  const param = hostId != null ? `host_id=${hostId}` : groupId != null ? `group_id=${groupId}` : null
  const { data } = useQuery({
    queryKey: ["me", "permissions", param],
    queryFn: () => apiFetch<ScopedPermissions>(`/api/users/me/permissions?${param}`),
    enabled: scoped && param != null,
    staleTime: 60_000,
  })
  return (
    <PermissionScopeContext.Provider value={data?.permissions ?? null}>
      {children}
    </PermissionScopeContext.Provider>
  )
}
