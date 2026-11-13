import type { Metadata } from "next"
import { RegisterForm } from "./register-form"

export const metadata: Metadata = {
  title: "Create Admin Account — unbork",
}

export default function RegisterPage() {
  return <RegisterForm />
}
