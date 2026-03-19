"use client"

import { useState, FormEvent } from "react"
import { useRouter } from "next/navigation"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import { apiFetch } from "@/lib/api"

export default function NewGroupPage() {
  const router = useRouter()
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [priority, setPriority] = useState(100)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setError(null)
    setLoading(true)

    try {
      await apiFetch("/api/groups", {
        method: "POST",
        body: JSON.stringify({
          name,
          description: description || null,
          priority,
        }),
      })
      router.push("/groups")
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create group")
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="max-w-lg space-y-6">
      <Breadcrumb items={[{ label: "Groups", href: "/groups" }, { label: "New Group" }]} />
      <div>
        <h1 className="text-2xl font-bold text-white">New Group</h1>
        <p className="text-slate-400 text-sm mt-1">Create a new host group</p>
      </div>

      <div className="rounded-lg border border-slate-700 bg-slate-900 p-6">
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="name">Name</Label>
            <Input
              id="name"
              type="text"
              placeholder="e.g. production-servers"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="description">Description</Label>
            <textarea
              id="description"
              placeholder="Optional description..."
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
              className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring resize-none dark:bg-input/30"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="priority">Priority</Label>
            <Input
              id="priority"
              type="number"
              value={priority}
              onChange={(e) => setPriority(Number(e.target.value))}
              required
              min={0}
            />
          </div>

          {error && (
            <p className="text-sm text-red-400">{error}</p>
          )}

          <div className="flex gap-3 pt-2">
            <Button type="submit" disabled={loading}>
              {loading ? "Creating..." : "Create Group"}
            </Button>
            <Button
              type="button"
              variant="outline"
              onClick={() => router.push("/groups")}
            >
              Cancel
            </Button>
          </div>
        </form>
      </div>
    </div>
  )
}
