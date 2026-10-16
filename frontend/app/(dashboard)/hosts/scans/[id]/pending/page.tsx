import { Suspense } from "react"
import { Redirect } from "@/components/shell/redirect"

// A scan's results live on the Discovery screen; kept for deep links.
export async function generateStaticParams() {
  return [{ id: "placeholder" }]
}

export default function Page() {
  return (
    <Suspense fallback={null}>
      <Redirect to="/discovery?tab=pending&scan=:id" />
    </Suspense>
  )
}
