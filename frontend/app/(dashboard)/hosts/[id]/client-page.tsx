"use client"

import { useEffect, useMemo, useState } from "react"
import { useParams, useSearchParams } from "next/navigation"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { apiFetch, ApiError } from "@/lib/api"
import { collectHostState } from "@/lib/collect-state"
import { useSyncTray } from "@/lib/sync-tray"
import { statusDef, ageLabel } from "@/lib/fleet"
import { MODULES } from "@/lib/modules"
import { Banner, Confirm, Dot, PageHead, Seg, Status, Tabs } from "@/components/ld"
import { RunActionButton } from "@/components/run-action-button"
import { moduleLabel } from "@/components/module-diff-view"
import type { Host, HostGroup, ModuleCurrentState, ModuleDiff, SSHKey } from "@/lib/types"
import { OverviewTab } from "./_tabs/overview"
import { MetricsTab } from "./_tabs/metrics"
import { TerminalTab } from "./_tabs/terminal"
import { ActivityTab } from "./_tabs/activity"
import { FirewallTab } from "./_tabs/config/firewall"
import { ServicesTab } from "./_tabs/config/services"
import { HostsFileTab } from "./_tabs/config/hosts-file"
import { UsersTab } from "./_tabs/config/users"
import { CronTab } from "./_tabs/config/cron"
import { PackagesTab } from "./_tabs/config/packages"
import { CaCertsTab } from "./_tabs/config/ca-certs"
import { ResolverTab } from "./_tabs/config/resolver"
import { EditHostDialog } from "./_dialogs/edit-host"
import { SyncPreviewDialog, type SyncPreviewState } from "./_dialogs/sync-preview"

type HostTab = "overview" | "groups" | "rules" | "services" | "hosts-file" | "users" | "cron-jobs" | "packages" | "ca-certs" | "dns" | "actions" | "schedules" | "metrics"

/**
 * The page keeps its twelve tab values — every query, refresh key and
 * sync handler is keyed on them — but presents five: Overview · Config ·
 * Metrics · Terminal · Activity. The eight module tabs sit behind Config
 * with a module sub-nav; group membership is Overview content; actions
 * and schedules are Activity. Legacy `?tab=rules` links keep working.
 */
const MODULE_TABS: HostTab[] = ["rules", "services", "hosts-file", "users", "cron-jobs", "packages", "ca-certs", "dns"]
type PrimaryTab = "overview" | "config" | "metrics" | "terminal" | "activity"
function primaryOf(t: HostTab): PrimaryTab {
  if (MODULE_TABS.includes(t)) return "config"
  if (t === "actions" || t === "schedules") return "activity"
  if (t === "metrics") return "metrics"
  return "overview"
}

const tabToModule: Record<string, string> = {
  rules: "firewall", services: "services", "hosts-file": "hosts-file", users: "linux-users",
  "cron-jobs": "cron", packages: "packages", dns: "resolver",
}

export default function HostDetailPage() {
  const params = useParams()
  const id = Number(params.id)
  const queryClient = useQueryClient()
  const { registerSync, jobs: trayJobs } = useSyncTray()
  const searchParams = useSearchParams()
  const initialTab = (searchParams.get("tab") as HostTab) || "overview"
  const [activeTab, setActiveTab] = useState<HostTab>(initialTab)
  const [lastModuleTab, setLastModuleTab] = useState<HostTab>(MODULE_TABS.includes(initialTab) ? initialTab : "rules")
  const [primaryTab, setPrimaryTab] = useState<PrimaryTab>(searchParams.get("tab") === "terminal" ? "terminal" : primaryOf(initialTab))

  const { data: host, isLoading: hostLoading, error: hostError } = useQuery<Host>({
    queryKey: ["host", id],
    queryFn: () => apiFetch<Host>(`/api/hosts/${id}`),
    enabled: !!id,
  })
  const { data: sshKeys } = useQuery<SSHKey[]>({ queryKey: ["ssh-keys"], queryFn: () => apiFetch<SSHKey[]>("/api/ssh-keys") })
  const { data: groups } = useQuery<HostGroup[]>({ queryKey: ["groups"], queryFn: () => apiFetch<HostGroup[]>("/api/groups") })
  const currentStateQuery = useQuery<ModuleCurrentState[]>({
    queryKey: ["host-current-state", id],
    queryFn: () => apiFetch<ModuleCurrentState[]>(`/api/hosts/${id}/current-state`),
    enabled: !!id,
  })

  const [editOpen, setEditOpen] = useState(false)
  const [collecting, setCollecting] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [trustKeyOpen, setTrustKeyOpen] = useState(false)
  const [trustingKey, setTrustingKey] = useState(false)

  const [syncPreview, setSyncPreview] = useState<SyncPreviewState | null>(null)
  const [applying, setApplying] = useState(false)
  const [applyError, setApplyError] = useState<string | null>(null)
  const [pendingSyncJobs, setPendingSyncJobs] = useState<{ id: number; keys: string[][] }[]>([])

  const tabQueryKeys: Record<string, string[][]> = useMemo(() => ({
    overview: [["host", String(id)], ["host-current-state", String(id)], ["host-metrics", String(id)]],
    groups: [["host", String(id)], ["groups"]],
    rules: [["host-effective-rules", String(id)], ["host-current-state", String(id)]],
    services: [["host-effective-services", String(id)], ["host-service-overrides", String(id)]],
    "hosts-file": [["host-effective-hosts-entries", String(id)], ["host-hosts-overrides", String(id)]],
    users: [["host-effective-linux-users", String(id)], ["host-effective-linux-groups", String(id)]],
    "cron-jobs": [["host-effective-cron-jobs", String(id)], ["host-cron-overrides", String(id)]],
    packages: [["host-effective-packages", String(id)], ["host-package-overrides", String(id)], ["host-effective-repos", String(id)]],
    "ca-certs": [["host-effective-ca-certs", String(id)], ["host-ca-cert-overrides", String(id)], ["host-ca-cert-runs", String(id)]],
    dns: [["host-effective-resolver", String(id)], ["host-resolver-override", String(id)]],
    actions: [["actions-catalog"], ["action-runs", "host", String(id)]],
  }), [id])

  const openModulePreview = async (tabKey: string) => {
    const moduleName = tabToModule[tabKey]
    setApplyError(null)
    setSyncPreview({ scope: "module", tabKey, module: moduleName, loading: true, error: null, diffs: null })
    try {
      const diffs = await apiFetch<ModuleDiff[]>(`/api/sync/hosts/${id}/preview`, { method: "POST", body: JSON.stringify({ module_filter: [moduleName] }) })
      setSyncPreview((p) => (p ? { ...p, loading: false, diffs } : p))
    } catch (e) {
      setSyncPreview((p) => (p ? { ...p, loading: false, error: e instanceof Error ? e.message : "Preview failed" } : p))
    }
  }

  const openSyncAllPreview = async () => {
    setApplyError(null)
    setSyncPreview({ scope: "all", tabKey: null, module: null, loading: true, error: null, diffs: null })
    try {
      const diffs = await apiFetch<ModuleDiff[]>(`/api/sync/hosts/${id}/preview`, { method: "POST", body: JSON.stringify({ module_filter: null }) })
      setSyncPreview((p) => (p ? { ...p, loading: false, diffs } : p))
    } catch (e) {
      setSyncPreview((p) => (p ? { ...p, loading: false, error: e instanceof Error ? e.message : "Preview failed" } : p))
    }
  }

  const closeSyncPreview = () => {
    if (applying) return
    setSyncPreview(null)
    setApplyError(null)
  }

  const applyModuleSync = async () => {
    if (!syncPreview?.tabKey || !syncPreview.module) return
    const { tabKey, module } = syncPreview
    setApplying(true)
    setApplyError(null)
    try {
      const resp = await apiFetch<{ job_id: number; status: string }>(`/api/sync/hosts/${id}/bulk`, { method: "POST", body: JSON.stringify({ module_filter: [module] }) })
      registerSync({ label: `${host?.hostname ?? "Host"} — ${moduleLabel(module)}`, jobIds: [resp.job_id] })
      setPendingSyncJobs((prev) => [...prev, { id: resp.job_id, keys: tabQueryKeys[tabKey] ?? [] }])
      setApplying(false)
      setSyncPreview(null)
    } catch (e) {
      setApplyError(e instanceof Error ? e.message : "Sync failed")
      setApplying(false)
    }
  }

  const applyBulkSync = async () => {
    const modulesToApply = (syncPreview?.diffs ?? []).filter((d) => d.has_changes && !d.error).map((d) => d.module)
    if (modulesToApply.length === 0) return
    setApplying(true)
    setApplyError(null)
    try {
      const resp = await apiFetch<{ job_id: number; status: string }>(`/api/sync/hosts/${id}/bulk`, { method: "POST", body: JSON.stringify({ module_filter: modulesToApply }) })
      registerSync({ label: `${host?.hostname ?? "Host"} — Sync All`, jobIds: [resp.job_id] })
      setPendingSyncJobs((prev) => [...prev, { id: resp.job_id, keys: Object.values(tabQueryKeys).flat() }])
      setApplying(false)
      setSyncPreview(null)
    } catch (e) {
      setApplyError(e instanceof Error ? e.message : "Sync failed")
      setApplying(false)
    }
  }

  useEffect(() => {
    if (pendingSyncJobs.length === 0) return
    const TERMINAL = new Set(["success", "failed", "cancelled"])
    const finished = pendingSyncJobs.filter((p) => TERMINAL.has(trayJobs[p.id]?.status ?? ""))
    if (finished.length === 0) return
    const keys: string[][] = [["host", String(id)], ["host-current-state", String(id)], ...finished.flatMap((p) => p.keys)]
    void (async () => { for (const key of keys) await queryClient.invalidateQueries({ queryKey: key }) })()
    const finishedIds = new Set(finished.map((p) => p.id))
    setPendingSyncJobs((prev) => prev.filter((p) => !finishedIds.has(p.id)))
  }, [trayJobs, pendingSyncJobs, id, queryClient])

  async function handleCollectAll() {
    setCollecting(true)
    try {
      const { notices } = await collectHostState(id)
      await queryClient.invalidateQueries({ queryKey: ["host-current-state", id] })
      await queryClient.invalidateQueries({ queryKey: ["host", id] })
      for (const w of notices) toast.warning(w, { duration: 10000 })
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Operation failed")
    }
    setCollecting(false)
  }

  async function handleRefresh() {
    setRefreshing(true)
    for (const key of tabQueryKeys[activeTab] ?? []) await queryClient.invalidateQueries({ queryKey: key })
    await queryClient.invalidateQueries({ queryKey: ["host", String(id)] })
    setRefreshing(false)
  }

  const moduleStatus = (m: (typeof MODULES)[number]) => currentStateQuery.data?.find((r) => m.statusTypes.includes(r.module_type))?.sync_status
  const errors = currentStateQuery.data?.filter((m) => m.error_message) ?? []
  const errorDetail = (() => {
    if (!errors.length) return null
    const byMessage = new Map<string, string[]>()
    for (const m of errors) {
      const msg = m.error_message!
      const existing = byMessage.get(msg)
      if (existing) existing.push(m.module_type)
      else byMessage.set(msg, [m.module_type])
    }
    return byMessage.size === 1
      ? byMessage.keys().next().value
      : [...byMessage.entries()].map(([msg, mods]) => `${msg} (${mods.join(", ")})`).join("; ")
  })()

  if (!hostLoading && hostError) {
    return <PageHead crumbs={[{ label: "fleet", href: "/hosts" }]} title="Host" sub="That host no longer exists." />
  }

  return (
    <>
      <PageHead
        crumbs={[{ label: "fleet", href: "/hosts" }, { label: "hosts", href: "/hosts" }]}
        title={host ? <><span className="mono">{host.hostname}</span> <Status s={host.sync_status} /></> : hostLoading ? "Loading…" : `Host #${id}`}
        sub={host ? <>{host.ip_address} · {host.os_pretty_name ?? "not collected"} · last sync {ageLabel(host.last_sync_at)}</> : undefined}
        actions={
          host && (
            <>
              <RunActionButton scope="host" targetId={id} targetLabel={host.hostname} host={host} />
              <button type="button" className="btn btn-sm btn-ghost" disabled={refreshing} title="Refresh data for the current tab" onClick={handleRefresh}>
                {refreshing ? "refreshing…" : "refresh"}
              </button>
              {primaryTab === "overview" && (
                <>
                  <button type="button" className="btn btn-sm btn-ghost" disabled={collecting || !host.ssh_key_id} title={host.ssh_key_id ? "Collect current state for all modules" : "No SSH key assigned"} onClick={handleCollectAll}>
                    {collecting ? "collecting…" : "collect all"}
                  </button>
                  <button type="button" className="btn btn-sm" disabled={!host.ssh_key_id || !!syncPreview} title={host.ssh_key_id ? "Preview and sync all modules to this host" : "No SSH key assigned"} onClick={openSyncAllPreview}>
                    sync all
                  </button>
                  {!host.ssh_host_key_entry && (
                    <button type="button" className="btn btn-sm btn-ghost text-warn" disabled={trustingKey} onClick={() => setTrustKeyOpen(true)}>
                      trust new host key
                    </button>
                  )}
                </>
              )}
              <button type="button" className="btn btn-sm btn-ghost" onClick={() => setEditOpen(true)}>edit</button>
            </>
          )
        }
      >
        <div className="flex flex-wrap items-center gap-3">
          <Tabs
            tabs={[
              { k: "overview", label: "Overview" },
              { k: "config", label: "Config" },
              { k: "metrics", label: "Metrics" },
              { k: "terminal", label: "Terminal" },
              { k: "activity", label: "Activity" },
            ]}
            value={primaryTab}
            onChange={(k) => {
              if (k === "terminal") { setPrimaryTab("terminal"); return }
              if (k === "config") { setPrimaryTab("config"); setActiveTab(MODULE_TABS.includes(activeTab) ? activeTab : lastModuleTab); return }
              if (k === "activity") { setPrimaryTab("activity"); setActiveTab(activeTab === "schedules" ? "schedules" : "actions"); return }
              setPrimaryTab(k as PrimaryTab)
              setActiveTab(k as HostTab)
            }}
            counts={{ config: MODULES.length }}
          />
          {primaryTab === "config" && (
            <div className="ml-auto flex flex-wrap items-center gap-1">
              {MODULES.map((m) => {
                const on = activeTab === m.hostTab
                const st = moduleStatus(m)
                return (
                  <button
                    key={m.id}
                    type="button"
                    onClick={() => { setActiveTab(m.hostTab as HostTab); setLastModuleTab(m.hostTab as HostTab) }}
                    aria-pressed={on}
                    className="flex items-center gap-1.5 rounded-r px-2 py-1 text-[11.5px]"
                    style={{ background: on ? "var(--surface-3)" : "transparent", color: on ? "var(--text)" : "var(--text-2)", fontWeight: on ? 600 : 400 }}
                    title={st ? `${m.label}: ${statusDef(st).label}` : m.label}
                  >
                    {st && st !== "unknown" && <Dot tone={statusDef(st).tone} />}
                    {m.label}
                  </button>
                )
              })}
            </div>
          )}
          {primaryTab === "activity" && (
            <div className="ml-auto">
              <Seg sm value={activeTab === "schedules" ? "schedules" : "actions"} onChange={(k) => setActiveTab(k as HostTab)} options={[{ k: "actions", label: "Actions" }, { k: "schedules", label: "Schedules" }]} />
            </div>
          )}
        </div>
      </PageHead>

      {errorDetail != null && <Banner tone="danger" flush>Sync check encountered errors. {errorDetail}</Banner>}

      {primaryTab === "overview" && <OverviewTab hostId={id} host={host} groups={groups} />}

      {primaryTab === "config" && host && (
        <>
          {activeTab === "rules" && <FirewallTab hostId={id} host={host} currentState={currentStateQuery.data} syncBusy={!!syncPreview} onSync={() => openModulePreview("rules")} />}
          {activeTab === "services" && <ServicesTab hostId={id} host={host} currentState={currentStateQuery.data} syncBusy={!!syncPreview} onSync={() => openModulePreview("services")} />}
          {activeTab === "hosts-file" && <HostsFileTab hostId={id} host={host} currentState={currentStateQuery.data} syncBusy={!!syncPreview} onSync={() => openModulePreview("hosts-file")} />}
          {activeTab === "users" && <UsersTab hostId={id} currentState={currentStateQuery.data} syncBusy={!!syncPreview} onSync={() => openModulePreview("users")} />}
          {activeTab === "cron-jobs" && <CronTab hostId={id} currentState={currentStateQuery.data} syncBusy={!!syncPreview} onSync={() => openModulePreview("cron-jobs")} />}
          {activeTab === "packages" && <PackagesTab hostId={id} currentState={currentStateQuery.data} syncBusy={!!syncPreview} onSync={() => openModulePreview("packages")} />}
          {activeTab === "ca-certs" && <CaCertsTab hostId={id} host={host} />}
          {activeTab === "dns" && <ResolverTab hostId={id} currentState={currentStateQuery.data} syncBusy={!!syncPreview} onSync={() => openModulePreview("dns")} />}
        </>
      )}

      {primaryTab === "metrics" && <MetricsTab hostId={id} />}
      {primaryTab === "terminal" && host && <TerminalTab hostId={id} hostname={host.hostname} />}
      {primaryTab === "activity" && <ActivityTab hostId={id} host={host} view={activeTab === "schedules" ? "schedules" : "actions"} />}

      {editOpen && host && <EditHostDialog hostId={id} host={host} sshKeys={sshKeys} groups={groups} onClose={() => setEditOpen(false)} />}

      {host && (
        <Confirm
          open={trustKeyOpen}
          onOpenChange={(open) => { if (!trustingKey) setTrustKeyOpen(open) }}
          title={`Trust new host key for ${host.hostname}?`}
          description={`This clears the stored SSH host key. The next connection will accept whatever key ${host.hostname} (${host.ip_address}) presents and store it. If the host was not intentionally re-keyed, an attacker may be intercepting the connection.`}
          confirmLabel={trustingKey ? "Trusting…" : "Trust new key"}
          variant="destructive"
          loading={trustingKey}
          onConfirm={async () => {
            setTrustingKey(true)
            try {
              await apiFetch(`/api/hosts/${id}/trust-host-key`, { method: "POST" })
              await queryClient.invalidateQueries({ queryKey: ["host", String(id)] })
              toast.success("Host key cleared. Next connection will re-TOFU.")
            } catch (err) {
              toast.error(err instanceof Error ? err.message : "Failed to clear host key")
            } finally {
              setTrustingKey(false)
              setTrustKeyOpen(false)
            }
          }}
        />
      )}

      {syncPreview && (
        <SyncPreviewDialog
          preview={syncPreview}
          applying={applying}
          applyError={applyError}
          onClose={closeSyncPreview}
          onApply={syncPreview.scope === "all" ? applyBulkSync : applyModuleSync}
        />
      )}
    </>
  )
}
