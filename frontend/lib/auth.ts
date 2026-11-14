"use client"
import { createContext, useContext } from "react"

export type Role = "admin" | "operator" | "viewer"

export interface User {
  id: number
  email: string
  is_active: boolean
  /** Mirrors `role === "admin"`; read `role` or `can()` instead. */
  is_superuser: boolean
  is_verified: boolean
  role: Role | null
  display_name: string | null
  last_login_at: string | null
  must_change_password: boolean
  /** What this account may do everywhere, from `GET /api/users/me`. */
  permissions: string[]
  /**
   * What it may do on at least one host or group. The same list in
   * Community; wider when an extension (RBAC) grants rights on some hosts
   * or groups only. Zones and the palette show from this.
   */
  permissions_anywhere?: string[]
}

/** Whether `user` holds `permission` everywhere. The backend enforces it either way. */
export function can(user: User | null, permission: string): boolean {
  return !!user?.permissions?.includes(permission)
}

/** Whether `user` holds `permission` on at least one host or group. */
export function canAnywhere(user: User | null, permission: string): boolean {
  return can(user, permission) || !!user?.permissions_anywhere?.includes(permission)
}

/** Whether any of the user's rights are limited to some hosts or groups. */
export function isScoped(user: User | null): boolean {
  const anywhere = user?.permissions_anywhere ?? []
  return anywhere.some((p) => !user?.permissions?.includes(p))
}

/**
 * The permissions that apply on the host or group page being shown, set by
 * `<PermissionScope>`. `null` outside such a page: `useCan` then answers
 * from the global list.
 */
export const PermissionScopeContext = createContext<string[] | null>(null)

/**
 * `can()` for the signed-in user, on the current host or group page when
 * there is one. Use it to hide or disable a control the user cannot use;
 * the API refuses the request regardless.
 */
export function useCan(permission: string): boolean {
  const { user } = useContext(AuthContext)
  const scoped = useContext(PermissionScopeContext)
  return can(user, permission) || !!scoped?.includes(permission)
}

/** What each role is for, in the words the Users page shows. */
export const ROLE_INFO: Record<Role, { label: string; note: string }> = {
  viewer: { label: "viewer", note: "sees everything, changes nothing" },
  operator: { label: "operator", note: "edits configuration, syncs, runs actions, opens the terminal" },
  admin: { label: "admin", note: "also integrations, settings and user accounts" },
}

export interface AuthContextType {
  user: User | null
  loading: boolean
  logout: () => Promise<void>
}

export const AuthContext = createContext<AuthContextType>({
  user: null,
  loading: true,
  logout: async () => {},
})

export function useAuth() {
  return useContext(AuthContext)
}
