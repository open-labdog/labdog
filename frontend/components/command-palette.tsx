"use client"

import { useEffect, useState, useCallback } from "react"
import { useRouter } from "next/navigation"
import { Command } from "cmdk"
import { Dialog, DialogContent } from "@/components/ui/dialog"

const NAV_ITEMS = [
  { label: "Dashboard", href: "/dashboard" },
  { label: "Groups", href: "/groups" },
  { label: "Hosts", href: "/hosts" },
  { label: "SSH Keys", href: "/ssh-keys" },
  { label: "Git Repos", href: "/git-repos" },
  { label: "Audit Log", href: "/audit" },
  { label: "Users", href: "/users" },
]

export function CommandPalette() {
  const [open, setOpen] = useState(false)
  const router = useRouter()

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault()
        setOpen((prev) => !prev)
      }
    }
    document.addEventListener("keydown", handleKeyDown)
    return () => document.removeEventListener("keydown", handleKeyDown)
  }, [])

  const handleSelect = useCallback(
    (href: string) => {
      router.push(href)
      setOpen(false)
    },
    [router]
  )

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent
        showCloseButton={false}
        className="p-0 overflow-hidden max-w-lg"
      >
        <Command className="bg-slate-900 text-white">
          <Command.Input
            placeholder="Search pages..."
            className="w-full bg-transparent border-b border-slate-700 px-4 py-3 text-sm text-white placeholder:text-slate-500 outline-none"
          />
          <Command.List className="max-h-64 overflow-y-auto p-2">
            <Command.Empty className="py-6 text-center text-sm text-slate-500">
              No results found.
            </Command.Empty>
            {NAV_ITEMS.map((item) => (
              <Command.Item
                key={item.href}
                value={item.label}
                onSelect={() => handleSelect(item.href)}
                className="flex items-center gap-2 px-3 py-2 rounded-md text-sm text-slate-300 cursor-pointer hover:bg-slate-800 hover:text-white data-[selected=true]:bg-slate-800 data-[selected=true]:text-white"
              >
                {item.label}
              </Command.Item>
            ))}
          </Command.List>
        </Command>
      </DialogContent>
    </Dialog>
  )
}
