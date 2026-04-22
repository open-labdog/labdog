"use client"

import { useState, useEffect } from "react"
import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import { GroupMultiSelect } from "@/components/group-multi-select"
import { DataTable } from "@/components/ui/data-table"
import type { ColumnDef } from "@/components/ui/data-table"
import { apiFetch } from "@/lib/api"
import type { SSHKey, HostGroup } from "@/lib/types"

interface DiscoveredHost {
  ip: string
  hostname: string | null
  ssh_status: "open" | "refused"
}

interface ScanStatus {
  job_id: string
  status: "pending" | "running" | "done" | "error"
  progress: number
  total: number
  hosts_found: DiscoveredHost[]
  error?: string
}

interface FailedHost {
  ip: string
  error: string
}

interface AddResult {
  added: number
  skipped: number
  failed: FailedHost[]
}

const cidrSchema = z.object({
  cidr: z.string()
    .min(1, "CIDR is required")
    .regex(/^(\d{1,3}\.){3}\d{1,3}\/\d{1,2}$/, "Invalid CIDR format (e.g., 192.168.1.0/24)"),
})
type CidrInput = z.infer<typeof cidrSchema>

type Phase = "idle" | "scanning" | "done" | "adding"

export default function DiscoverHostsPage() {
  const [phase, setPhase] = useState<Phase>("idle")
  const [jobId, setJobId] = useState<string | null>(null)
  const [scanError, setScanError] = useState<string | null>(null)
  const [selectedHosts, setSelectedHosts] = useState<Set<string>>(new Set())
  const [selectedKeyId, setSelectedKeyId] = useState<number | null>(null)
  const [selectedGroupIds, setSelectedGroupIds] = useState<number[]>([])
  const [addResult, setAddResult] = useState<AddResult | null>(null)
  const [addError, setAddError] = useState<string | null>(null)

  const form = useForm<CidrInput>({
    resolver: zodResolver(cidrSchema),
    defaultValues: { cidr: "" },
    mode: "onChange",
  })

  const cidrValue = form.watch("cidr")
  const cidrValid = !form.formState.errors.cidr && cidrValue.length > 0

  const { data: scanStatus } = useQuery<ScanStatus>({
    queryKey: ["discovery-scan", jobId],
    queryFn: () => apiFetch<ScanStatus>(`/api/discovery/scan/${jobId}`),
    enabled: !!jobId,
    refetchInterval: (query) => {
      const data = query.state.data
      if (!data || data.status === "pending" || data.status === "running") return 2000
      return false
    },
  })

  useEffect(() => {
    if (!jobId || !scanStatus || phase !== "scanning") return
    if (scanStatus.status === "done") {
      setPhase("done")
    } else if (scanStatus.status === "error") {
      setScanError(scanStatus.error ?? "Scan failed")
      setPhase("idle")
      setJobId(null)
    }
  }, [jobId, scanStatus, phase])

  const { data: sshKeys } = useQuery<SSHKey[]>({
    queryKey: ["ssh-keys"],
    queryFn: () => apiFetch<SSHKey[]>("/api/ssh-keys"),
  })

  const { data: groups } = useQuery<HostGroup[]>({
    queryKey: ["groups"],
    queryFn: () => apiFetch<HostGroup[]>("/api/groups"),
  })

  const handleScan = form.handleSubmit(async (data) => {
    setScanError(null)
    setAddResult(null)
    setAddError(null)
    setSelectedHosts(new Set())
    setJobId(null)
    setPhase("scanning")
    try {
      const status = await apiFetch<ScanStatus>("/api/discovery/scan", {
        method: "POST",
        body: JSON.stringify({ cidr: data.cidr }),
      })
      setJobId(status.job_id)
    } catch (err) {
      setScanError(err instanceof Error ? err.message : "Failed to start scan")
      setPhase("idle")
    }
  })

  const handleAdd = async () => {
    setAddError(null)
    setPhase("adding")
    try {
      const result = await apiFetch<AddResult>("/api/discovery/add-hosts", {
        method: "POST",
        body: JSON.stringify({
          ips: Array.from(selectedHosts),
          ssh_key_id: selectedKeyId,
          group_ids: selectedGroupIds,
        }),
      })
      setAddResult(result)
      setPhase("done")
    } catch (err) {
      setAddError(err instanceof Error ? err.message : "Failed to add hosts")
      setPhase("done")
    }
  }

  const toggleHost = (ip: string) => {
    setSelectedHosts((prev) => {
      const next = new Set(prev)
      if (next.has(ip)) next.delete(ip)
      else next.add(ip)
      return next
    })
  }

  const toggleAll = () => {
    if (!scanStatus?.hosts_found) return
    const allIps = scanStatus.hosts_found.map((h) => h.ip)
    if (selectedHosts.size === allIps.length) {
      setSelectedHosts(new Set())
    } else {
      setSelectedHosts(new Set(allIps))
    }
  }

  useEffect(() => {
    if (!sshKeys || selectedKeyId !== null) return
    const defaultKey = sshKeys.find((k) => k.is_default)
    if (defaultKey) setSelectedKeyId(defaultKey.id)
  }, [sshKeys, selectedKeyId])

  const hostsFound = scanStatus?.hosts_found ?? []
  const progressPct = scanStatus && scanStatus.total > 0
    ? Math.round((scanStatus.progress / scanStatus.total) * 100)
    : 0

  const discoveryColumns: ColumnDef<DiscoveredHost>[] = [
    {
      key: "select",
      label: "",
      cell: (host) => (
        <input
          type="checkbox"
          checked={selectedHosts.has(host.ip)}
          onChange={() => toggleHost(host.ip)}
          disabled={phase === "adding"}
          className="rounded border-input"
        />
      ),
      defaultWidth: 40,
      resizable: false,
      sortable: false,
    },
    {
      key: "ip",
      label: "IP Address",
      accessor: (h) => h.ip,
      cell: (h) => <span className="font-mono text-slate-300">{h.ip}</span>,
      defaultWidth: 160,
      filter: { type: "text", placeholder: "e.g. 10.0.1" },
    },
    {
      key: "hostname",
      label: "Hostname",
      accessor: (h) => h.hostname ?? "",
      cell: (h) => <span className="text-white">{h.hostname ?? "—"}</span>,
      defaultWidth: 200,
      filter: { type: "text", placeholder: "e.g. web-01" },
    },
    {
      key: "ssh_status",
      label: "Status",
      accessor: (h) => h.ssh_status,
      cell: (h) => h.ssh_status === "open" ? (
        <span className="inline-flex items-center rounded-full bg-green-500/10 px-2 py-0.5 text-xs font-medium text-green-400 ring-1 ring-green-500/20">
          SSH Open
        </span>
      ) : (
        <span className="inline-flex items-center rounded-full bg-amber-500/10 px-2 py-0.5 text-xs font-medium text-amber-400 ring-1 ring-amber-500/20">
          SSH Refused
        </span>
      ),
      defaultWidth: 140,
      filter: { type: "enum", options: [{label:"SSH Open",value:"open"},{label:"SSH Refused",value:"refused"}] },
    },
  ]

  return (
    <div className="max-w-3xl space-y-6">
      <Breadcrumb items={[{ label: "Hosts", href: "/hosts" }, { label: "Discovery", href: "/hosts/discovery" }, { label: "Scan now" }]} />
      <div>
        <h1 className="text-2xl font-bold text-white">Discover Hosts</h1>
        <p className="text-slate-400 text-sm mt-1">
          Scan a network range to find SSH-reachable hosts
        </p>
      </div>

      <div className="rounded-lg border border-slate-700 bg-slate-900 p-6">
        <form onSubmit={handleScan} noValidate className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="cidr">Network CIDR</Label>
            <div className="flex gap-3">
              <Input
                id="cidr"
                type="text"
                placeholder="192.168.1.0/24"
                {...form.register("cidr")}
                disabled={phase === "scanning" || phase === "adding"}
                className="font-mono"
              />
              <Button
                type="submit"
                disabled={!cidrValid || phase === "scanning" || phase === "adding"}
              >
                {phase === "scanning" ? "Scanning…" : "Scan Network"}
              </Button>
            </div>
            {form.formState.errors.cidr && cidrValue && (
              <p className="text-xs text-red-400">
                {form.formState.errors.cidr.message}
              </p>
            )}
          </div>
        </form>
      </div>

      {phase === "scanning" && scanStatus && (
        <div className="rounded-lg border border-slate-700 bg-slate-900 p-4 space-y-3">
          <div className="flex items-center gap-3">
            <svg className="animate-spin h-4 w-4 text-blue-400" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
            </svg>
            <span className="text-sm text-slate-300">
              {scanStatus.status === "pending"
                ? "Scan starting…"
                : `Scanning ${scanStatus.progress} / ${scanStatus.total} hosts…`}
            </span>
          </div>
          <div className="h-2 rounded-full bg-slate-700 overflow-hidden">
            <div
              className="h-full rounded-full bg-blue-500 transition-all duration-300"
              style={{ width: `${progressPct}%` }}
            />
          </div>
        </div>
      )}

      {phase === "scanning" && !scanStatus && (
        <div className="rounded-lg border border-slate-700 bg-slate-900 p-4">
          <div className="flex items-center gap-3">
            <svg className="animate-spin h-4 w-4 text-blue-400" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
            </svg>
            <span className="text-sm text-slate-300">Starting scan…</span>
          </div>
        </div>
      )}

      {scanError && (
        <div className="rounded-lg border border-red-800 bg-red-950/30 px-4 py-3 text-red-400 text-sm">
          {scanError}
        </div>
      )}

      {phase !== "scanning" && phase !== "idle" && hostsFound.length === 0 && !addResult && (
        <div className="rounded-lg border border-slate-700 bg-slate-900 px-4 py-8 text-center text-slate-400">
          No new SSH hosts found on this network.
        </div>
      )}

      {hostsFound.length > 0 && (phase === "done" || phase === "adding") && (
        <>
          {hostsFound.length > 0 && (phase === "done" || phase === "adding") && (
            <div className="flex items-center gap-2 mb-1">
              <input
                type="checkbox"
                checked={selectedHosts.size === hostsFound.length && hostsFound.length > 0}
                onChange={toggleAll}
                disabled={phase === "adding"}
                className="rounded border-input"
              />
              <span className="text-sm text-slate-400">Select all</span>
            </div>
          )}
          <DataTable<DiscoveredHost>
            tableId="discovery-results"
            columns={discoveryColumns}
            data={hostsFound}
            getRowKey={(h) => h.ip}
            emptyMessage="No hosts found."
          />
        </>
      )}

      {(phase === "done" || phase === "adding") && selectedHosts.size > 0 && !addResult && (
        <div className="rounded-lg border border-slate-700 bg-slate-900 p-6 space-y-4">
          <h2 className="text-lg font-semibold text-white">
            Add {selectedHosts.size} Host{selectedHosts.size !== 1 ? "s" : ""}
          </h2>

          <div className="space-y-2">
            <Label htmlFor="ssh_key">SSH Key</Label>
            <select
              id="ssh_key"
              value={selectedKeyId ?? ""}
              onChange={(e) => setSelectedKeyId(e.target.value ? Number(e.target.value) : null)}
              disabled={phase === "adding"}
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
              onChange={setSelectedGroupIds}
              disabled={phase === "adding"}
              label="Groups (optional)"
            />
          )}

          {addError && (
            <div className="rounded-lg border border-red-800 bg-red-950/30 px-4 py-3 text-red-400 text-sm">
              {addError}
            </div>
          )}

          <Button onClick={handleAdd} disabled={phase === "adding"}>
            {phase === "adding"
              ? "Adding…"
              : `Add ${selectedHosts.size} Host${selectedHosts.size !== 1 ? "s" : ""}`}
          </Button>
        </div>
      )}

      {addResult && (
        <div className="space-y-3">
          {addResult.added > 0 && (
            <div className="rounded-lg border border-green-800 bg-green-950/30 px-4 py-4 space-y-2">
              <p className="text-green-400 text-sm font-medium">
                {addResult.added} host{addResult.added !== 1 ? "s" : ""} added
                {addResult.skipped > 0 && (
                  <span className="text-slate-400 font-normal">
                    {" "}({addResult.skipped} already existed)
                  </span>
                )}
              </p>
              <Link href="/hosts" className="text-sm text-blue-400 hover:text-blue-300 underline underline-offset-2">
                View all hosts →
              </Link>
            </div>
          )}
          {addResult.failed.length > 0 && (
            <div className="rounded-lg border border-red-800 bg-red-950/30 px-4 py-4 space-y-2">
              <p className="text-red-400 text-sm font-medium">
                {addResult.failed.length} host{addResult.failed.length !== 1 ? "s" : ""} failed SSH verification
              </p>
              <ul className="space-y-1">
                {addResult.failed.map((f) => (
                  <li key={f.ip} className="text-sm text-slate-400">
                    <span className="font-mono text-slate-300">{f.ip}</span>
                    {" — "}{f.error}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {addResult.added === 0 && addResult.failed.length === 0 && addResult.skipped > 0 && (
            <div className="rounded-lg border border-slate-700 bg-slate-900 px-4 py-4">
              <p className="text-slate-400 text-sm">All {addResult.skipped} hosts already existed.</p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
