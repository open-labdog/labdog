import { CsrfGuard } from "./csrf-guard"

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <CsrfGuard />
      {children}
    </>
  )
}
