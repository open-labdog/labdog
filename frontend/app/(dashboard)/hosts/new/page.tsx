"use client"

import { useState, FormEvent } from "react"
import { useRouter } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import { apiFetch } from "@/lib/api"
import type { SSHKey, HostGroup } from "@/lib/types"

export default function NewHostPage() {
  const router = useRouter()
  const [hostname, setHostname] = useState("")
  const [ipAddress, setIpAddress] = useState("")
  const [sshPort, setSshPort] = useState(22)
  const [sshKeyId, setSshKeyId] = useState<number | null>(null)
  const [selectedGroups, setSelectedGroups] = useState<number[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const { data: sshKeys } = useQuery<SSHKey[]>({
    queryKey: ["ssh-keys"],
    queryFn: () => apiFetch<SSHKey[]>("/api/ssh-keys"),
  })

  const { data: groups } = useQuery<HostGroup[]>({
    queryKey: ["groups"],
    queryFn: () => apiFetch<HostGroup[]>("/api/groups"),
  })

  function toggleGroup(id: number) {
    setSelectedGroups((prev) =>
      prev.includes(id) ? prev.filter((g) => g !== id) : [...prev, id]
    )
  }

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setError(null)
    setLoading(true)

    try {
      await apiFetch("/api/hosts", {
        method: "POST",
        body: JSON.stringify({
          hostname,
          ip_address: ipAddress,
          ssh_port: sshPort,
          ssh_key_id: sshKeyId,
          group_ids: selectedGroups,
        }),
      })
      router.push("/hosts")
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create host")
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="max-w-lg space-y-6">
      <Breadcrumb items={[{ label: "Hosts", href: "/hosts" }, { label: "New Host" }]} />
      <div>
        <h1 className="text-2xl font-bold text-white">Add Host</h1>
        <p className="text-slate-400 text-sm mt-1">Register a new host for firewall management</p>
      </div>

      <div className="rounded-lg border border-slate-700 bg-slate-900 p-6">
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="hostname">Hostname</Label>
            <Input
              id="hostname"
              type="text"
              placeholder="e.g. web-server-01"
              value={hostname}
              onChange={(e) => setHostname(e.target.value)}
              required
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="ip_address">IP Address</Label>
            <Input
              id="ip_address"
              type="text"
              placeholder="e.g. 192.168.1.100"
              value={ipAddress}
              onChange={(e) => setIpAddress(e.target.value)}
              required
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="ssh_port">SSH Port</Label>
            <Input
              id="ssh_port"
              type="number"
              value={sshPort}
              onChange={(e) => setSshPort(Number(e.target.value))}
              required
              min={1}
              max={65535}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="ssh_key">SSH Key</Label>
            <select
              id="ssh_key"
              value={sshKeyId ?? ""}
              onChange={(e) => setSshKeyId(e.target.value ? Number(e.target.value) : null)}
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
            <div className="space-y-2">
              <Label>Groups</Label>
              <div className="space-y-2 rounded-lg border border-input p-3 dark:bg-input/10">
                {groups.map((group) => (
                  <label key={group.id} className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={selectedGroups.includes(group.id)}
                      onChange={() => toggleGroup(group.id)}
                      className="rounded border-input"
                    />
                    <span className="text-sm text-foreground">{group.name}</span>
                    {group.description && (
                      <span className="text-xs text-muted-foreground">— {group.description}</span>
                    )}
                  </label>
                ))}
              </div>
            </div>
          )}

          {error && (
            <p className="text-sm text-red-400">{error}</p>
          )}

          <div className="flex gap-3 pt-2">
            <Button type="submit" disabled={loading}>
              {loading ? "Adding..." : "Add Host"}
            </Button>
            <Button
              type="button"
              variant="outline"
              onClick={() => router.push("/hosts")}
            >
              Cancel
            </Button>
          </div>
        </form>
      </div>
    </div>
  )
}
