"use client"

import { useEffect } from "react"
import { useRouter, useParams } from "next/navigation"

export default function ScanPendingRedirectClient() {
  const router = useRouter()
  const params = useParams()
  useEffect(() => {
    router.replace(`/hosts/discovery/${params.id}/pending`)
  }, [router, params.id])
  return null
}
