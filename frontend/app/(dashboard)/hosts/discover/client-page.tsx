"use client"

import { useState, useEffect } from "react"
import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { apiFetch } from "@/lib/api"
import { Banner, Field, Meter, Panel, Table, Tag } from "@/components/ld"
import { GroupMultiSelect } from "@/components/group-multi-select"
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
  cidr: z.string().min(1, "CIDR is required").regex(/^(\d{1,3}\.){3}\d{1,3}\/\d{1,2}$/, "Invalid CIDR format (e.g., 192.168.1.0/24)"),
})
type CidrInput = z.infer<typeof cidrSchema>

type Phase = "idle" | "scanning" | "done" | "adding"

/** The manual scan-now form, embedded in the Discovery screen's Scan now
 *  tab (no route of its own). */
export default function DiscoverHostsPage() {
  const [phase, setPhase] = useState<Phase>("idle")
  const [jobId, setJobId] = useState<string | null>(null)
  const [scanError, setScanError] = useState<string | null>(null)
  const [selectedHosts, setSelectedHosts] = useState<Set<string | number>>(new Set())
  const [selectedKeyId, setSelectedKeyId] = useState<number | null>(null)
  const [selectedGroupIds, setSelectedGroupIds] = useState<number[]>([])
  const [addResult, setAddResult] = useState<AddResult | null>(null)
  const [addError, setAddError] = useState<string | null>(null)

  const form = useForm<CidrInput>({ resolver: zodResolver(cidrSchema), defaultValues: { cidr: "" }, mode: "onChange" })
  const cidrValue = form.watch("cidr")
  const cidrValid = !form.formState.errors.cidr && cidrValue.length > 0

  const { data: scanStatus } = useQuery<ScanStatus>({
    queryKey: ["discovery-scan", jobId],
    queryFn: () => apiFetch<ScanStatus>(`/api/discovery/scan/${jobId}`),
    enabled: !!jobId,
    refetchInterval: (query) => {
      const data = query.state.data
      return !data || data.status === "pending" || data.status === "running" ? 2000 : false
    },
  })

  useEffect(() => {
    if (!jobId || !scanStatus || phase !== "scanning") return
    if (scanStatus.status === "done") setPhase("done")
    else if (scanStatus.status === "error") {
      setScanError(scanStatus.error ?? "Scan failed")
      setPhase("idle")
      setJobId(null)
    }
  }, [jobId, scanStatus, phase])

  const { data: sshKeys } = useQuery<SSHKey[]>({ queryKey: ["ssh-keys"], queryFn: () => apiFetch<SSHKey[]>("/api/ssh-keys") })
  const { data: groups } = useQuery<HostGroup[]>({ queryKey: ["groups"], queryFn: () => apiFetch<HostGroup[]>("/api/groups") })

  useEffect(() => {
    if (!sshKeys || selectedKeyId !== null) return
    const defaultKey = sshKeys.find((k) => k.is_default)
    if (defaultKey) setSelectedKeyId(defaultKey.id)
  }, [sshKeys, selectedKeyId])

  const handleScan = form.handleSubmit(async (data) => {
    setScanError(null)
    setAddResult(null)
    setAddError(null)
    setSelectedHosts(new Set())
    setJobId(null)
    setPhase("scanning")
    try {
      const status = await apiFetch<ScanStatus>("/api/discovery/scan", { method: "POST", body: JSON.stringify({ cidr: data.cidr }) })
      setJobId(status.job_id)
    } catch (err) {
      setScanError(err instanceof Error ? err.message : "Failed to start scan")
      setPhase("idle")
    }
  })

  async function handleAdd() {
    setAddError(null)
    setPhase("adding")
    try {
      const result = await apiFetch<AddResult>("/api/discovery/add-hosts", {
        method: "POST",
        body: JSON.stringify({ ips: Array.from(selectedHosts), ssh_key_id: selectedKeyId, group_ids: selectedGroupIds }),
      })
      setAddResult(result)
      setPhase("done")
    } catch (err) {
      setAddError(err instanceof Error ? err.message : "Failed to add hosts")
      setPhase("done")
    }
  }

  const hostsFound = scanStatus?.hosts_found ?? []
  const progressPct = scanStatus && scanStatus.total > 0 ? Math.round((scanStatus.progress / scanStatus.total) * 100) : 0

  return (
    <div className="scroll flex flex-1 flex-col gap-3 p-3.5" style={{ maxWidth: 720 }}>
      <Panel title="scan a network range" pad={11}>
        <form onSubmit={handleScan} noValidate className="flex flex-col gap-2">
          <div className="flex items-end gap-2">
            <Field label="network cidr" htmlFor="cidr" error={cidrValue ? form.formState.errors.cidr?.message : undefined} className="flex-1">
              <input id="cidr" className="inp mono" placeholder="192.168.1.0/24" disabled={phase === "scanning" || phase === "adding"} {...form.register("cidr")} />
            </Field>
            <button type="submit" className="btn btn-primary" disabled={!cidrValid || phase === "scanning" || phase === "adding"}>
              {phase === "scanning" ? "Scanning…" : "Scan network"}
            </button>
          </div>
        </form>
      </Panel>

      {phase === "scanning" && (
        <Panel pad={11}>
          <Meter pct={progressPct} label={scanStatus ? (scanStatus.status === "pending" ? "scan starting…" : `scanning ${scanStatus.progress} / ${scanStatus.total} hosts`) : "starting scan…"} />
        </Panel>
      )}

      {scanError && <Banner tone="danger">{scanError}</Banner>}

      {phase !== "scanning" && phase !== "idle" && hostsFound.length === 0 && !addResult && <Banner tone="idle">No new SSH hosts found on this network.</Banner>}

      {hostsFound.length > 0 && (phase === "done" || phase === "adding") && (
        <Panel title="discovered hosts" pad={0}>
          <Table<DiscoveredHost>
            cols={[
              { k: "ip", label: "ip address", w: "140px", sortable: false, cell: (h) => <span className="mono text-text">{h.ip}</span> },
              { k: "hostname", label: "hostname", w: "minmax(140px,1fr)", sortable: false, cell: (h) => <span className="text-text-2">{h.hostname ?? "—"}</span> },
              { k: "ssh", label: "ssh", w: "120px", sortable: false, cell: (h) => (h.ssh_status === "open" ? <Tag tone="ok">open</Tag> : <Tag tone="warn">refused</Tag>) },
            ]}
            rows={hostsFound}
            keyOf={(h) => h.ip}
            selected={selectedHosts}
            onSelect={setSelectedHosts}
            empty="No hosts found."
          />
        </Panel>
      )}

      {(phase === "done" || phase === "adding") && selectedHosts.size > 0 && !addResult && (
        <Panel title={`add ${selectedHosts.size} host${selectedHosts.size !== 1 ? "s" : ""}`} pad={11}>
          <div className="flex flex-col gap-2.5">
            <Field as="div" label="ssh key">
              <select className="inp" value={selectedKeyId ?? ""} disabled={phase === "adding"} onChange={(e) => setSelectedKeyId(e.target.value ? Number(e.target.value) : null)}>
                <option value="">No SSH key</option>
                {sshKeys?.map((key) => <option key={key.id} value={key.id}>{key.name}{key.is_default ? " (default)" : ""}</option>)}
              </select>
            </Field>

            {groups && groups.length > 0 && <GroupMultiSelect groups={groups} selected={selectedGroupIds} onChange={setSelectedGroupIds} disabled={phase === "adding"} label="groups (optional)" />}

            {addError && <Banner tone="danger">{addError}</Banner>}

            <button type="button" className="btn btn-primary" disabled={phase === "adding"} onClick={handleAdd}>
              {phase === "adding" ? "Adding…" : `Add ${selectedHosts.size} host${selectedHosts.size !== 1 ? "s" : ""}`}
            </button>
          </div>
        </Panel>
      )}

      {addResult && (
        <div className="flex flex-col gap-2">
          {addResult.added > 0 && (
            <Banner tone="ok" action={<Link href="/hosts" className="btn btn-sm btn-ghost hover:no-underline">view all hosts →</Link>}>
              {addResult.added} host{addResult.added !== 1 ? "s" : ""} added{addResult.skipped > 0 ? ` (${addResult.skipped} already existed)` : ""}
            </Banner>
          )}
          {addResult.failed.length > 0 && (
            <Banner tone="danger">
              {addResult.failed.length} host{addResult.failed.length !== 1 ? "s" : ""} failed SSH verification: {addResult.failed.map((f) => `${f.ip} — ${f.error}`).join("; ")}
            </Banner>
          )}
          {addResult.added === 0 && addResult.failed.length === 0 && addResult.skipped > 0 && <Banner tone="idle">All {addResult.skipped} hosts already existed.</Banner>}
        </div>
      )}
    </div>
  )
}
