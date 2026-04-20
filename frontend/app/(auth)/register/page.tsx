import type { Metadata } from "next"
import { RegisterForm } from "./register-form"

export const metadata: Metadata = {
  title: "Create Admin Account — Barricade",
}

export default function RegisterPage() {
  return <RegisterForm />
}
