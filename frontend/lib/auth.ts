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
