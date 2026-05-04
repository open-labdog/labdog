import { AuthBackground } from "@/components/auth-background"

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthBackground>
      <main>{children}</main>
    </AuthBackground>
  )
}
