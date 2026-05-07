"use client"

import { useQuery } from "@tanstack/react-query"
import { useState } from "react"
import { apiFetch } from "@/lib/api"
import { useDelayedLoading } from "@/lib/utils"
import type {
  Host,
  HostGroup,
  SSHKey,
  ChainPolicies,
  EffectiveFirewallRule,
  FirewallRule,
  EffectiveService,
  ServiceRule,
  EffectiveHostsEntry,
  HostsEntry,
  EffectiveLinuxUser,
  EffectiveLinuxGroup,
  LinuxUser,
  LinuxGroup,
  EffectiveCronJob,
  CronJob,
  EffectivePackage,
  PackageRule,
  PackageRepository,
  EffectiveResolverConfig,
  ResolverConfig,
  ModuleCurrentState,
  CACertRule,
  EffectiveCACert,
  CACertActionRun,
} from "@/lib/types"

type ActiveTab = "overview" | "groups" | "rules" | "services" | "hosts-file" | "users" | "cron-jobs" | "packages" | "ca-certs" | "dns" | "actions" | "schedules"

export function useHostQueries(id: number, activeTab: ActiveTab) {
  const host = useQuery<Host>({
    queryKey: ["host", id],
    queryFn: () => apiFetch<Host>(`/api/hosts/${id}`),
    enabled: !!id,
  })

  const effectiveRules = useQuery<EffectiveFirewallRule[]>({
    queryKey: ["host-effective-rules", id],
    queryFn: () => apiFetch<EffectiveFirewallRule[]>(`/api/hosts/${id}/effective-rules`),
    enabled: !!id,
  })
  const showRulesLoading = useDelayedLoading(effectiveRules.isLoading)

  const effectivePolicies = useQuery<ChainPolicies>({
    queryKey: ["host-effective-policies", id],
    queryFn: () => apiFetch<ChainPolicies>(`/api/hosts/${id}/effective-policies`),
    enabled: !!id && activeTab === "rules",
  })

  const sshKeys = useQuery<SSHKey[]>({
    queryKey: ["ssh-keys"],
    queryFn: () => apiFetch<SSHKey[]>("/api/ssh-keys"),
  })

  const groups = useQuery<HostGroup[]>({
    queryKey: ["groups"],
    queryFn: () => apiFetch<HostGroup[]>("/api/groups"),
  })

  // Services tab
  const effectiveServices = useQuery<EffectiveService[]>({
    queryKey: ["host-effective-services", id],
    queryFn: () => apiFetch<EffectiveService[]>(`/api/hosts/${id}/effective-services`),
    enabled: !!id && activeTab === "services",
  })
  const showServicesLoading = useDelayedLoading(effectiveServices.isLoading)

  const hostOverrides = useQuery<ServiceRule[]>({
    queryKey: ["host-service-overrides", id],
    queryFn: () => apiFetch<ServiceRule[]>(`/api/hosts/${id}/services`),
    enabled: !!id && activeTab === "services",
  })

  const hostFirewallOverrides = useQuery<FirewallRule[]>({
    queryKey: ["host-firewall-overrides", id],
    queryFn: () => apiFetch<FirewallRule[]>(`/api/hosts/${id}/firewall-rules`),
    enabled: !!id && activeTab === "rules",
  })

  // Hosts file tab
  const effectiveHosts = useQuery<EffectiveHostsEntry[]>({
    queryKey: ["host-effective-hosts-entries", id],
    queryFn: () => apiFetch<EffectiveHostsEntry[]>(`/api/hosts/${id}/effective-hosts-entries`),
    enabled: !!id && activeTab === "hosts-file",
  })
  const showHostsEntriesLoading = useDelayedLoading(effectiveHosts.isLoading)

  const hostHostsOverrides = useQuery<HostsEntry[]>({
    queryKey: ["host-hosts-overrides", id],
    queryFn: () => apiFetch<HostsEntry[]>(`/api/hosts/${id}/hosts-entries`),
    enabled: !!id && activeTab === "hosts-file",
  })

  // Users tab
  const effectiveLinuxUsers = useQuery<EffectiveLinuxUser[]>({
    queryKey: ["host-effective-linux-users", id],
    queryFn: () => apiFetch<EffectiveLinuxUser[]>(`/api/hosts/${id}/effective-users`),
    enabled: !!id && activeTab === "users",
  })
  const showLinuxUsersLoading = useDelayedLoading(effectiveLinuxUsers.isLoading)

  const effectiveLinuxGroups = useQuery<EffectiveLinuxGroup[]>({
    queryKey: ["host-effective-linux-groups", id],
    queryFn: () => apiFetch<EffectiveLinuxGroup[]>(`/api/hosts/${id}/effective-groups`),
    enabled: !!id && activeTab === "users",
  })
  const showLinuxGroupsLoading = useDelayedLoading(effectiveLinuxGroups.isLoading)

  const hostLinuxUserOverrides = useQuery<LinuxUser[]>({
    queryKey: ["host-linux-user-overrides", id],
    queryFn: () => apiFetch<LinuxUser[]>(`/api/hosts/${id}/linux-users`),
    enabled: !!id && activeTab === "users",
  })

  const hostLinuxGroupOverrides = useQuery<LinuxGroup[]>({
    queryKey: ["host-linux-group-overrides", id],
    queryFn: () => apiFetch<LinuxGroup[]>(`/api/hosts/${id}/linux-groups`),
    enabled: !!id && activeTab === "users",
  })

  // Cron jobs tab
  const effectiveCronJobs = useQuery<EffectiveCronJob[]>({
    queryKey: ["host-effective-cron-jobs", id],
    queryFn: () => apiFetch<EffectiveCronJob[]>(`/api/hosts/${id}/effective-cron-jobs`),
    enabled: !!id && activeTab === "cron-jobs",
  })
  const showCronJobsLoading = useDelayedLoading(effectiveCronJobs.isLoading)

  const hostCronOverrides = useQuery<CronJob[]>({
    queryKey: ["host-cron-overrides", id],
    queryFn: () => apiFetch<CronJob[]>(`/api/hosts/${id}/cron-jobs`),
    enabled: !!id && activeTab === "cron-jobs",
  })

  // Packages tab
  const effectivePackages = useQuery<EffectivePackage[]>({
    queryKey: ["host-effective-packages", id],
    queryFn: () => apiFetch<EffectivePackage[]>(`/api/hosts/${id}/effective-packages`),
    enabled: !!id && activeTab === "packages",
  })
  const showPackagesLoading = useDelayedLoading(effectivePackages.isLoading)

  const hostPackageOverrides = useQuery<PackageRule[]>({
    queryKey: ["host-package-overrides", id],
    queryFn: () => apiFetch<PackageRule[]>(`/api/hosts/${id}/packages`),
    enabled: !!id && activeTab === "packages",
  })

  const effectiveRepos = useQuery<PackageRepository[]>({
    queryKey: ["host-effective-repos", id],
    queryFn: () => apiFetch<PackageRepository[]>(`/api/hosts/${id}/effective-repos`),
    enabled: !!id && activeTab === "packages",
  })

  // CA certs tab
  const effectiveCACerts = useQuery<EffectiveCACert[]>({
    queryKey: ["host-effective-ca-certs", id],
    queryFn: () => apiFetch<EffectiveCACert[]>(`/api/hosts/${id}/effective-ca-certs`),
    enabled: !!id && activeTab === "ca-certs",
  })
  const showCACertsLoading = useDelayedLoading(effectiveCACerts.isLoading)

  const hostCACertOverrides = useQuery<CACertRule[]>({
    queryKey: ["host-ca-cert-overrides", id],
    queryFn: () => apiFetch<CACertRule[]>(`/api/hosts/${id}/ca-certs`),
    enabled: !!id && activeTab === "ca-certs",
  })

  const hostCACertRuns = useQuery<CACertActionRun[]>({
    queryKey: ["host-ca-cert-runs", id],
    queryFn: () => apiFetch<CACertActionRun[]>(`/api/ca-certs/hosts/${id}/runs`),
    enabled: !!id && activeTab === "ca-certs",
    refetchInterval: 5000,
  })

  const effectiveResolver = useQuery<EffectiveResolverConfig>({
    queryKey: ["host-effective-resolver", id],
    queryFn: () => apiFetch<EffectiveResolverConfig>(`/api/hosts/${id}/effective-resolver`),
    enabled: !!id && activeTab === "dns",
    retry: (count, error) => {
      if (error && "status" in error && (error as { status: number }).status === 404) return false
      return count < 3
    },
  })
  const showResolverLoading = useDelayedLoading(effectiveResolver.isLoading)

  const hostResolverOverride = useQuery<ResolverConfig>({
    queryKey: ["host-resolver-override", id],
    queryFn: () => apiFetch<ResolverConfig>(`/api/hosts/${id}/resolver`),
    enabled: !!id && activeTab === "dns",
    retry: (count, error) => {
      if (error && "status" in error && (error as { status: number }).status === 404) return false
      return count < 3
    },
  })

  const currentState = useQuery<ModuleCurrentState[]>({
    queryKey: ["host-current-state", id],
    queryFn: () => apiFetch<ModuleCurrentState[]>(`/api/hosts/${id}/current-state`),
    enabled: !!id,
  })

  return {
    host,
    effectiveRules,
    effectivePolicies,
    showRulesLoading,
    sshKeys,
    groups,
    effectiveServices,
    showServicesLoading,
    hostOverrides,
    hostFirewallOverrides,
    effectiveHosts,
    showHostsEntriesLoading,
    hostHostsOverrides,
    effectiveLinuxUsers,
    showLinuxUsersLoading,
    effectiveLinuxGroups,
    showLinuxGroupsLoading,
    hostLinuxUserOverrides,
    hostLinuxGroupOverrides,
    effectiveCronJobs,
    showCronJobsLoading,
    hostCronOverrides,
    effectivePackages,
    showPackagesLoading,
    hostPackageOverrides,
    effectiveRepos,
    effectiveCACerts,
    showCACertsLoading,
    hostCACertOverrides,
    hostCACertRuns,
    effectiveResolver,
    showResolverLoading,
    hostResolverOverride,
    currentState,
  }
}

export function useHostDialogs() {
  const [editOpen, setEditOpen] = useState(false)
  const [fwDialogOpen, setFwDialogOpen] = useState(false)
  const [svcDialogOpen, setSvcDialogOpen] = useState(false)
  const [hostsDialogOpen, setHostsDialogOpen] = useState(false)
  const [luDialogOpen, setLuDialogOpen] = useState(false)
  const [lgDialogOpen, setLgDialogOpen] = useState(false)
  const [cjDialogOpen, setCjDialogOpen] = useState(false)
  const [ppDialogOpen, setPpDialogOpen] = useState(false)
  const [caDialogOpen, setCaDialogOpen] = useState(false)
  const [protectedConfirmOpen, setProtectedConfirmOpen] = useState(false)

  return {
    editOpen, setEditOpen,
    fwDialogOpen, setFwDialogOpen,
    svcDialogOpen, setSvcDialogOpen,
    hostsDialogOpen, setHostsDialogOpen,
    luDialogOpen, setLuDialogOpen,
    lgDialogOpen, setLgDialogOpen,
    cjDialogOpen, setCjDialogOpen,
    ppDialogOpen, setPpDialogOpen,
    caDialogOpen, setCaDialogOpen,
    protectedConfirmOpen, setProtectedConfirmOpen,
  }
}
