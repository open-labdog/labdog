"use client"

import { useState, useEffect } from "react"
import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { apiFetch } from "@/lib/api"
import type { SSHKey, HostGroup } from "@/lib/types"

// --- Types ---

interface DiscoveredHost {
  ip: string
  hostname: string | null
}

interface ScanStatus {
  job_id: string
  status: "pending" | "running" | "done" | "error"
  progress: number
  total: number
  hosts_found: DiscoveredHost[]
  error?: string
}

interface AddResult {
  added: number
  skipped: number
}

// --- Helpers ---

const CIDR_REGEX = /^(\d{1,3}\.){3}\d{1,3}\/\d{1,2}$/

type Phase = "idle" | "scanning" | "done" | "adding"

// --- Component ---

export default function DiscoverHostsPage() {
  // Phase
  const [phase, setPhase] = useState<Phase>("idle")

  // Scan
  const [cidr, setCidr] = useState("")
  const [jobId, setJobId] = useState<string | null>(null)
  const [scanError, setScanError] = useState<string | null>(null)

  // Selection
  const [selectedHosts, setSelectedHosts] = useState<Set<string>>(new Set())

  // Add
  const [selectedKeyId, setSelectedKeyId] = useState<number | null>(null)
  const [selectedGroupIds, setSelectedGroupIds] = useState<number[]>([])
  const [addResult, setAddResult] = useState<AddResult | null>(null)
  const [addError, setAddError] = useState<string | null>(null)

  // --- Polling for scan status ---
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

  // Transition to done/error when scan completes
  useEffect(() => {
    if (!scanStatus || phase !== "scanning") return
    if (scanStatus.status === "done") {
      setPhase("done")
    } else if (scanStatus.status === "error") {
      setScanError(scanStatus.error ?? "Scan failed")
      setPhase("idle")
      setJobId(null)
    }
  }, [scanStatus, phase])

  // --- Fetch SSH keys & groups ---
  const { data: sshKeys } = useQuery<SSHKey[]>({
    queryKey: ["ssh-keys"],
    queryFn: () => apiFetch<SSHKey[]>("/api/ssh-keys"),
  })

  const { data: groups } = useQuery<HostGroup[]>({
    queryKey: ["groups"],
    queryFn: () => apiFetch<HostGroup[]>("/api/groups"),
  })

  // --- Handlers ---

  const cidrValid = CIDR_REGEX.test(cidr)

  const handleScan = async () => {
    setScanError(null)
    setAddResult(null)
    setAddError(null)
    setSelectedHosts(new Set())
    setPhase("scanning")
    try {
      const status = await apiFetch<ScanStatus>("/api/discovery/scan", {
        method: "POST",
        body: JSON.stringify({ cidr }),
      })
      setJobId(status.job_id)
    } catch (err) {
      setScanError(err instanceof Error ? err.message : "Failed to start scan")
      setPhase("idle")
    }
  }

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

  const toggleGroup = (id: number) => {
    setSelectedGroupIds((prev) =>
      prev.includes(id) ? prev.filter((g) => g !== id) : [...prev, id]
    )
  }

  // Pre-select default SSH key
  useEffect(() => {
    if (!sshKeys || selectedKeyId !== null) return
    const defaultKey = sshKeys.find((k) => k.is_default)
    if (defaultKey) setSelectedKeyId(defaultKey.id)
  }, [sshKeys, selectedKeyId])

  const hostsFound = scanStatus?.hosts_found ?? []
  const progressPct = scanStatus && scanStatus.total > 0
    ? Math.round((scanStatus.progress / scanStatus.total) * 100)
    : 0

  return (
    <div className="max-w-3xl space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-white">Discover Hosts</h1>
        <p className="text-slate-400 text-sm mt-1">
          Scan a network range to find SSH-reachable hosts
        </p>
      </div>

      {/* 1. CIDR Input */}
      <div className="rounded-lg border border-slate-700 bg-slate-900 p-6">
        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="cidr">Network CIDR</Label>
            <div className="flex gap-3">
              <Input
                id="cidr"
                type="text"
                placeholder="192.168.1.0/24"
                value={cidr}
                onChange={(e) => setCidr(e.target.value)}
                disabled={phase === "scanning" || phase === "adding"}
                className="font-mono"
              />
              <Button
                onClick={handleScan}
                disabled={!cidrValid || phase === "scanning" || phase === "adding"}
              >
                {phase === "scanning" ? "Scanning…" : "Scan Network"}
              </Button>
            </div>
            {cidr && !cidrValid && (
              <p className="text-xs text-red-400">
                Enter a valid CIDR (e.g. 192.168.1.0/24)
              </p>
            )}
          </div>
        </div>
      </div>

      {/* 2. Progress */}
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

      {/* Scanning without status yet */}
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

      {/* 3. Error */}
      {scanError && (
        <div className="rounded-lg border border-red-800 bg-red-950/30 px-4 py-3 text-red-400 text-sm">
          {scanError}
        </div>
      )}

      {/* 4. Results */}
      {phase !== "scanning" && phase !== "idle" && hostsFound.length === 0 && !addResult && (
        <div className="rounded-lg border border-slate-700 bg-slate-900 px-4 py-8 text-center text-slate-400">
          No new SSH hosts found on this network.
        </div>
      )}

      {hostsFound.length > 0 && (phase === "done" || phase === "adding") && (
        <div className="rounded-lg border border-slate-700 bg-slate-900 overflow-hidden">
          <Table>
            <TableHeader>
              <TableRow className="border-slate-700">
                <TableHead className="w-10">
                  <input
                    type="checkbox"
                    checked={selectedHosts.size === hostsFound.length && hostsFound.length > 0}
                    onChange={toggleAll}
                    disabled={phase === "adding"}
                    className="rounded border-input"
                  />
                </TableHead>
                <TableHead>IP Address</TableHead>
                <TableHead>Hostname</TableHead>
                <TableHead>Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {hostsFound.map((host) => (
                <TableRow key={host.ip} className="border-slate-700">
                  <TableCell>
                    <input
                      type="checkbox"
                      checked={selectedHosts.has(host.ip)}
                      onChange={() => toggleHost(host.ip)}
                      disabled={phase === "adding"}
                      className="rounded border-input"
                    />
                  </TableCell>
                  <TableCell className="font-mono text-slate-300">{host.ip}</TableCell>
                  <TableCell className="text-white">{host.hostname ?? "—"}</TableCell>
                  <TableCell>
                    <span className="inline-flex items-center rounded-full bg-blue-500/10 px-2 py-0.5 text-xs font-medium text-blue-400 ring-1 ring-blue-500/20">
                      New
                    </span>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      {/* 5. Add section */}
      {(phase === "done" || phase === "adding") && selectedHosts.size > 0 && !addResult && (
        <div className="rounded-lg border border-slate-700 bg-slate-900 p-6 space-y-4">
          <h2 className="text-lg font-semibold text-white">
            Add {selectedHosts.size} Host{selectedHosts.size !== 1 ? "s" : ""}
          </h2>

          {/* SSH Key select */}
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

          {/* Groups multi-select */}
          {groups && groups.length > 0 && (
            <div className="space-y-2">
              <Label>Groups (optional)</Label>
              <div className="space-y-2 rounded-lg border border-input p-3 dark:bg-input/10">
                {groups.map((group) => (
                  <label key={group.id} className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={selectedGroupIds.includes(group.id)}
                      onChange={() => toggleGroup(group.id)}
                      disabled={phase === "adding"}
                      className="rounded border-input"
                    />
                    <span className="text-sm text-foreground">{group.name}</span>
                  </label>
                ))}
              </div>
            </div>
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

      {/* 6. Success */}
      {addResult && (
        <div className="rounded-lg border border-green-800 bg-green-950/30 px-4 py-4 space-y-2">
          <p className="text-green-400 text-sm font-medium">
            {addResult.added} host{addResult.added !== 1 ? "s" : ""} added
            {addResult.skipped > 0 && (
              <span className="text-slate-400 font-normal">
                {" "}({addResult.skipped} skipped)
              </span>
            )}
          </p>
          <Link href="/hosts" className="text-sm text-blue-400 hover:text-blue-300 underline underline-offset-2">
            View all hosts →
          </Link>
        </div>
      )}
    </div>
  )
}
