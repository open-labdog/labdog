import { Suspense } from "react"
import { Redirect } from "@/components/shell/redirect"

export async function generateStaticParams() {
  return [{ id: "placeholder" }]
}

/** The same queue is a tab of Discovery now. */
export default function Page() {
  return (
    <Suspense fallback={null}>
      <Redirect to="/discovery?tab=pending&scan=:id" />
    </Suspense>
  )
}
