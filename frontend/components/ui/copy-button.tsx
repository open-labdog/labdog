"use client"

import { useState } from "react"
import { CopyIcon, CheckIcon } from "lucide-react"
import { Button } from "@/components/ui/button"

export function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)

  function handleCopy() {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }).catch(() => {
      // clipboard API unavailable (non-HTTPS or permissions denied) — silently ignore
    })
  }

  return (
    <Button
      variant="ghost"
      size="icon-xs"
      onClick={handleCopy}
      aria-label="Copy to clipboard"
      className="shrink-0"
    >
      {copied ? (
        <CheckIcon className="w-3 h-3 text-green-400" />
      ) : (
        <CopyIcon className="w-3 h-3" />
      )}
    </Button>
  )
}
