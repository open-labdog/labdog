"use client"
import { createContext, useContext } from "react"

export interface User {
  id: number
  email: string
  is_active: boolean
  is_superuser: boolean
  is_verified: boolean
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
