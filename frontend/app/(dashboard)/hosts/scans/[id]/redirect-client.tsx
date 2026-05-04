"use client"

import { useEffect } from "react"
import { useRouter, useParams } from "next/navigation"

export default function ScanRedirectClient() {
  const router = useRouter()
  const params = useParams()
  useEffect(() => {
    router.replace(`/hosts/discovery/${params.id}`)
  }, [router, params.id])
  return null
}
