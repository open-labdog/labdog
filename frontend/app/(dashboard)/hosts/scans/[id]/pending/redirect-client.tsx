"use client"

import { useEffect } from "react"
import { useRouter, useParams } from "next/navigation"

export default function ScanPendingRedirectClient() {
  const router = useRouter()
  const params = useParams()
  useEffect(() => {
    router.replace(`/discovery?tab=pending&scan=${params.id}`)
  }, [router, params.id])
  return null
}
