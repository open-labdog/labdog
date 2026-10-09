import { Suspense } from "react"
import { Redirect } from "@/components/shell/redirect"

// The editor lives on the group page's Config tab; the old standalone URL lands there.
export async function generateStaticParams() {
  return [{ id: "placeholder" }]
}

export default function Page() {
  return (
    <Suspense fallback={null}>
      <Redirect to="/groups/:id?tab=config&module=services" />
    </Suspense>
  )
}
