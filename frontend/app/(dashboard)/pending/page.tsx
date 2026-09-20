import { Suspense } from "react"
import { Redirect } from "@/components/shell/redirect"

// Pending is a view of Overview; `?lane=` carries across.
export default function Page() {
  return (
    <Suspense fallback={null}>
      <Redirect to="/overview?view=pending" keepQuery />
    </Suspense>
  )
}
