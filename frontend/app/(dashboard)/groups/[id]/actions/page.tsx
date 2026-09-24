import { Suspense } from "react"
import { Redirect } from "@/components/shell/redirect"

// A group's runs are its Activity tab now; the old standalone URL lands there.
export async function generateStaticParams() {
  return [{ id: "placeholder" }]
}

export default function Page() {
  return (
    <Suspense fallback={null}>
      <Redirect to="/groups/:id?tab=activity" />
    </Suspense>
  )
}
