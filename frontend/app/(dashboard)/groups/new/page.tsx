"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { InfoIcon } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import { Tooltip } from "@/components/ui/tooltip"
import { apiFetch } from "@/lib/api"
import { groupSchema, type GroupInput } from "@/lib/schemas"

export default function NewGroupPage() {
  const router = useRouter()
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const form = useForm<GroupInput>({
    resolver: zodResolver(groupSchema),
    defaultValues: { name: "", description: "", category: "", priority: 100 },
    mode: "onSubmit",
  })

  const onSubmit = form.handleSubmit(async (data) => {
    setError(null)
    setLoading(true)

    try {
      await apiFetch("/api/groups", {
        method: "POST",
        body: JSON.stringify({
          name: data.name,
          description: data.description || null,
          category: data.category || null,
          priority: data.priority,
        }),
      })
      router.push("/groups")
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create group")
    } finally {
      setLoading(false)
    }
  })

  return (
    <div className="max-w-lg space-y-6">
      <Breadcrumb items={[{ label: "Groups", href: "/groups" }, { label: "New Group" }]} />
      <div>
        <h1 className="text-2xl font-bold text-white">New Group</h1>
        <p className="text-slate-400 text-sm mt-1">Create a new host group</p>
      </div>

      <div className="rounded-lg border border-slate-700 bg-slate-900 p-6">
        <form onSubmit={onSubmit} noValidate className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="name">Name</Label>
            <Input
              id="name"
              type="text"
              placeholder="e.g. production-servers"
              {...form.register("name")}
            />
            {form.formState.errors.name && (
              <p className="text-sm text-red-400">{form.formState.errors.name.message}</p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="description">Description</Label>
            <textarea
              id="description"
              placeholder="Optional description..."
              {...form.register("description")}
              rows={3}
              className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring resize-none dark:bg-input/30"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="category">Category</Label>
            <Input
              id="category"
              type="text"
              placeholder="e.g. Production, Security, Networking"
              {...form.register("category")}
            />
          </div>

           <div className="space-y-2">
             <div className="flex items-center gap-1.5">
               <Label htmlFor="priority">Priority</Label>
               <Tooltip content="Higher number = higher priority. Rules from higher-priority groups override lower ones.">
                 <InfoIcon className="w-3.5 h-3.5 text-slate-500 cursor-help" />
               </Tooltip>
             </div>
             <Input
               id="priority"
               type="number"
               {...form.register("priority", { valueAsNumber: true })}
               min={1}
               max={1000}
             />
             {form.formState.errors.priority && (
               <p className="text-sm text-red-400">{form.formState.errors.priority.message}</p>
             )}
           </div>

          {error && (
            <p className="text-sm text-red-400">{error}</p>
          )}

          <div className="flex gap-3 pt-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => router.push("/groups")}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={loading}>
              {loading ? "Creating..." : "Create Group"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  )
}
