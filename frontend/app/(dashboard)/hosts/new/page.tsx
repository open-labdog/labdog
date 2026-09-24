"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { apiFetch } from "@/lib/api"
import { hostSchema, type HostInput } from "@/lib/schemas"
import type { SSHKey, HostGroup } from "@/lib/types"
import { Banner, Field, PageHead, Panel } from "@/components/ld"
import { GroupMultiSelect } from "@/components/group-multi-select"

export default function NewHostPage() {
  const router = useRouter()
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const form = useForm<HostInput>({
    resolver: zodResolver(hostSchema),
    defaultValues: { hostname: "", ip_address: "", ssh_port: 22, ssh_user: "root", ssh_key_id: "", group_ids: [], drift_check_enabled: true },
    mode: "onSubmit",
  })

  const { data: sshKeys } = useQuery<SSHKey[]>({ queryKey: ["ssh-keys"], queryFn: () => apiFetch<SSHKey[]>("/api/ssh-keys") })
  const { data: groups } = useQuery<HostGroup[]>({ queryKey: ["groups"], queryFn: () => apiFetch<HostGroup[]>("/api/groups") })

  const selectedGroupIds = (form.watch("group_ids") ?? []).map(Number)

  const onSubmit = form.handleSubmit(async (data) => {
    setError(null)
    setLoading(true)
    try {
      await apiFetch("/api/hosts", {
        method: "POST",
        body: JSON.stringify({
          hostname: data.hostname,
          ip_address: data.ip_address,
          ssh_port: data.ssh_port,
          ssh_user: data.ssh_user,
          ssh_key_id: data.ssh_key_id ? Number(data.ssh_key_id) : null,
          group_ids: (data.group_ids ?? []).map(Number),
          drift_check_enabled: !!data.drift_check_enabled,
        }),
      })
      router.push("/hosts")
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create host")
    } finally {
      setLoading(false)
    }
  })

  return (
    <>
      <PageHead crumbs={[{ label: "fleet", href: "/hosts" }, { label: "hosts", href: "/hosts" }]} title="Add Host" sub="Register a new host for firewall management" />
      <div className="scroll flex flex-1 flex-col p-3.5">
        <Panel pad={13} style={{ maxWidth: 480 }}>
          <form onSubmit={onSubmit} noValidate className="flex flex-col gap-3">
            <Field label="hostname" htmlFor="hostname" hint="leave empty to auto-detect via SSH" error={form.formState.errors.hostname?.message}>
              <input id="hostname" className="inp mono" placeholder="leave empty to auto-detect via SSH" {...form.register("hostname")} />
            </Field>
            <Field label="ip address" htmlFor="ip_address" error={form.formState.errors.ip_address?.message}>
              <input id="ip_address" className="inp mono" placeholder="e.g. 192.168.1.100" {...form.register("ip_address")} />
            </Field>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label="ssh port" htmlFor="ssh_port" hint="default 22" error={form.formState.errors.ssh_port?.message}>
                <input id="ssh_port" type="number" min={1} max={65535} className="inp mono num" {...form.register("ssh_port", { valueAsNumber: true })} />
              </Field>
              <Field label="ssh user" htmlFor="ssh_user" hint="default root" error={form.formState.errors.ssh_user?.message}>
                <input id="ssh_user" className="inp mono" placeholder="root" {...form.register("ssh_user")} />
              </Field>
            </div>
            <Field as="div" label="ssh key">
              <select id="ssh_key" className="inp" {...form.register("ssh_key_id")}>
                <option value="">No SSH key</option>
                {sshKeys?.map((key) => <option key={key.id} value={key.id}>{key.name}{key.is_default ? " (default)" : ""}</option>)}
              </select>
            </Field>

            {groups && groups.length > 0 && (
              <GroupMultiSelect groups={groups} selected={selectedGroupIds} onChange={(ids) => form.setValue("group_ids", ids.map(String))} />
            )}

            <label className="flex items-start gap-2.5 rounded-r border border-line bg-surface-2 px-3.5 py-2.5">
              <input type="checkbox" className="mt-0.5" {...form.register("drift_check_enabled")} />
              <span className="text-xs text-text">
                <span className="font-medium">Check this host for drift</span>
                <span className="mt-0.5 block text-[11px] text-text-3">
                  LabDog connects over SSH on a timer and reports where the host has diverged from its desired configuration. Read-only — it never changes the host. Turn this off per module later.
                </span>
              </span>
            </label>

            {error && <Banner tone="danger">{error}</Banner>}

            <div className="flex gap-2.5 pt-1">
              <button type="button" className="btn" onClick={() => router.push("/hosts")}>Cancel</button>
              <button type="submit" className="btn btn-primary" disabled={loading}>{loading ? "Adding…" : "Add Host"}</button>
            </div>
          </form>
        </Panel>
      </div>
    </>
  )
}
