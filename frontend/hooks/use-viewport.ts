"use client"

import { useEffect, useState } from "react"

/**
 * The viewport width, updated on resize. Starts from a desktop guess on
 * the server — there is no viewport there, and the shell is desktop-first.
 */
export function useViewportWidth(fallback = 1440): number {
  const [w, setW] = useState(() => (typeof window === "undefined" ? fallback : window.innerWidth))
  useEffect(() => {
    const h = () => setW(window.innerWidth)
    h()
    window.addEventListener("resize", h)
    return () => window.removeEventListener("resize", h)
  }, [])
  return w
}
