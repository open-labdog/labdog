"use client"

import { useEffect } from "react"
import { useRouter } from "next/navigation"

export default function ScansRedirect() {
  const router = useRouter()
  useEffect(() => {
    router.replace("/hosts/discovery")
  }, [router])
  return null
}
