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
  /** What this account may do, from `GET /api/users/me`. */
  permissions: string[]
}

/** Whether `user` holds `permission`. The backend enforces it either way. */
export function can(user: User | null, permission: string): boolean {
  return !!user?.permissions?.includes(permission)
}

/**
 * `can()` for the signed-in user. Use it to hide or disable a control the
 * user's role cannot use; the API refuses the request regardless.
 */
export function useCan(permission: string): boolean {
  const { user } = useContext(AuthContext)
  return can(user, permission)
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
