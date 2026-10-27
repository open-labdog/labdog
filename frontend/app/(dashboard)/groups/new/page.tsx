"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { useQueryClient } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { groupSchema, type GroupInput } from "@/lib/schemas"
import { Banner, Field, PageHead, Panel } from "@/components/ld"

export default function NewGroupPage() {
  const router = useRouter()
  const queryClient = useQueryClient()
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
        body: JSON.stringify({ name: data.name, description: data.description || null, category: data.category || null, priority: data.priority }),
      })
      await queryClient.invalidateQueries({ queryKey: ["groups-summary"] })
      router.push("/groups")
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create group")
    } finally {
      setLoading(false)
    }
  })

  return (
    <>
      <PageHead crumbs={[{ label: "fleet", href: "/hosts" }, { label: "groups", href: "/groups" }]} title="New Group" sub="Create a new host group" />
      <div className="scroll flex flex-1 flex-col p-3.5">
        <Panel pad={13} style={{ maxWidth: 480 }}>
          <form onSubmit={onSubmit} noValidate className="flex flex-col gap-3">
            <Field label="name" htmlFor="name" error={form.formState.errors.name?.message}>
              <input id="name" className="inp" placeholder="e.g. production-servers" {...form.register("name")} />
            </Field>
            <Field label="description" htmlFor="description" hint="optional">
              <textarea id="description" className="inp" rows={3} {...form.register("description")} />
            </Field>
            <Field label="category" htmlFor="category" hint="optional">
              <input id="category" className="inp" placeholder="e.g. Production, Security, Networking" {...form.register("category")} />
            </Field>
            <Field label="priority" htmlFor="priority" hint="higher wins over lower-priority groups" error={form.formState.errors.priority?.message}>
              <input id="priority" type="number" min={1} max={1000} className="inp mono num" {...form.register("priority", { valueAsNumber: true })} />
            </Field>

            {error && <Banner tone="danger">{error}</Banner>}

            <div className="flex gap-2.5 pt-1">
              <button type="button" className="btn" onClick={() => router.push("/groups")}>Cancel</button>
              <button type="submit" className="btn btn-primary" disabled={loading}>{loading ? "Creating…" : "Create Group"}</button>
            </div>
          </form>
        </Panel>
      </div>
    </>
  )
}
