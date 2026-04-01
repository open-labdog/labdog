"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { InfoIcon } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import { Tooltip } from "@/components/ui/tooltip"
import { GroupMultiSelect } from "@/components/group-multi-select"
import { apiFetch } from "@/lib/api"
import { hostSchema, type HostInput } from "@/lib/schemas"
import type { SSHKey, HostGroup } from "@/lib/types"

export default function NewHostPage() {
  const router = useRouter()
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const form = useForm<HostInput>({
    resolver: zodResolver(hostSchema),
    defaultValues: { hostname: "", ip_address: "", ssh_port: 22, ssh_user: "root", ssh_key_id: "", group_ids: [] },
    mode: "onSubmit",
  })

  const { data: sshKeys } = useQuery<SSHKey[]>({
    queryKey: ["ssh-keys"],
    queryFn: () => apiFetch<SSHKey[]>("/api/ssh-keys"),
  })

  const { data: groups } = useQuery<HostGroup[]>({
    queryKey: ["groups"],
    queryFn: () => apiFetch<HostGroup[]>("/api/groups"),
  })

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
    <div className="max-w-lg space-y-6">
      <Breadcrumb items={[{ label: "Hosts", href: "/hosts" }, { label: "New Host" }]} />
      <div>
        <h1 className="text-2xl font-bold text-white">Add Host</h1>
        <p className="text-slate-400 text-sm mt-1">Register a new host for firewall management</p>
      </div>

      <div className="rounded-lg border border-slate-700 bg-slate-900 p-6">
        <form onSubmit={onSubmit} noValidate className="space-y-4">
          <div className="space-y-2">
            <div className="flex items-center gap-1.5">
              <Label htmlFor="hostname">Hostname</Label>
              <Tooltip content="Leave empty to auto-detect via SSH (requires an SSH key)">
                <InfoIcon className="w-3.5 h-3.5 text-slate-500 cursor-help" />
              </Tooltip>
            </div>
            <Input
              id="hostname"
              type="text"
              placeholder="Leave empty to auto-detect via SSH"
              {...form.register("hostname")}
            />
            {form.formState.errors.hostname && (
              <p className="text-sm text-red-400">{form.formState.errors.hostname.message}</p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="ip_address">IP Address</Label>
            <Input
              id="ip_address"
              type="text"
              placeholder="e.g. 192.168.1.100"
              {...form.register("ip_address")}
            />
            {form.formState.errors.ip_address && (
              <p className="text-sm text-red-400">{form.formState.errors.ip_address.message}</p>
            )}
          </div>

           <div className="space-y-2">
              <div className="flex items-center gap-1.5">
                <Label htmlFor="ssh_port">SSH Port</Label>
                <Tooltip content="Default is 22. Change if your server uses a non-standard SSH port.">
                  <InfoIcon className="w-3.5 h-3.5 text-slate-500 cursor-help" />
                </Tooltip>
              </div>
              <Input
                id="ssh_port"
                type="number"
                {...form.register("ssh_port", { valueAsNumber: true })}
                min={1}
                max={65535}
              />
              {form.formState.errors.ssh_port && (
                <p className="text-sm text-red-400">{form.formState.errors.ssh_port.message}</p>
              )}
            </div>

            <div className="space-y-2">
              <div className="flex items-center gap-1.5">
                <Label htmlFor="ssh_user">SSH User</Label>
                <Tooltip content="SSH username for connecting to the host. Default is 'root'.">
                  <InfoIcon className="w-3.5 h-3.5 text-slate-500 cursor-help" />
                </Tooltip>
              </div>
              <Input
                id="ssh_user"
                type="text"
                placeholder="root"
                {...form.register("ssh_user")}
              />
              {form.formState.errors.ssh_user && (
                <p className="text-sm text-red-400">{form.formState.errors.ssh_user.message}</p>
              )}
            </div>

           <div className="space-y-2">
            <Label htmlFor="ssh_key">SSH Key</Label>
            <select
              id="ssh_key"
              {...form.register("ssh_key_id")}
              className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30"
            >
              <option value="">No SSH key</option>
              {sshKeys?.map((key) => (
                <option key={key.id} value={key.id}>
                  {key.name}{key.is_default ? " (default)" : ""}
                </option>
              ))}
            </select>
          </div>

          {groups && groups.length > 0 && (
            <GroupMultiSelect
              groups={groups}
              selected={selectedGroupIds}
              onChange={(ids) => form.setValue("group_ids", ids.map(String))}
            />
          )}

          {error && (
            <p className="text-sm text-red-400">{error}</p>
          )}

          <div className="flex gap-3 pt-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => router.push("/hosts")}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={loading}>
              {loading ? "Adding..." : "Add Host"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  )
}
