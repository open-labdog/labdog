"use client"

import { useState, useEffect, type FormEvent } from "react"
import { useParams } from "next/navigation"
import { useQueryClient } from "@tanstack/react-query"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { SyncStatusBadge, FirewallBadge } from "@/components/status-badge"
import { GroupMultiSelect } from "@/components/group-multi-select"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { useApiMutation } from "@/lib/mutations"
import { TableSkeleton, CardSkeleton } from "@/components/ui/skeleton"
import { apiFetch, API_BASE } from "@/lib/api"
import { useHostQueries, useHostDialogs } from "@/hooks/use-host-detail"
import type { FirewallRule, HostsEntry, LiveService, ServiceCommandResult } from "@/lib/types"

function ActionBadge({ action }: { action: string }) {
  const config: Record<string, string> = {
    allow: "bg-green-600 text-white",
    deny: "bg-red-600 text-white",
    reject: "bg-amber-600 text-white",
  }
  return (
    <Badge className={config[action] ?? ""}>
      {action.charAt(0).toUpperCase() + action.slice(1)}
    </Badge>
  )
}

function formatPorts(rule: FirewallRule): string {
  if (rule.port_start == null) return "—"
  if (rule.port_end != null && rule.port_end !== rule.port_start) {
    return `${rule.port_start}–${rule.port_end}`
  }
  return String(rule.port_start)
}

function cronToHuman(schedule: string): string {
  const s = schedule.trim()
  if (s === "* * * * *") return "Every minute"
  if (s === "0 * * * *") return "Every hour"
  if (s === "0 0 * * *") return "Every day at midnight"
  // 0 N * * *  => Every day at N:00
  const dailyMatch = s.match(/^0\s+(\d+)\s+\*\s+\*\s+\*$/)
  if (dailyMatch) return `Every day at ${dailyMatch[1]}:00`
  // */N * * * *  => Every N minutes
  const everyNMin = s.match(/^\*\/(\d+)\s+\*\s+\*\s+\*\s+\*$/)
  if (everyNMin) return `Every ${everyNMin[1]} minutes`
  return s
}

function InfoRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-4 py-2 border-b border-slate-800 last:border-0">
      <span className="text-slate-400 text-sm w-40 shrink-0">{label}</span>
      <span className="text-white text-sm">{children}</span>
    </div>
  )
}

export default function HostDetailPage() {
  const params = useParams()
  const id = Number(params.id)
  const queryClient = useQueryClient()
  const [activeTab, setActiveTab] = useState<"overview" | "services" | "hosts-file" | "users" | "cron-jobs" | "packages" | "dns">("overview")

  const {
    host: hostQuery, effectiveRules: effectiveRulesQuery, showRulesLoading, sshKeys: sshKeysQuery, groups: groupsQuery,
    effectiveServices: effectiveServicesQuery, showServicesLoading, hostOverrides: hostOverridesQuery,
    effectiveHosts: effectiveHostsQuery, showHostsEntriesLoading, hostHostsOverrides: hostHostsOverridesQuery,
    effectiveLinuxUsers: effectiveLinuxUsersQuery, showLinuxUsersLoading,
    effectiveLinuxGroups: effectiveLinuxGroupsQuery, showLinuxGroupsLoading,
    hostLinuxUserOverrides: hostLinuxUserOverridesQuery, hostLinuxGroupOverrides: hostLinuxGroupOverridesQuery,
    effectiveCronJobs: effectiveCronJobsQuery, showCronJobsLoading, hostCronOverrides: hostCronOverridesQuery,
    effectivePackages: effectivePackagesQuery, showPackagesLoading, hostPackageOverrides: hostPackageOverridesQuery,
    effectiveResolver: effectiveResolverQuery, showResolverLoading, hostResolverOverride: hostResolverOverrideQuery,
  } = useHostQueries(id, activeTab)

  const host = hostQuery.data
  const hostLoading = hostQuery.isLoading
  const hostError = hostQuery.error
  const effectiveRules = effectiveRulesQuery.data
  const rulesLoading = effectiveRulesQuery.isLoading
  const rulesError = effectiveRulesQuery.error
  const sshKeys = sshKeysQuery.data
  const groups = groupsQuery.data
  const effectiveServices = effectiveServicesQuery.data
  const servicesLoading = effectiveServicesQuery.isLoading
  const servicesError = effectiveServicesQuery.error
  const hostOverrides = hostOverridesQuery.data
  const effectiveHosts = effectiveHostsQuery.data
  const hostsEntriesLoading = effectiveHostsQuery.isLoading
  const hostsEntriesError = effectiveHostsQuery.error
  const hostHostsOverrides = hostHostsOverridesQuery.data
  const effectiveLinuxUsers = effectiveLinuxUsersQuery.data
  const linuxUsersLoading = effectiveLinuxUsersQuery.isLoading
  const linuxUsersError = effectiveLinuxUsersQuery.error
  const effectiveLinuxGroups = effectiveLinuxGroupsQuery.data
  const linuxGroupsLoading = effectiveLinuxGroupsQuery.isLoading
  const linuxGroupsError = effectiveLinuxGroupsQuery.error
  const hostLinuxUserOverrides = hostLinuxUserOverridesQuery.data
  const hostLinuxGroupOverrides = hostLinuxGroupOverridesQuery.data
  const effectiveCronJobs = effectiveCronJobsQuery.data
  const cronJobsLoading = effectiveCronJobsQuery.isLoading
  const cronJobsError = effectiveCronJobsQuery.error
  const hostCronOverrides = hostCronOverridesQuery.data
  const effectivePackages = effectivePackagesQuery.data
  const packagesLoading = effectivePackagesQuery.isLoading
  const packagesError = effectivePackagesQuery.error
  const hostPackageOverrides = hostPackageOverridesQuery.data
  const effectiveResolver = effectiveResolverQuery.data
  const resolverLoading = effectiveResolverQuery.isLoading
  const resolverError = effectiveResolverQuery.error
  const resolverIs404 = resolverError && "status" in resolverError && (resolverError as { status: number }).status === 404
  const hostResolverOverride = hostResolverOverrideQuery.data

  const {
    editOpen, setEditOpen,
    svcDialogOpen, setSvcDialogOpen,
    hostsDialogOpen, setHostsDialogOpen,
    luDialogOpen, setLuDialogOpen,
    lgDialogOpen, setLgDialogOpen,
    cjDialogOpen, setCjDialogOpen,
    ppDialogOpen, setPpDialogOpen,
    protectedConfirmOpen, setProtectedConfirmOpen,
  } = useHostDialogs()

  const [editHostname, setEditHostname] = useState("")
  const [editIp, setEditIp] = useState("")
  const [editSshPort, setEditSshPort] = useState(22)
  const [editSshKeyId, setEditSshKeyId] = useState<number | null>(null)
  const [editGroups, setEditGroups] = useState<number[]>([])
  const editMutation = useApiMutation({
    mutationFn: (data: Record<string, unknown>) =>
      apiFetch(`/api/hosts/${id}`, { method: "PUT", body: JSON.stringify(data) }),
    invalidateKeys: [["host", id], ["host-effective-rules", id]],
    onSuccess: () => setEditOpen(false),
  })
  const [confirmState, setConfirmState] = useState<{
    open: boolean
    title: string
    description: string
    action: () => void | Promise<void>
    loading?: boolean
    confirmLabel?: string
    variant?: "default" | "destructive"
  } | null>(null)



  const [hostsPreview, setHostsPreview] = useState<string | null>(null)
  const [hostsPreviewLoading, setHostsPreviewLoading] = useState(false)
  const [hostsPreviewError, setHostsPreviewError] = useState<string | null>(null)

  const [hostsIp, setHostsIp] = useState("")
  const [hostsHostname, setHostsHostname] = useState("")
  const [hostsAliases, setHostsAliases] = useState("")
  const [hostsComment, setHostsComment] = useState("")
  const [hostsPriority, setHostsPriority] = useState(100)
  const hostsSaveMutation = useApiMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      apiFetch(`/api/hosts/${id}/hosts-entries`, { method: "POST", body: JSON.stringify(payload) }),
    invalidateKeys: [["host-effective-hosts-entries", id], ["host-hosts-overrides", id]],
    onSuccess: () => setHostsDialogOpen(false),
  })

  const hostsDeleteMutation = useApiMutation({
    mutationFn: (entryId: number) =>
      apiFetch(`/api/hosts/${id}/hosts-entries/${entryId}`, { method: "DELETE" }),
    invalidateKeys: [["host-effective-hosts-entries", id], ["host-hosts-overrides", id]],
  })

  const [luUsername, setLuUsername] = useState("")
  const [luUid, setLuUid] = useState("")
  const [luShell, setLuShell] = useState("/bin/bash")
  const [luHomeDir, setLuHomeDir] = useState("")
  const [luState, setLuState] = useState<"present" | "absent">("present")
  const [luComment, setLuComment] = useState("")
  const [luSudoRule, setLuSudoRule] = useState("")
  const [luAuthorizedKeys, setLuAuthorizedKeys] = useState("")
  const [luSupplementaryGroups, setLuSupplementaryGroups] = useState("")
  const [luPriority, setLuPriority] = useState(100)
  const luSaveMutation = useApiMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      apiFetch(`/api/hosts/${id}/linux-users`, { method: "POST", body: JSON.stringify(payload) }),
    invalidateKeys: [["host-effective-linux-users", id], ["host-linux-user-overrides", id]],
    onSuccess: () => setLuDialogOpen(false),
  })

  const luDeleteMutation = useApiMutation({
    mutationFn: (overrideId: number) =>
      apiFetch(`/api/hosts/${id}/linux-users/${overrideId}`, { method: "DELETE" }),
    invalidateKeys: [["host-effective-linux-users", id], ["host-linux-user-overrides", id]],
  })

  const [cjName, setCjName] = useState("")
  const [cjUser, setCjUser] = useState("root")
  const [cjSchedule, setCjSchedule] = useState("")
  const [cjCommand, setCjCommand] = useState("")
  const [cjState, setCjState] = useState<"present" | "absent">("present")
  const [cjPriority, setCjPriority] = useState(100)
  const [cjComment, setCjComment] = useState("")
  const [cjEnvVars, setCjEnvVars] = useState<{ key: string; value: string }[]>([])
  const cjSaveMutation = useApiMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      apiFetch(`/api/hosts/${id}/cron-jobs`, { method: "POST", body: JSON.stringify(payload) }),
    invalidateKeys: [["host-effective-cron-jobs", id], ["host-cron-overrides", id]],
    onSuccess: () => setCjDialogOpen(false),
  })

  const cjDeleteMutation = useApiMutation({
    mutationFn: (overrideId: number) =>
      apiFetch(`/api/hosts/${id}/cron-jobs/${overrideId}`, { method: "DELETE" }),
    invalidateKeys: [["host-effective-cron-jobs", id], ["host-cron-overrides", id]],
  })

  const [ppName, setPpName] = useState("")
  const [ppVersion, setPpVersion] = useState("")
  const [ppState, setPpState] = useState<"present" | "absent" | "latest">("present")
  const [ppManager, setPpManager] = useState<"auto" | "apt" | "dnf" | "yum">("auto")
  const [ppPriority, setPpPriority] = useState(0)
  const [ppComment, setPpComment] = useState("")
  const ppSaveMutation = useApiMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      apiFetch(`/api/hosts/${id}/packages`, { method: "POST", body: JSON.stringify(payload) }),
    invalidateKeys: [["host-effective-packages", id], ["host-package-overrides", id]],
    onSuccess: () => setPpDialogOpen(false),
  })

  const ppDeleteMutation = useApiMutation({
    mutationFn: (overrideId: number) =>
      apiFetch(`/api/hosts/${id}/packages/${overrideId}`, { method: "DELETE" }),
    invalidateKeys: [["host-effective-packages", id], ["host-package-overrides", id]],
  })

  function openPpDialog() {
    setPpName("")
    setPpVersion("")
    setPpState("present")
    setPpManager("auto")
    setPpPriority(0)
    setPpComment("")
    ppSaveMutation.reset()
    setPpDialogOpen(true)
  }

  function handlePpSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    ppSaveMutation.mutate({
      package_name: ppName, version: ppVersion || null, state: ppState,
      package_manager: ppManager, priority: ppPriority, comment: ppComment || null,
    })
  }

  function handlePpDelete(packageName: string) {
    setConfirmState({
      open: true,
      title: "Delete Package Override",
      description: `Delete host package override for "${packageName}"? This action cannot be undone.`,
      confirmLabel: "Delete",
      variant: "destructive",
      action: async () => {
        const override = hostPackageOverrides?.find(o => o.package_name === packageName)
        if (!override) { setConfirmState(null); return }
        setConfirmState(prev => prev ? { ...prev, loading: true } : null)
        try { await ppDeleteMutation.mutateAsync(override.id) } finally { setConfirmState(null) }
      },
    })
  }

  function openCjDialog() {
    setCjName("")
    setCjUser("root")
    setCjSchedule("")
    setCjCommand("")
    setCjState("present")
    setCjPriority(100)
    setCjComment("")
    setCjEnvVars([])
    cjSaveMutation.reset()
    setCjDialogOpen(true)
  }

  function handleCjSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const env: Record<string, string> = {}
    for (const v of cjEnvVars) { const k = v.key.trim(); if (k) env[k] = v.value }
    cjSaveMutation.mutate({
      name: cjName, user: cjUser, schedule: cjSchedule, command: cjCommand,
      state: cjState, priority: cjPriority, comment: cjComment || null, environment: env,
    })
  }

  function handleCjDelete(name: string, user: string) {
    setConfirmState({
      open: true,
      title: "Delete Cron Job Override",
      description: `Delete host cron job override for "${name}" (user: ${user})? This action cannot be undone.`,
      confirmLabel: "Delete",
      variant: "destructive",
      action: async () => {
        const override = hostCronOverrides?.find(o => o.name === name && o.user === user)
        if (!override) { setConfirmState(null); return }
        setConfirmState(prev => prev ? { ...prev, loading: true } : null)
        try { await cjDeleteMutation.mutateAsync(override.id) } finally { setConfirmState(null) }
      },
    })
  }

  const [lgGroupname, setLgGroupname] = useState("")
  const [lgGid, setLgGid] = useState("")
  const [lgState, setLgState] = useState<"present" | "absent">("present")
  const [lgPriority, setLgPriority] = useState(100)
  const lgSaveMutation = useApiMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      apiFetch(`/api/hosts/${id}/linux-groups`, { method: "POST", body: JSON.stringify(payload) }),
    invalidateKeys: [["host-effective-linux-groups", id], ["host-linux-group-overrides", id]],
    onSuccess: () => setLgDialogOpen(false),
  })

  const lgDeleteMutation = useApiMutation({
    mutationFn: (overrideId: number) =>
      apiFetch(`/api/hosts/${id}/linux-groups/${overrideId}`, { method: "DELETE" }),
    invalidateKeys: [["host-effective-linux-groups", id], ["host-linux-group-overrides", id]],
  })

  function openLuDialog() {
    setLuUsername("")
    setLuUid("")
    setLuShell("/bin/bash")
    setLuHomeDir("")
    setLuState("present")
    setLuComment("")
    setLuSudoRule("")
    setLuAuthorizedKeys("")
    setLuSupplementaryGroups("")
    setLuPriority(100)
    luSaveMutation.reset()
    setLuDialogOpen(true)
  }

  function handleLuSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    luSaveMutation.mutate({
      username: luUsername, uid: luUid ? Number(luUid) : null, shell: luShell,
      home_dir: luHomeDir || null, state: luState, comment: luComment || null,
      sudo_rule: luSudoRule || null,
      authorized_keys: luAuthorizedKeys.split("\n").map((k) => k.trim()).filter(Boolean),
      supplementary_groups: luSupplementaryGroups.split(",").map((g) => g.trim()).filter(Boolean),
      priority: luPriority,
    })
  }

  function handleLuDelete(username: string) {
    setConfirmState({
      open: true,
      title: "Delete User Override",
      description: `Delete host user override for "${username}"? This action cannot be undone.`,
      confirmLabel: "Delete",
      variant: "destructive",
      action: async () => {
        const override = hostLinuxUserOverrides?.find(o => o.username === username)
        if (!override) { setConfirmState(null); return }
        setConfirmState(prev => prev ? { ...prev, loading: true } : null)
        try { await luDeleteMutation.mutateAsync(override.id) } finally { setConfirmState(null) }
      },
    })
  }

  function openLgDialog() {
    setLgGroupname("")
    setLgGid("")
    setLgState("present")
    setLgPriority(100)
    lgSaveMutation.reset()
    setLgDialogOpen(true)
  }

  function handleLgSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    lgSaveMutation.mutate({
      groupname: lgGroupname, gid: lgGid ? Number(lgGid) : null, state: lgState, priority: lgPriority,
    })
  }

  function handleLgDelete(groupname: string) {
    setConfirmState({
      open: true,
      title: "Delete Group Override",
      description: `Delete host group override for "${groupname}"? This action cannot be undone.`,
      confirmLabel: "Delete",
      variant: "destructive",
      action: async () => {
        const override = hostLinuxGroupOverrides?.find(o => o.groupname === groupname)
        if (!override) { setConfirmState(null); return }
        setConfirmState(prev => prev ? { ...prev, loading: true } : null)
        try { await lgDeleteMutation.mutateAsync(override.id) } finally { setConfirmState(null) }
      },
    })
  }

  function openHostsDialog() {
    setHostsIp("")
    setHostsHostname("")
    setHostsAliases("")
    setHostsComment("")
    setHostsPriority(100)
    hostsSaveMutation.reset()
    setHostsDialogOpen(true)
  }

  function handleHostsSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    hostsSaveMutation.mutate({
      ip_address: hostsIp, hostname: hostsHostname,
      aliases: hostsAliases.split(",").map((a) => a.trim()).filter(Boolean),
      comment: hostsComment || null, priority: hostsPriority,
    })
  }

  function handleHostsEntryDelete(entry: HostsEntry) {
    setConfirmState({
      open: true,
      title: "Delete Hosts Entry",
      description: `Delete hosts entry "${entry.ip_address} ${entry.hostname}"? This action cannot be undone.`,
      confirmLabel: "Delete",
      variant: "destructive",
      action: async () => {
        setConfirmState(prev => prev ? { ...prev, loading: true } : null)
        try { await hostsDeleteMutation.mutateAsync(entry.id) } finally { setConfirmState(null) }
      },
    })
  }

  async function fetchHostsPreview() {
    setHostsPreviewLoading(true)
    setHostsPreviewError(null)
    try {
      const res = await fetch(`${API_BASE}/api/hosts/${id}/hosts-file-preview`, {
        credentials: "include",
      })
      if (!res.ok) throw new Error("Failed to load preview")
      const text = await res.text()
      setHostsPreview(text)
    } catch (err) {
      setHostsPreviewError(err instanceof Error ? err.message : "Failed to load preview")
    } finally {
      setHostsPreviewLoading(false)
    }
  }

  const [svcName, setSvcName] = useState("")
  const [svcState, setSvcState] = useState<"running" | "stopped">("running")
  const [svcEnabled, setSvcEnabled] = useState(true)
  const [svcPriority, setSvcPriority] = useState(100)
  const [svcComment, setSvcComment] = useState("")
  const svcSaveMutation = useApiMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      apiFetch(`/api/hosts/${id}/services`, { method: "POST", body: JSON.stringify(payload) }),
    invalidateKeys: [["host-effective-services", id], ["host-service-overrides", id]],
    onSuccess: () => setSvcDialogOpen(false),
  })

  const svcDeleteMutation = useApiMutation({
    mutationFn: (overrideId: number) =>
      apiFetch(`/api/hosts/${id}/services/${overrideId}`, { method: "DELETE" }),
    invalidateKeys: [["host-effective-services", id], ["host-service-overrides", id]],
  })

  // Live inventory state
  const [inventoryLoaded, setInventoryLoaded] = useState(false)
  const [inventoryLoading, setInventoryLoading] = useState(false)
  const [inventory, setInventory] = useState<LiveService[]>([])
  const [inventoryError, setInventoryError] = useState<string | null>(null)
  const [inventoryFilter, setInventoryFilter] = useState("")
  const [pendingAction, setPendingAction] = useState<{ service: string; action: string } | null>(null)
  const [actionLoading, setActionLoading] = useState(false)
  const [actionResult, setActionResult] = useState<{ success: boolean; message: string } | null>(null)
  const [protectedTarget, setProtectedTarget] = useState<{ service: string; action: string } | null>(null)

  function openSvcDialog() {
    setSvcName("")
    setSvcState("running")
    setSvcEnabled(true)
    setSvcPriority(100)
    setSvcComment("")
    svcSaveMutation.reset()
    setSvcDialogOpen(true)
  }

  function handleSvcSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    svcSaveMutation.mutate({
      service_name: svcName, state: svcState, enabled: svcEnabled,
      priority: svcPriority, comment: svcComment || null,
    })
  }

  function handleSvcDelete(serviceName: string) {
    setConfirmState({
      open: true,
      title: "Delete Service Override",
      description: `Delete host override for "${serviceName}"? This action cannot be undone.`,
      confirmLabel: "Delete",
      variant: "destructive",
      action: async () => {
        const override = hostOverrides?.find(o => o.service_name === serviceName)
        if (!override) { setConfirmState(null); return }
        setConfirmState(prev => prev ? { ...prev, loading: true } : null)
        try { await svcDeleteMutation.mutateAsync(override.id) } finally { setConfirmState(null) }
      },
    })
  }

  async function loadInventory() {
    setInventoryLoading(true)
    setInventoryError(null)
    setActionResult(null)
    try {
      const data = await apiFetch<LiveService[]>(`/api/services/hosts/${id}/inventory`)
      setInventory(data)
      setInventoryLoaded(true)
    } catch (err) {
      setInventoryError(err instanceof Error ? err.message : "Failed to load inventory")
    } finally {
      setInventoryLoading(false)
    }
  }

  async function executeCommand(serviceName: string, action: string) {
    setActionLoading(true)
    setActionResult(null)
    setPendingAction({ service: serviceName, action })
    try {
      const result = await apiFetch<ServiceCommandResult>(`/api/services/hosts/${id}/command`, {
        method: "POST",
        body: JSON.stringify({ service_name: serviceName, action }),
      })
      if (result.success) {
        setActionResult({ success: true, message: `${action} ${serviceName}: success` })
        await loadInventory()
      } else {
        setActionResult({ success: false, message: `${action} ${serviceName} failed: ${result.stderr}` })
      }
    } catch (err) {
      setActionResult({ success: false, message: err instanceof Error ? err.message : "Command failed" })
    } finally {
      setActionLoading(false)
      setPendingAction(null)
    }
  }

  function handleActionClick(service: LiveService, action: string) {
    if (service.is_protected) {
      setProtectedTarget({ service: service.unit, action })
      setProtectedConfirmOpen(true)
    } else {
      const actionLabel = action.charAt(0).toUpperCase() + action.slice(1)
      setConfirmState({
        open: true,
        title: `${actionLabel} Service`,
        description: `${actionLabel} ${service.unit}?`,
        confirmLabel: actionLabel,
        variant: action === "stop" || action === "restart" ? "destructive" : "default",
        action: async () => {
          setConfirmState(null)
          await executeCommand(service.unit, action)
        },
      })
    }
  }

  const filteredInventory = inventory.filter(
    (svc) =>
      svc.unit.toLowerCase().includes(inventoryFilter.toLowerCase()) ||
      svc.description.toLowerCase().includes(inventoryFilter.toLowerCase())
  )

  useEffect(() => {
    if (editOpen && host) {
      setEditHostname(host.hostname)
      setEditIp(host.ip_address)
      setEditSshPort(host.ssh_port)
      setEditSshKeyId(host.ssh_key_id)
      setEditGroups(host.group_ids ?? [])
      editMutation.reset()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editOpen, host])

  function handleEditSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    editMutation.mutate({
      hostname: editHostname, ip_address: editIp, ssh_port: editSshPort,
      ssh_key_id: editSshKeyId, group_ids: editGroups,
    })
  }

  return (
    <div className="space-y-8">
      <Breadcrumb items={[{ label: "Hosts", href: "/hosts" }, { label: host?.hostname ?? "Host" }]} />
      {/* Host Info */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white mb-1">
            {hostLoading ? "Loading…" : host?.hostname ?? `Host #${id}`}
          </h1>
          <p className="text-slate-400 text-sm">Host details and effective firewall rules</p>
        </div>
        {host && (
          <Dialog open={editOpen} onOpenChange={setEditOpen}>
            <DialogTrigger>
              <Button variant="outline" size="sm">Edit</Button>
            </DialogTrigger>
            <DialogContent className="sm:max-w-md">
              <DialogHeader>
                <DialogTitle>Edit Host</DialogTitle>
              </DialogHeader>
              <form onSubmit={handleEditSubmit} className="space-y-4 mt-2">
                <div className="space-y-2">
                  <Label htmlFor="edit-hostname">Hostname</Label>
                  <Input
                    id="edit-hostname"
                    type="text"
                    value={editHostname}
                    onChange={(e) => setEditHostname(e.target.value)}
                    required
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="edit-ip">IP Address</Label>
                  <Input
                    id="edit-ip"
                    type="text"
                    value={editIp}
                    onChange={(e) => setEditIp(e.target.value)}
                    required
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="edit-ssh-port">SSH Port</Label>
                  <Input
                    id="edit-ssh-port"
                    type="number"
                    value={editSshPort}
                    onChange={(e) => setEditSshPort(Number(e.target.value))}
                    required
                    min={1}
                    max={65535}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="edit-ssh-key">SSH Key</Label>
                  <select
                    id="edit-ssh-key"
                    value={editSshKeyId ?? ""}
                    onChange={(e) => setEditSshKeyId(e.target.value ? Number(e.target.value) : null)}
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
                    selected={editGroups}
                    onChange={setEditGroups}
                  />
                )}

                {editMutation.error && (
                  <p className="text-sm text-red-400">{editMutation.error.message}</p>
                )}

                <div className="flex gap-3 pt-2">
                  <Button type="submit" disabled={editMutation.isPending}>
                    {editMutation.isPending ? "Saving..." : "Save Changes"}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => setEditOpen(false)}
                  >
                    Cancel
                  </Button>
                </div>
              </form>
            </DialogContent>
          </Dialog>
        )}
      </div>

      <div className="flex gap-1 border-b border-slate-700">
        <button
          onClick={() => setActiveTab("overview")}
          className={`px-4 py-2 text-sm font-medium transition-colors ${
            activeTab === "overview"
              ? "text-white border-b-2 border-white"
              : "text-slate-400 hover:text-white"
          }`}
        >
          Overview
        </button>
        <button
          onClick={() => setActiveTab("services")}
          className={`px-4 py-2 text-sm font-medium transition-colors ${
            activeTab === "services"
              ? "text-white border-b-2 border-white"
              : "text-slate-400 hover:text-white"
          }`}
        >
          Services
        </button>
        <button
          onClick={() => setActiveTab("hosts-file")}
          className={`px-4 py-2 text-sm font-medium transition-colors ${
            activeTab === "hosts-file"
              ? "text-white border-b-2 border-white"
              : "text-slate-400 hover:text-white"
          }`}
        >
          Hosts File
        </button>
        <button
          onClick={() => setActiveTab("users")}
          className={`px-4 py-2 text-sm font-medium transition-colors ${
            activeTab === "users"
              ? "text-white border-b-2 border-white"
              : "text-slate-400 hover:text-white"
          }`}
        >
          Users
        </button>
        <button
          onClick={() => setActiveTab("cron-jobs")}
          className={`px-4 py-2 text-sm font-medium transition-colors ${
            activeTab === "cron-jobs"
              ? "text-white border-b-2 border-white"
              : "text-slate-400 hover:text-white"
          }`}
        >
          Cron Jobs
        </button>
        <button
          onClick={() => setActiveTab("packages")}
          className={`px-4 py-2 text-sm font-medium transition-colors ${
            activeTab === "packages"
              ? "text-white border-b-2 border-white"
              : "text-slate-400 hover:text-white"
          }`}
        >
          Packages
        </button>
        <button
          onClick={() => setActiveTab("dns")}
          className={`px-4 py-2 text-sm font-medium transition-colors ${
            activeTab === "dns"
              ? "text-white border-b-2 border-white"
              : "text-slate-400 hover:text-white"
          }`}
        >
          DNS
        </button>
      </div>

      {activeTab === "overview" && (
        <>
          {hostError && (
            <div className="text-red-400">Failed to load host details</div>
          )}

          {host && (
            <div className="rounded-lg border border-slate-700 bg-slate-900 p-4 space-y-0">
              <InfoRow label="Hostname">{host.hostname}</InfoRow>
              <InfoRow label="IP Address">
                <span className="font-mono">{host.ip_address}</span>
              </InfoRow>
              <InfoRow label="SSH Port">
                <span className="font-mono">{host.ssh_port}</span>
              </InfoRow>
              <InfoRow label="Firewall Backend">
                <FirewallBadge backend={host.firewall_backend} />
              </InfoRow>
              <InfoRow label="Sync Status">
                <SyncStatusBadge status={host.sync_status} />
              </InfoRow>
              <InfoRow label="Last Sync">
                {host.last_sync_at
                  ? new Date(host.last_sync_at).toLocaleString()
                  : "Never"}
              </InfoRow>
              <InfoRow label="Last Drift Check">
                {host.last_drift_check_at
                  ? new Date(host.last_drift_check_at).toLocaleString()
                  : "Never"}
              </InfoRow>
              <InfoRow label="Drift Check">
                {host.drift_check_enabled ? (
                  <Badge className="bg-green-700 text-white">Enabled</Badge>
                ) : (
                  <Badge variant="outline">Disabled</Badge>
                )}
              </InfoRow>
            </div>
          )}

          <div>
            <h2 className="text-lg font-semibold text-white mb-3">Effective Rules</h2>
            <p className="text-slate-400 text-sm mb-4">
              Combined rules applied to this host from all assigned groups, in priority order.
            </p>

            {showRulesLoading && <TableSkeleton rows={3} columns={4} />}

            {rulesError && (
              <div className="text-red-400 py-6 text-center">Failed to load effective rules</div>
            )}

            {!rulesLoading && !rulesError && effectiveRules && effectiveRules.length === 0 && (
              <div className="text-slate-400 py-6 text-center">
                No effective rules. Assign this host to a group with rules.
              </div>
            )}

            {!rulesLoading && !rulesError && effectiveRules && effectiveRules.length > 0 && (
              <div className="rounded-lg border border-slate-700 bg-slate-900">
                <Table>
                  <TableHeader>
                    <TableRow className="border-slate-700">
                      <TableHead className="w-16">Priority</TableHead>
                      <TableHead>Action</TableHead>
                      <TableHead>Protocol</TableHead>
                      <TableHead>Direction</TableHead>
                      <TableHead>Source</TableHead>
                      <TableHead>Dest</TableHead>
                      <TableHead>Port(s)</TableHead>
                      <TableHead>Group</TableHead>
                      <TableHead>Comment</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {effectiveRules.map((rule) => (
                      <TableRow key={rule.id} className="border-slate-700">
                        <TableCell className="font-mono text-slate-300 text-xs">{rule.priority}</TableCell>
                        <TableCell>
                          <ActionBadge action={rule.action} />
                        </TableCell>
                        <TableCell className="text-slate-300 uppercase text-xs">{rule.protocol}</TableCell>
                        <TableCell className="text-slate-300 capitalize text-xs">{rule.direction}</TableCell>
                        <TableCell className="font-mono text-slate-300 text-xs">{rule.source_cidr ?? "any"}</TableCell>
                        <TableCell className="font-mono text-slate-300 text-xs">{rule.destination_cidr ?? "any"}</TableCell>
                        <TableCell className="font-mono text-slate-300 text-xs">{formatPorts(rule)}</TableCell>
                        <TableCell>
                          <Badge variant="outline" className="text-xs font-mono">
                            #{rule.group_id}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-slate-400 text-xs max-w-[140px] truncate">{rule.comment ?? "—"}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </div>
        </>
      )}

      {activeTab === "services" && (
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold text-white">Effective Services</h2>
              <p className="text-slate-400 text-sm mt-1">
                Services applied to this host from groups and host-level overrides.
              </p>
            </div>
            <Button onClick={openSvcDialog}>Add Override</Button>
          </div>

          {svcDeleteMutation.error && (
            <div className="text-red-400 text-sm">{svcDeleteMutation.error.message}</div>
          )}

          {showServicesLoading && <TableSkeleton rows={3} columns={4} />}

          {servicesError && (
            <div className="text-red-400 py-6 text-center">Failed to load services</div>
          )}

          {!servicesLoading && !servicesError && effectiveServices && effectiveServices.length === 0 && (
            <div className="text-slate-400 py-6 text-center">
              No services configured. Add a host override or assign service rules to a group.
            </div>
          )}

          {!servicesLoading && !servicesError && effectiveServices && effectiveServices.length > 0 && (
            <div className="rounded-lg border border-slate-700 bg-slate-900">
              <Table>
                <TableHeader>
                  <TableRow className="border-slate-700">
                    <TableHead>Service Name</TableHead>
                    <TableHead>State</TableHead>
                    <TableHead>Enabled</TableHead>
                    <TableHead>Source</TableHead>
                    <TableHead className="w-32">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {effectiveServices.map((svc) => (
                    <TableRow key={`${svc.source}-${svc.source_id}-${svc.service_name}`} className="border-slate-700">
                      <TableCell className="font-mono text-white text-sm">{svc.service_name}</TableCell>
                      <TableCell>
                        <Badge className={svc.state === "running" ? "bg-green-600 text-white" : "bg-slate-600 text-white"}>
                          {svc.state.charAt(0).toUpperCase() + svc.state.slice(1)}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        {svc.enabled ? (
                          <Badge className="bg-green-700 text-white">Enabled</Badge>
                        ) : (
                          <Badge variant="outline">Disabled</Badge>
                        )}
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className="text-xs">
                          {svc.source === "group" ? `Group: ${svc.source_name}` : "Host override"}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        {svc.source === "host" ? (
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={svcDeleteMutation.isPending}
                            onClick={() => handleSvcDelete(svc.service_name)}
                            className="text-red-400 hover:text-red-300 hover:bg-red-950"
                          >
                            {svcDeleteMutation.isPending ? "…" : "Delete"}
                          </Button>
                        ) : (
                          <span className="text-slate-600 text-xs">Read-only</span>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}

          <Dialog open={svcDialogOpen} onOpenChange={setSvcDialogOpen}>
            <DialogContent className="sm:max-w-md">
              <DialogHeader>
                <DialogTitle>Add Service Override</DialogTitle>
              </DialogHeader>
              <form onSubmit={handleSvcSubmit} className="space-y-4 mt-2">
                <div className="space-y-2">
                  <Label htmlFor="svc-name">Service Name</Label>
                  <Input
                    id="svc-name"
                    type="text"
                    placeholder="e.g. nginx, sshd, docker"
                    value={svcName}
                    onChange={(e) => setSvcName(e.target.value)}
                    required
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="svc-state">State</Label>
                  <select
                    id="svc-state"
                    value={svcState}
                    onChange={(e) => setSvcState(e.target.value as "running" | "stopped")}
                    className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30"
                  >
                    <option value="running">Running</option>
                    <option value="stopped">Stopped</option>
                  </select>
                </div>

                <div className="flex items-center gap-2">
                  <input
                    id="svc-enabled"
                    type="checkbox"
                    checked={svcEnabled}
                    onChange={(e) => setSvcEnabled(e.target.checked)}
                    className="rounded border-input"
                  />
                  <Label htmlFor="svc-enabled">Enabled</Label>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="svc-priority">Priority</Label>
                  <Input
                    id="svc-priority"
                    type="number"
                    value={svcPriority}
                    onChange={(e) => setSvcPriority(Number(e.target.value))}
                    required
                    min={0}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="svc-comment">Comment</Label>
                  <Input
                    id="svc-comment"
                    type="text"
                    placeholder="Optional comment"
                    value={svcComment}
                    onChange={(e) => setSvcComment(e.target.value)}
                  />
                </div>

                {svcSaveMutation.error && (
                  <p className="text-sm text-red-400">{svcSaveMutation.error.message}</p>
                )}

                <div className="flex gap-3 pt-2">
                  <Button type="submit" disabled={svcSaveMutation.isPending}>
                    {svcSaveMutation.isPending ? "Saving..." : "Create Override"}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => setSvcDialogOpen(false)}
                  >
                    Cancel
                  </Button>
                </div>
              </form>
            </DialogContent>
          </Dialog>

          {/* Live Service Inventory */}
          <hr className="border-slate-700" />

          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold text-white">Service Inventory</h2>
              <p className="text-slate-400 text-sm mt-1">
                Live systemd services on this host. Fetched via SSH on demand.
              </p>
            </div>
            <Button
              onClick={loadInventory}
              disabled={inventoryLoading}
            >
              {inventoryLoading ? "Loading..." : inventoryLoaded ? "Refresh" : "Load Inventory"}
            </Button>
          </div>

          {actionResult && (
            <div
              className={`flex items-center justify-between rounded-lg border p-3 text-sm ${
                actionResult.success
                  ? "border-green-700 bg-green-950 text-green-300"
                  : "border-red-700 bg-red-950 text-red-300"
              }`}
            >
              <span>{actionResult.message}</span>
              <button
                onClick={() => setActionResult(null)}
                className="ml-4 text-slate-400 hover:text-white"
              >
                ✕
              </button>
            </div>
          )}

          {inventoryError && (
            <div className="text-red-400 text-sm">{inventoryError}</div>
          )}

          {inventoryLoaded && inventory.length === 0 && !inventoryLoading && (
            <div className="text-slate-400 py-6 text-center">No services found.</div>
          )}

          {inventoryLoaded && inventory.length > 0 && (
            <>
              <Input
                placeholder="Filter services..."
                value={inventoryFilter}
                onChange={(e) => setInventoryFilter(e.target.value)}
                className="max-w-sm"
              />

              <div className="rounded-lg border border-slate-700 bg-slate-900">
                <Table>
                  <TableHeader>
                    <TableRow className="border-slate-700">
                      <TableHead>Unit</TableHead>
                      <TableHead>Active State</TableHead>
                      <TableHead>Sub State</TableHead>
                      <TableHead>Load State</TableHead>
                      <TableHead>Description</TableHead>
                      <TableHead className="w-48">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {filteredInventory.map((svc) => (
                      <TableRow key={svc.unit} className="border-slate-700">
                        <TableCell className="font-mono text-white text-sm">
                          {svc.unit}
                          {svc.is_managed && (
                            <Badge variant="outline" className="text-xs ml-2">Managed</Badge>
                          )}
                        </TableCell>
                        <TableCell>
                          <Badge
                            className={
                              svc.active_state === "active"
                                ? "bg-green-600 text-white"
                                : svc.active_state === "failed"
                                  ? "bg-red-600 text-white"
                                  : svc.active_state === "activating" || svc.active_state === "deactivating"
                                    ? "bg-yellow-600 text-white"
                                    : "bg-slate-600 text-white"
                            }
                          >
                            {svc.active_state}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-slate-300 text-xs">{svc.sub_state}</TableCell>
                        <TableCell className="text-slate-300 text-xs">{svc.load_state}</TableCell>
                        <TableCell className="text-slate-400 text-xs max-w-[200px] truncate">{svc.description}</TableCell>
                        <TableCell>
                          <div className="flex gap-1">
                            {(["start", "stop", "restart"] as const).map((action) => (
                              <Button
                                key={action}
                                size="sm"
                                variant="ghost"
                                disabled={actionLoading && pendingAction?.service === svc.unit}
                                onClick={() => handleActionClick(svc, action)}
                                className="text-xs"
                              >
                                {actionLoading && pendingAction?.service === svc.unit && pendingAction?.action === action
                                  ? "..."
                                  : action.charAt(0).toUpperCase() + action.slice(1)}
                              </Button>
                            ))}
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>

              <p className="text-slate-500 text-xs">
                Showing {filteredInventory.length} of {inventory.length} services
              </p>
            </>
          )}

          {/* Protected Service Confirmation Dialog */}
          <Dialog open={protectedConfirmOpen} onOpenChange={setProtectedConfirmOpen}>
            <DialogContent className="sm:max-w-md">
              <DialogHeader>
                <DialogTitle>Protected Service Warning</DialogTitle>
              </DialogHeader>
              <div className="space-y-4 mt-2">
                <p className="text-amber-400 text-sm">
                  <strong>{protectedTarget?.service}</strong> is a protected system service.
                  Performing <strong>{protectedTarget?.action}</strong> on this service could
                  cause system instability or loss of access.
                </p>
                <p className="text-slate-400 text-sm">Are you sure you want to proceed?</p>
                <div className="flex justify-end gap-2">
                  <Button variant="outline" onClick={() => setProtectedConfirmOpen(false)}>
                    Cancel
                  </Button>
                  <Button
                    variant="destructive"
                    onClick={() => {
                      setProtectedConfirmOpen(false)
                      if (protectedTarget) executeCommand(protectedTarget.service, protectedTarget.action)
                    }}
                  >
                    Confirm {protectedTarget?.action}
                  </Button>
                </div>
              </div>
            </DialogContent>
          </Dialog>
        </div>
      )}

      {activeTab === "hosts-file" && (
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold text-white">Effective Hosts File</h2>
              <p className="text-slate-400 text-sm mt-1">
                /etc/hosts entries applied to this host from groups, overrides, and system defaults.
              </p>
            </div>
            <div className="flex gap-2">
              <Button
                variant="outline"
                onClick={fetchHostsPreview}
                disabled={hostsPreviewLoading}
              >
                {hostsPreviewLoading ? "Loading..." : "Preview File"}
              </Button>
              <Button onClick={openHostsDialog}>Add Override</Button>
            </div>
          </div>

          {hostsDeleteMutation.error && (
            <div className="text-red-400 text-sm">{hostsDeleteMutation.error.message}</div>
          )}

          {hostsPreviewError && (
            <div className="text-red-400 text-sm">{hostsPreviewError}</div>
          )}

          {hostsPreview !== null && (
            <div className="rounded-lg border border-slate-700 bg-slate-950 p-4">
              <div className="flex items-center justify-between mb-2">
                <h3 className="text-sm font-medium text-slate-300">/etc/hosts preview</h3>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setHostsPreview(null)}
                  className="text-slate-400 hover:text-white"
                >
                  Close
                </Button>
              </div>
              <pre className="text-xs text-slate-300 font-mono whitespace-pre overflow-x-auto">{hostsPreview}</pre>
            </div>
          )}

          {showHostsEntriesLoading && <TableSkeleton rows={3} columns={4} />}

          {hostsEntriesError && (
            <div className="text-red-400 py-6 text-center">Failed to load hosts entries</div>
          )}

          {!hostsEntriesLoading && !hostsEntriesError && effectiveHosts && effectiveHosts.length === 0 && (
            <div className="text-slate-400 py-6 text-center">
              No hosts entries configured. Add a host override or assign entries to a group.
            </div>
          )}

          {!hostsEntriesLoading && !hostsEntriesError && effectiveHosts && effectiveHosts.length > 0 && (
            <div className="rounded-lg border border-slate-700 bg-slate-900">
              <Table>
                <TableHeader>
                  <TableRow className="border-slate-700">
                    <TableHead>IP Address</TableHead>
                    <TableHead>Hostname</TableHead>
                    <TableHead>Aliases</TableHead>
                    <TableHead>Source</TableHead>
                    <TableHead className="w-32">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {effectiveHosts.map((entry) => (
                    <TableRow key={`${entry.source}-${entry.source_id}-${entry.hostname}`} className="border-slate-700">
                      <TableCell className="font-mono text-white text-sm">{entry.ip_address}</TableCell>
                      <TableCell className="font-mono text-slate-300 text-sm">{entry.hostname}</TableCell>
                      <TableCell className="text-slate-300 text-xs max-w-[200px] truncate">
                        {entry.aliases.length > 0 ? entry.aliases.join(", ") : "—"}
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className="text-xs">
                          {entry.source === "system"
                            ? "System"
                            : entry.source === "group"
                              ? `Group: ${entry.source_name}`
                              : "Host override"}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        {entry.source === "host" && !entry.is_system ? (
                          (() => {
                            const override = hostHostsOverrides?.find(
                              (o) => o.hostname === entry.hostname && o.ip_address === entry.ip_address
                            )
                            return override ? (
                              <Button
                                size="sm"
                                variant="ghost"
                                 disabled={hostsDeleteMutation.isPending}
                                onClick={() => handleHostsEntryDelete(override)}
                                className="text-red-400 hover:text-red-300 hover:bg-red-950"
                              >
                                {hostsDeleteMutation.isPending ? "…" : "Delete"}
                              </Button>
                            ) : null
                          })()
                        ) : (
                          <span className="text-slate-600 text-xs">Read-only</span>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}

          <Dialog open={hostsDialogOpen} onOpenChange={setHostsDialogOpen}>
            <DialogContent className="sm:max-w-md">
              <DialogHeader>
                <DialogTitle>Add Hosts Entry Override</DialogTitle>
              </DialogHeader>
              <form onSubmit={handleHostsSubmit} className="space-y-4 mt-2">
                <div className="space-y-2">
                  <Label htmlFor="hosts-ip">IP Address</Label>
                  <Input
                    id="hosts-ip"
                    type="text"
                    placeholder="e.g. 192.168.1.10"
                    value={hostsIp}
                    onChange={(e) => setHostsIp(e.target.value)}
                    required
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="hosts-hostname">Hostname</Label>
                  <Input
                    id="hosts-hostname"
                    type="text"
                    placeholder="e.g. myserver.local"
                    value={hostsHostname}
                    onChange={(e) => setHostsHostname(e.target.value)}
                    required
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="hosts-aliases">Aliases (comma-separated)</Label>
                  <Input
                    id="hosts-aliases"
                    type="text"
                    placeholder="e.g. myserver, ms"
                    value={hostsAliases}
                    onChange={(e) => setHostsAliases(e.target.value)}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="hosts-comment">Comment</Label>
                  <Input
                    id="hosts-comment"
                    type="text"
                    placeholder="Optional comment"
                    value={hostsComment}
                    onChange={(e) => setHostsComment(e.target.value)}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="hosts-priority">Priority</Label>
                  <Input
                    id="hosts-priority"
                    type="number"
                    value={hostsPriority}
                    onChange={(e) => setHostsPriority(Number(e.target.value))}
                    required
                    min={0}
                  />
                </div>

                {hostsSaveMutation.error && (
                  <p className="text-sm text-red-400">{hostsSaveMutation.error.message}</p>
                )}

                <div className="flex gap-3 pt-2">
                  <Button type="submit" disabled={hostsSaveMutation.isPending}>
                    {hostsSaveMutation.isPending ? "Saving..." : "Create Override"}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => setHostsDialogOpen(false)}
                  >
                    Cancel
                  </Button>
                </div>
              </form>
            </DialogContent>
          </Dialog>
        </div>
      )}

      {activeTab === "users" && (
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold text-white">Effective Linux Users</h2>
              <p className="text-slate-400 text-sm mt-1">
                Users applied to this host from groups and host-level overrides.
              </p>
            </div>
            <Button onClick={openLuDialog}>Add User Override</Button>
          </div>

          {luDeleteMutation.error && (
            <div className="text-red-400 text-sm">{luDeleteMutation.error.message}</div>
          )}

          {showLinuxUsersLoading && <TableSkeleton rows={3} columns={4} />}

          {linuxUsersError && (
            <div className="text-red-400 py-6 text-center">Failed to load users</div>
          )}

          {!linuxUsersLoading && !linuxUsersError && effectiveLinuxUsers && effectiveLinuxUsers.length === 0 && (
            <div className="text-slate-400 py-6 text-center">
              No Linux users configured. Add a host override or assign users to a group.
            </div>
          )}

          {!linuxUsersLoading && !linuxUsersError && effectiveLinuxUsers && effectiveLinuxUsers.length > 0 && (
            <div className="rounded-lg border border-slate-700 bg-slate-900">
              <Table>
                <TableHeader>
                  <TableRow className="border-slate-700">
                    <TableHead>Username</TableHead>
                    <TableHead>UID</TableHead>
                    <TableHead>Shell</TableHead>
                    <TableHead>State</TableHead>
                    <TableHead>Keys</TableHead>
                    <TableHead>Sudo</TableHead>
                    <TableHead>Source</TableHead>
                    <TableHead className="w-32">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {effectiveLinuxUsers.map((user) => (
                    <TableRow key={`${user.source}-${user.source_id}-${user.username}`} className="border-slate-700">
                      <TableCell className="font-mono text-white text-sm">{user.username}</TableCell>
                      <TableCell className="font-mono text-slate-300 text-xs">{user.uid ?? "auto"}</TableCell>
                      <TableCell className="font-mono text-slate-300 text-xs">{user.shell}</TableCell>
                      <TableCell>
                        <Badge className={user.state === "present" ? "bg-green-600 text-white" : "bg-red-600 text-white"}>
                          {user.state.charAt(0).toUpperCase() + user.state.slice(1)}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className="text-xs">
                          {user.authorized_keys.length} {user.authorized_keys.length === 1 ? "key" : "keys"}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        {user.sudo_rule ? (
                          <Badge className="bg-amber-600 text-white">Yes</Badge>
                        ) : (
                          <span className="text-slate-600 text-xs">No</span>
                        )}
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className="text-xs">
                          {user.source === "group" ? `Group: ${user.source_name}` : "Host override"}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        {user.source === "host" ? (
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={luDeleteMutation.isPending}
                            onClick={() => handleLuDelete(user.username)}
                            className="text-red-400 hover:text-red-300 hover:bg-red-950"
                          >
                            {luDeleteMutation.isPending ? "…" : "Delete"}
                          </Button>
                        ) : (
                          <span className="text-slate-600 text-xs">Read-only</span>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}

          <hr className="border-slate-700" />

          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold text-white">Effective Linux Groups</h2>
              <p className="text-slate-400 text-sm mt-1">
                System groups applied to this host.
              </p>
            </div>
            <Button onClick={openLgDialog}>Add Group Override</Button>
          </div>

          {lgDeleteMutation.error && (
            <div className="text-red-400 text-sm">{lgDeleteMutation.error.message}</div>
          )}

          {showLinuxGroupsLoading && <TableSkeleton rows={3} columns={4} />}

          {linuxGroupsError && (
            <div className="text-red-400 py-6 text-center">Failed to load groups</div>
          )}

          {!linuxGroupsLoading && !linuxGroupsError && effectiveLinuxGroups && effectiveLinuxGroups.length === 0 && (
            <div className="text-slate-400 py-6 text-center">
              No Linux groups configured. Add a host override or assign groups to a group.
            </div>
          )}

          {!linuxGroupsLoading && !linuxGroupsError && effectiveLinuxGroups && effectiveLinuxGroups.length > 0 && (
            <div className="rounded-lg border border-slate-700 bg-slate-900">
              <Table>
                <TableHeader>
                  <TableRow className="border-slate-700">
                    <TableHead>Group Name</TableHead>
                    <TableHead>GID</TableHead>
                    <TableHead>State</TableHead>
                    <TableHead>Source</TableHead>
                    <TableHead className="w-32">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {effectiveLinuxGroups.map((group) => (
                    <TableRow key={`${group.source}-${group.source_id}-${group.groupname}`} className="border-slate-700">
                      <TableCell className="font-mono text-white text-sm">{group.groupname}</TableCell>
                      <TableCell className="font-mono text-slate-300 text-xs">{group.gid ?? "auto"}</TableCell>
                      <TableCell>
                        <Badge className={group.state === "present" ? "bg-green-600 text-white" : "bg-red-600 text-white"}>
                          {group.state.charAt(0).toUpperCase() + group.state.slice(1)}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className="text-xs">
                          {group.source === "group" ? `Group: ${group.source_name}` : "Host override"}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        {group.source === "host" ? (
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={lgDeleteMutation.isPending}
                            onClick={() => handleLgDelete(group.groupname)}
                            className="text-red-400 hover:text-red-300 hover:bg-red-950"
                          >
                            {lgDeleteMutation.isPending ? "…" : "Delete"}
                          </Button>
                        ) : (
                          <span className="text-slate-600 text-xs">Read-only</span>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}

          <Dialog open={luDialogOpen} onOpenChange={setLuDialogOpen}>
            <DialogContent className="sm:max-w-lg max-h-[90vh] overflow-y-auto">
              <DialogHeader>
                <DialogTitle>Add Linux User Override</DialogTitle>
              </DialogHeader>
              <form onSubmit={handleLuSubmit} className="space-y-4 mt-2">
                <div className="space-y-2">
                  <Label htmlFor="lu-username">Username</Label>
                  <Input
                    id="lu-username"
                    type="text"
                    placeholder="e.g. deploy, appuser"
                    value={luUsername}
                    onChange={(e) => setLuUsername(e.target.value)}
                    required
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="lu-uid">UID (optional)</Label>
                  <Input
                    id="lu-uid"
                    type="number"
                    placeholder="Auto-assign if empty"
                    value={luUid}
                    onChange={(e) => setLuUid(e.target.value)}
                    min={1000}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="lu-shell">Shell</Label>
                  <Input
                    id="lu-shell"
                    type="text"
                    value={luShell}
                    onChange={(e) => setLuShell(e.target.value)}
                    required
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="lu-home">Home Directory (optional)</Label>
                  <Input
                    id="lu-home"
                    type="text"
                    placeholder="e.g. /home/deploy"
                    value={luHomeDir}
                    onChange={(e) => setLuHomeDir(e.target.value)}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="lu-state">State</Label>
                  <select
                    id="lu-state"
                    value={luState}
                    onChange={(e) => setLuState(e.target.value as "present" | "absent")}
                    className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30"
                  >
                    <option value="present">Present</option>
                    <option value="absent">Absent</option>
                  </select>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="lu-keys">SSH Authorized Keys</Label>
                  <textarea
                    id="lu-keys"
                    placeholder="One SSH public key per line"
                    value={luAuthorizedKeys}
                    onChange={(e) => setLuAuthorizedKeys(e.target.value)}
                    rows={3}
                    className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground font-mono focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30 resize-y"
                  />
                  <p className="text-xs text-slate-500">One SSH public key per line</p>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="lu-groups">Supplementary Groups</Label>
                  <Input
                    id="lu-groups"
                    type="text"
                    placeholder="e.g. docker, wheel, sudo"
                    value={luSupplementaryGroups}
                    onChange={(e) => setLuSupplementaryGroups(e.target.value)}
                  />
                  <p className="text-xs text-slate-500">Comma-separated group names</p>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="lu-sudo">Sudo Rule (optional)</Label>
                  <Input
                    id="lu-sudo"
                    type="text"
                    placeholder="e.g. ALL=(ALL) NOPASSWD: ALL"
                    value={luSudoRule}
                    onChange={(e) => setLuSudoRule(e.target.value)}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="lu-comment">Comment (optional)</Label>
                  <Input
                    id="lu-comment"
                    type="text"
                    placeholder="GECOS / description"
                    value={luComment}
                    onChange={(e) => setLuComment(e.target.value)}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="lu-priority">Priority</Label>
                  <Input
                    id="lu-priority"
                    type="number"
                    value={luPriority}
                    onChange={(e) => setLuPriority(Number(e.target.value))}
                    required
                    min={0}
                  />
                </div>

                {luSaveMutation.error && (
                  <p className="text-sm text-red-400">{luSaveMutation.error.message}</p>
                )}

                <div className="flex gap-3 pt-2">
                  <Button type="submit" disabled={luSaveMutation.isPending}>
                    {luSaveMutation.isPending ? "Saving..." : "Create Override"}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => setLuDialogOpen(false)}
                  >
                    Cancel
                  </Button>
                </div>
              </form>
            </DialogContent>
          </Dialog>

          <Dialog open={lgDialogOpen} onOpenChange={setLgDialogOpen}>
            <DialogContent className="sm:max-w-md">
              <DialogHeader>
                <DialogTitle>Add Linux Group Override</DialogTitle>
              </DialogHeader>
              <form onSubmit={handleLgSubmit} className="space-y-4 mt-2">
                <div className="space-y-2">
                  <Label htmlFor="lg-name">Group Name</Label>
                  <Input
                    id="lg-name"
                    type="text"
                    placeholder="e.g. docker, developers"
                    value={lgGroupname}
                    onChange={(e) => setLgGroupname(e.target.value)}
                    required
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="lg-gid">GID (optional)</Label>
                  <Input
                    id="lg-gid"
                    type="number"
                    placeholder="Auto-assign if empty"
                    value={lgGid}
                    onChange={(e) => setLgGid(e.target.value)}
                    min={1000}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="lg-state">State</Label>
                  <select
                    id="lg-state"
                    value={lgState}
                    onChange={(e) => setLgState(e.target.value as "present" | "absent")}
                    className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30"
                  >
                    <option value="present">Present</option>
                    <option value="absent">Absent</option>
                  </select>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="lg-priority">Priority</Label>
                  <Input
                    id="lg-priority"
                    type="number"
                    value={lgPriority}
                    onChange={(e) => setLgPriority(Number(e.target.value))}
                    required
                    min={0}
                  />
                </div>

                {lgSaveMutation.error && (
                  <p className="text-sm text-red-400">{lgSaveMutation.error.message}</p>
                )}

                <div className="flex gap-3 pt-2">
                  <Button type="submit" disabled={lgSaveMutation.isPending}>
                    {lgSaveMutation.isPending ? "Saving..." : "Create Override"}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => setLgDialogOpen(false)}
                  >
                    Cancel
                  </Button>
                </div>
              </form>
            </DialogContent>
          </Dialog>
        </div>
      )}

      {activeTab === "cron-jobs" && (
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold text-white">Effective Cron Jobs</h2>
              <p className="text-slate-400 text-sm mt-1">
                Cron jobs applied to this host from groups and host-level overrides.
              </p>
            </div>
            <div className="flex gap-2">
              <Button
                variant="outline"
                onClick={() => {
                  queryClient.invalidateQueries({ queryKey: ["host-effective-cron-jobs", id] })
                  queryClient.invalidateQueries({ queryKey: ["host-cron-overrides", id] })
                }}
              >
                Refresh
              </Button>
              <Button onClick={openCjDialog}>Add Override</Button>
            </div>
          </div>

          {cjDeleteMutation.error && (
            <div className="text-red-400 text-sm">{cjDeleteMutation.error.message}</div>
          )}

          {showCronJobsLoading && <TableSkeleton rows={3} columns={4} />}

          {cronJobsError && (
            <div className="text-red-400 py-6 text-center">Failed to load cron jobs</div>
          )}

          {!cronJobsLoading && !cronJobsError && effectiveCronJobs && effectiveCronJobs.length === 0 && (
            <div className="text-slate-400 py-6 text-center">
              No cron jobs configured. Add a host override or assign cron jobs to a group.
            </div>
          )}

          {!cronJobsLoading && !cronJobsError && effectiveCronJobs && effectiveCronJobs.length > 0 && (
            <div className="rounded-lg border border-slate-700 bg-slate-900">
              <Table>
                <TableHeader>
                  <TableRow className="border-slate-700">
                    <TableHead>Name</TableHead>
                    <TableHead>User</TableHead>
                    <TableHead>Schedule</TableHead>
                    <TableHead>Command</TableHead>
                    <TableHead>State</TableHead>
                    <TableHead>Source</TableHead>
                    <TableHead className="w-32">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {effectiveCronJobs.map((job) => (
                    <TableRow key={`${job.source}-${job.source_id}-${job.name}-${job.user}`} className="border-slate-700">
                      <TableCell className="font-mono text-white text-sm">{job.name}</TableCell>
                      <TableCell className="font-mono text-slate-300 text-xs">{job.user}</TableCell>
                      <TableCell>
                        <div>
                          <span className="font-mono text-slate-300 text-xs">{job.schedule}</span>
                          {cronToHuman(job.schedule) !== job.schedule && (
                            <div className="text-slate-500 text-xs mt-0.5">{cronToHuman(job.schedule)}</div>
                          )}
                        </div>
                      </TableCell>
                      <TableCell className="font-mono text-slate-300 text-xs max-w-[200px]">
                        <span title={job.command}>
                          {job.command.length > 60 ? job.command.slice(0, 60) + "..." : job.command}
                        </span>
                      </TableCell>
                      <TableCell>
                        <Badge className={job.state === "present" ? "bg-green-600 text-white" : "bg-red-600 text-white"}>
                          {job.state.charAt(0).toUpperCase() + job.state.slice(1)}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className="text-xs">
                          {job.source === "group" ? `Group: ${job.source_name}` : "Host override"}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        {job.source === "host" ? (
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={cjDeleteMutation.isPending}
                            onClick={() => handleCjDelete(job.name, job.user)}
                            className="text-red-400 hover:text-red-300 hover:bg-red-950"
                          >
                            {cjDeleteMutation.isPending ? "..." : "Delete"}
                          </Button>
                        ) : (
                          <span className="text-slate-600 text-xs">Read-only</span>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}

          <Dialog open={cjDialogOpen} onOpenChange={setCjDialogOpen}>
            <DialogContent className="sm:max-w-lg max-h-[90vh] overflow-y-auto">
              <DialogHeader>
                <DialogTitle>Add Cron Job Override</DialogTitle>
              </DialogHeader>
              <form onSubmit={handleCjSubmit} className="space-y-4 mt-2">
                <div className="space-y-2">
                  <Label htmlFor="cj-name">Name</Label>
                  <Input
                    id="cj-name"
                    type="text"
                    placeholder="e.g. backup-db, cleanup-logs"
                    value={cjName}
                    onChange={(e) => setCjName(e.target.value)}
                    required
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="cj-user">User</Label>
                  <Input
                    id="cj-user"
                    type="text"
                    placeholder="root"
                    value={cjUser}
                    onChange={(e) => setCjUser(e.target.value)}
                    required
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="cj-schedule">Schedule (cron expression)</Label>
                  <Input
                    id="cj-schedule"
                    type="text"
                    placeholder="*/5 * * * *"
                    value={cjSchedule}
                    onChange={(e) => setCjSchedule(e.target.value)}
                    required
                  />
                  {cjSchedule.trim() && (
                    <p className="text-xs text-slate-400">
                      {cronToHuman(cjSchedule)}
                    </p>
                  )}
                </div>

                <div className="space-y-2">
                  <Label htmlFor="cj-command">Command</Label>
                  <textarea
                    id="cj-command"
                    placeholder="e.g. /usr/local/bin/backup.sh --full"
                    value={cjCommand}
                    onChange={(e) => setCjCommand(e.target.value)}
                    required
                    rows={3}
                    className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground font-mono focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30 resize-y"
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="cj-state">State</Label>
                  <select
                    id="cj-state"
                    value={cjState}
                    onChange={(e) => setCjState(e.target.value as "present" | "absent")}
                    className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30"
                  >
                    <option value="present">Present</option>
                    <option value="absent">Absent</option>
                  </select>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="cj-priority">Priority</Label>
                  <Input
                    id="cj-priority"
                    type="number"
                    value={cjPriority}
                    onChange={(e) => setCjPriority(Number(e.target.value))}
                    required
                    min={0}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="cj-comment">Comment (optional)</Label>
                  <textarea
                    id="cj-comment"
                    placeholder="Optional description"
                    value={cjComment}
                    onChange={(e) => setCjComment(e.target.value)}
                    rows={2}
                    className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30 resize-y"
                  />
                </div>

                <div className="space-y-2">
                  <Label>Environment Variables</Label>
                  <div className="space-y-2">
                    {cjEnvVars.map((v, idx) => (
                      <div key={idx} className="flex items-center gap-2">
                        <Input
                          type="text"
                          placeholder="KEY"
                          value={v.key}
                          onChange={(e) => {
                            const updated = cjEnvVars.map((ev, i) => i === idx ? { ...ev, key: e.target.value } : ev)
                            setCjEnvVars(updated)
                          }}
                          className="flex-1 font-mono text-xs"
                        />
                        <span className="text-slate-500 text-xs">=</span>
                        <Input
                          type="text"
                          placeholder="value"
                          value={v.value}
                          onChange={(e) => {
                            const updated = cjEnvVars.map((ev, i) => i === idx ? { ...ev, value: e.target.value } : ev)
                            setCjEnvVars(updated)
                          }}
                          className="flex-1 font-mono text-xs"
                        />
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          onClick={() => setCjEnvVars(cjEnvVars.filter((_, i) => i !== idx))}
                          className="text-red-400 hover:text-red-300 hover:bg-red-950 px-2"
                        >
                          &times;
                        </Button>
                      </div>
                    ))}
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => setCjEnvVars([...cjEnvVars, { key: "", value: "" }])}
                    >
                      + Add variable
                    </Button>
                  </div>
                </div>

                {cjSaveMutation.error && (
                  <p className="text-sm text-red-400">{cjSaveMutation.error.message}</p>
                )}

                <div className="flex gap-3 pt-2">
                  <Button type="submit" disabled={cjSaveMutation.isPending}>
                    {cjSaveMutation.isPending ? "Saving..." : "Create Override"}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => setCjDialogOpen(false)}
                  >
                    Cancel
                  </Button>
                </div>
              </form>
            </DialogContent>
          </Dialog>
        </div>
      )}

      {activeTab === "packages" && (
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold text-white">Effective Packages</h2>
              <p className="text-slate-400 text-sm mt-1">
                Packages applied to this host from groups and host-level overrides.
              </p>
            </div>
            <Button onClick={openPpDialog}>Add Override</Button>
          </div>

          {ppDeleteMutation.error && (
            <div className="text-red-400 text-sm">{ppDeleteMutation.error.message}</div>
          )}

          {showPackagesLoading && <TableSkeleton rows={3} columns={4} />}

          {packagesError && (
            <div className="text-red-400 py-6 text-center">Failed to load packages</div>
          )}

          {!packagesLoading && !packagesError && effectivePackages && effectivePackages.length === 0 && (
            <div className="text-slate-400 py-6 text-center">
              No packages configured. Add a host override or assign packages to a group.
            </div>
          )}

          {!packagesLoading && !packagesError && effectivePackages && effectivePackages.length > 0 && (
            <div className="rounded-lg border border-slate-700 bg-slate-900">
              <Table>
                <TableHeader>
                  <TableRow className="border-slate-700">
                    <TableHead>Package Name</TableHead>
                    <TableHead>Version</TableHead>
                    <TableHead>State</TableHead>
                    <TableHead>Package Manager</TableHead>
                    <TableHead>Source</TableHead>
                    <TableHead className="w-32">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {effectivePackages.map((pkg) => (
                    <TableRow key={`${pkg.source}-${pkg.source_id}-${pkg.package_name}`} className="border-slate-700">
                      <TableCell className="font-mono text-white text-sm">{pkg.package_name}</TableCell>
                      <TableCell className="font-mono text-slate-300 text-xs">{pkg.version ?? "any"}</TableCell>
                      <TableCell>
                        <Badge className={
                          pkg.state === "present" ? "bg-green-600 text-white"
                            : pkg.state === "latest" ? "bg-blue-600 text-white"
                            : "bg-red-600 text-white"
                        }>
                          {pkg.state.charAt(0).toUpperCase() + pkg.state.slice(1)}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className="text-xs font-mono">{pkg.package_manager}</Badge>
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className="text-xs">
                          {pkg.source === "group" ? `Group: ${pkg.source_name}` : "Host override"}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        {pkg.source === "host" ? (
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={ppDeleteMutation.isPending}
                            onClick={() => handlePpDelete(pkg.package_name)}
                            className="text-red-400 hover:text-red-300 hover:bg-red-950"
                          >
                            {ppDeleteMutation.isPending ? "..." : "Delete"}
                          </Button>
                        ) : (
                          <span className="text-slate-600 text-xs">Read-only</span>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}

          <Dialog open={ppDialogOpen} onOpenChange={setPpDialogOpen}>
            <DialogContent className="sm:max-w-lg max-h-[90vh] overflow-y-auto">
              <DialogHeader>
                <DialogTitle>Add Package Override</DialogTitle>
              </DialogHeader>
              <form onSubmit={handlePpSubmit} className="space-y-4 mt-2">
                <div className="space-y-2">
                  <Label htmlFor="pp-name">Package Name</Label>
                  <Input
                    id="pp-name"
                    type="text"
                    placeholder="e.g. nginx, curl, htop"
                    value={ppName}
                    onChange={(e) => setPpName(e.target.value)}
                    required
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="pp-version">Version</Label>
                  <Input
                    id="pp-version"
                    type="text"
                    placeholder="any version"
                    value={ppVersion}
                    onChange={(e) => setPpVersion(e.target.value)}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="pp-state">State</Label>
                  <select
                    id="pp-state"
                    value={ppState}
                    onChange={(e) => setPpState(e.target.value as "present" | "absent" | "latest")}
                    className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30"
                  >
                    <option value="present">Present</option>
                    <option value="absent">Absent</option>
                    <option value="latest">Latest</option>
                  </select>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="pp-manager">Package Manager</Label>
                  <select
                    id="pp-manager"
                    value={ppManager}
                    onChange={(e) => setPpManager(e.target.value as "auto" | "apt" | "dnf" | "yum")}
                    className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30"
                  >
                    <option value="auto">Auto-detect</option>
                    <option value="apt">apt</option>
                    <option value="dnf">dnf</option>
                    <option value="yum">yum</option>
                  </select>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="pp-priority">Priority</Label>
                  <Input
                    id="pp-priority"
                    type="number"
                    value={ppPriority}
                    onChange={(e) => setPpPriority(Number(e.target.value))}
                    required
                    min={0}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="pp-comment">Comment (optional)</Label>
                  <textarea
                    id="pp-comment"
                    placeholder="Optional description"
                    value={ppComment}
                    onChange={(e) => setPpComment(e.target.value)}
                    rows={2}
                    className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30 resize-y"
                  />
                </div>

                {ppSaveMutation.error && (
                  <p className="text-sm text-red-400">{ppSaveMutation.error.message}</p>
                )}

                <div className="flex gap-3 pt-2">
                  <Button type="submit" disabled={ppSaveMutation.isPending}>
                    {ppSaveMutation.isPending ? "Saving..." : "Create Override"}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => setPpDialogOpen(false)}
                  >
                    Cancel
                  </Button>
                </div>
              </form>
            </DialogContent>
          </Dialog>
        </div>
      )}

      {activeTab === "dns" && (
        <div className="space-y-6">
          <div>
            <h2 className="text-lg font-semibold text-white">Effective DNS Resolver</h2>
            <p className="text-slate-400 text-sm mt-1">
              DNS resolver configuration applied to this host.
            </p>
          </div>

          {showResolverLoading && <CardSkeleton />}

          {resolverIs404 && !resolverLoading && (
            <div className="text-slate-400 py-6 text-center">
              DNS is not managed for this host. Configure DNS at the group level to get started.
            </div>
          )}

          {resolverError && !resolverIs404 && (
            <div className="text-red-400 py-6 text-center">Failed to load DNS resolver</div>
          )}

          {!resolverLoading && !resolverError && effectiveResolver && (
            <div className="rounded-lg border border-slate-700 bg-slate-900 p-6 space-y-4">
              <div className="flex items-center gap-4 py-2 border-b border-slate-800">
                <span className="text-slate-400 text-sm w-40 shrink-0">Source</span>
                <Badge variant="outline" className="text-xs">
                  {effectiveResolver.source === "group"
                    ? `Group: ${effectiveResolver.source_name}`
                    : "Host override"}
                </Badge>
              </div>

              <div className="flex items-center gap-4 py-2 border-b border-slate-800">
                <span className="text-slate-400 text-sm w-40 shrink-0">Resolver Type</span>
                <span className="text-white text-sm">
                  {effectiveResolver.resolver_type === "resolv_conf" && "resolv.conf"}
                  {effectiveResolver.resolver_type === "systemd_resolved" && "systemd-resolved"}
                  {effectiveResolver.resolver_type === "networkmanager" && "NetworkManager"}
                </span>
              </div>

              <div className="flex items-start gap-4 py-2 border-b border-slate-800">
                <span className="text-slate-400 text-sm w-40 shrink-0">Nameservers</span>
                <div className="space-y-1">
                  {effectiveResolver.nameservers.length > 0 ? effectiveResolver.nameservers.map((ns, idx) => (
                    <div key={idx} className="font-mono text-sm text-slate-300">{ns}</div>
                  )) : (
                    <span className="text-slate-500 text-sm">None configured</span>
                  )}
                </div>
              </div>

              <div className="flex items-start gap-4 py-2 border-b border-slate-800">
                <span className="text-slate-400 text-sm w-40 shrink-0">Search Domains</span>
                <div className="space-y-1">
                  {effectiveResolver.search_domains.length > 0 ? effectiveResolver.search_domains.map((sd, idx) => (
                    <div key={idx} className="font-mono text-sm text-slate-300">{sd}</div>
                  )) : (
                    <span className="text-slate-500 text-sm">None configured</span>
                  )}
                </div>
              </div>

              {Object.keys(effectiveResolver.options).length > 0 && (
                <div className="flex items-start gap-4 py-2 border-b border-slate-800">
                  <span className="text-slate-400 text-sm w-40 shrink-0">Options</span>
                  <div className="space-y-1">
                    {Object.entries(effectiveResolver.options).map(([key, value]) => (
                      <div key={key} className="font-mono text-sm text-slate-300">
                        {key}: {String(value)}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {effectiveResolver.resolver_type === "systemd_resolved" && (
                <div className="flex items-center gap-4 py-2 border-b border-slate-800 last:border-0">
                  <span className="text-slate-400 text-sm w-40 shrink-0">DNS-over-TLS</span>
                  <Badge className={effectiveResolver.dns_over_tls ? "bg-green-700 text-white" : "bg-slate-600 text-white"}>
                    {effectiveResolver.dns_over_tls ? "Enabled" : "Disabled"}
                  </Badge>
                </div>
              )}
            </div>
          )}

          {!resolverLoading && !resolverError && effectiveResolver && (
            <div className="text-xs text-slate-500">
              {hostResolverOverride
                ? "This host has a resolver override. Delete the override to inherit from the group."
                : "Inherited from group configuration."}
            </div>
          )}
        </div>
      )}

      {confirmState && (
        <ConfirmDialog
          open={confirmState.open}
          onOpenChange={(open) => !open && setConfirmState(null)}
          title={confirmState.title}
          description={confirmState.description}
          confirmLabel={confirmState.confirmLabel ?? "Confirm"}
          variant={confirmState.variant ?? "destructive"}
          loading={confirmState.loading}
          onConfirm={confirmState.action}
        />
      )}
    </div>
  )
}
