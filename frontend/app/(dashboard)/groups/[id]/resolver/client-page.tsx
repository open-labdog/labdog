"use client"

import { useState, type FormEvent } from "react"
import { useParams } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { GitBranch } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { useDelayedLoading } from "@/lib/utils"
import { CardSkeleton } from "@/components/ui/skeleton"
import type { ResolverConfig, HostGroup } from "@/lib/types"

const RESOLVER_TYPE_LABELS: Record<string, string> = {
  resolv_conf: "resolv.conf",
  systemd_resolved: "systemd-resolved",
  networkmanager: "NetworkManager",
}

const OPTION_KEYS = ["ndots", "timeout", "attempts", "rotate", "edns0"] as const

export default function GroupResolverPage({ embedded = false }: { embedded?: boolean } = {}) {
  const params = useParams()
  const id = Number(params.id)

  const { data: group } = useQuery<HostGroup>({
    queryKey: ["group", id],
    queryFn: () => apiFetch<HostGroup>(`/api/groups/${id}`),
    enabled: !!id,
  })

  const gitopsEnabled = !!group?.gitops_enabled

  const resolverQuery = useQuery<ResolverConfig | null>({
    queryKey: ["group-resolver", id],
    queryFn: () => apiFetch<ResolverConfig | null>(`/api/groups/${id}/resolver`),
    enabled: !!id,
  })

  const showLoading = useDelayedLoading(resolverQuery.isLoading)
  // The endpoint returns 200+null when no resolver config exists yet,
  // which is the common case rather than an error. Distinguish "not
  // configured" (data === null) from "request failed" (error truthy).
  const is404 = !resolverQuery.isLoading && !resolverQuery.error && resolverQuery.data === null
  const hasConfig = !!resolverQuery.data && !resolverQuery.error

  const [resolverType, setResolverType] = useState<"resolv_conf" | "systemd_resolved" | "networkmanager">("resolv_conf")
  const [nameservers, setNameservers] = useState<string[]>([])
  const [nsInput, setNsInput] = useState("")
  const [searchDomains, setSearchDomains] = useState<string[]>([])
  const [sdInput, setSdInput] = useState("")
  const [options, setOptions] = useState<{ key: string; value: string }[]>([])
  const [dnsOverTls, setDnsOverTls] = useState(false)
  const [formReady, setFormReady] = useState(false)

  const [confirmState, setConfirmState] = useState<{
    open: boolean; title: string; description: string; action: () => void | Promise<void>; loading?: boolean
  } | null>(null)

  const [preview, setPreview] = useState<string | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)

  function populateForm(config: ResolverConfig) {
    setResolverType(config.resolver_type)
    setNameservers([...config.nameservers])
    setSearchDomains([...config.search_domains])
    setOptions(
      Object.entries(config.options).map(([key, value]) => ({ key, value: String(value) }))
    )
    setDnsOverTls(config.dns_over_tls)
    setFormReady(true)
  }

  function initNewForm() {
    setResolverType("resolv_conf")
    setNameservers([])
    setSearchDomains([])
    setOptions([])
    setDnsOverTls(false)
    setNsInput("")
    setSdInput("")
    setFormReady(true)
  }

  if (hasConfig && !formReady && resolverQuery.data) {
    populateForm(resolverQuery.data)
  }

  const saveMutation = useApiMutation<unknown, Record<string, unknown>, ResolverConfig>({
    mutationFn: (payload) =>
      apiFetch(`/api/groups/${id}/resolver`, {
        method: "PUT",
        body: JSON.stringify(payload),
      }),
    invalidateKeys: [["group-resolver", id]],
    successMessage: "DNS resolver configuration saved",
  })

  const deleteMutation = useApiMutation({
    mutationFn: () =>
      apiFetch(`/api/groups/${id}/resolver`, { method: "DELETE" }),
    invalidateKeys: [["group-resolver", id]],
    successMessage: "DNS resolver configuration deleted",
    onSuccess: () => setFormReady(false),
  })

  const controlsDisabled = gitopsEnabled || saveMutation.isPending

  function handleSave(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const optionsObj: Record<string, number | string> = {}
    for (const o of options) {
      const k = o.key.trim()
      if (!k) continue
      const numVal = Number(o.value)
      optionsObj[k] = !isNaN(numVal) && o.value.trim() !== "" ? numVal : o.value
    }
    saveMutation.mutate({
      nameservers,
      search_domains: searchDomains,
      options: optionsObj,
      resolver_type: resolverType,
      dns_over_tls: dnsOverTls,
    })
  }

  function handleDelete() {
    setConfirmState({
      open: true,
      title: "Delete DNS Resolver Config",
      description: "Remove DNS resolver configuration for this group? Hosts will no longer have DNS managed from this group.",
      action: async () => {
        setConfirmState((prev) => (prev ? { ...prev, loading: true } : null))
        try {
          await deleteMutation.mutateAsync(undefined as never)
        } finally {
          setConfirmState(null)
        }
      },
    })
  }

  function addNameserver() {
    const val = nsInput.trim()
    if (val && !nameservers.includes(val)) {
      setNameservers([...nameservers, val])
      setNsInput("")
    }
  }

  function removeNameserver(idx: number) {
    setNameservers(nameservers.filter((_, i) => i !== idx))
  }

  function addSearchDomain() {
    const val = sdInput.trim()
    if (val && !searchDomains.includes(val)) {
      setSearchDomains([...searchDomains, val])
      setSdInput("")
    }
  }

  function removeSearchDomain(idx: number) {
    setSearchDomains(searchDomains.filter((_, i) => i !== idx))
  }

  function addOption() {
    setOptions([...options, { key: "", value: "" }])
  }

  function removeOption(idx: number) {
    setOptions(options.filter((_, i) => i !== idx))
  }

  async function fetchPreview() {
    setPreviewLoading(true)
    setPreviewError(null)
    try {
      const text = await apiFetch<string>(`/api/groups/${id}/resolver`, {
        headers: { Accept: "text/plain" },
      })
      setPreview(typeof text === "string" ? text : JSON.stringify(text, null, 2))
    } catch (err) {
      setPreviewError(err instanceof Error ? err.message : "Failed to load preview")
    } finally {
      setPreviewLoading(false)
    }
  }

  if (showLoading) {
    return (
      <div className="space-y-6">
        {!embedded && <Breadcrumb items={[{ label: "Groups", href: "/groups" }, { label: group?.name ?? "Group", href: `/groups/${id}` }, { label: "DNS Resolver" }]} />}
        <CardSkeleton />
      </div>
    )
  }

  if ((is404 || (!hasConfig && !resolverQuery.isLoading)) && !formReady) {
    return (
      <div className="space-y-6">
        {!embedded && <Breadcrumb items={[{ label: "Groups", href: "/groups" }, { label: group?.name ?? "Group", href: `/groups/${id}` }, { label: "DNS Resolver" }]} />}
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-bold text-white">DNS Resolver</h1>
        </div>
        {gitopsEnabled && (
          <div className="flex items-start gap-3 p-4 rounded-lg bg-blue-950 border border-blue-800">
            <GitBranch className="h-5 w-5 text-blue-400 flex-shrink-0 mt-0.5" />
            <div>
              <p className="text-blue-200 font-medium">GitOps Enabled</p>
              <p className="text-blue-300 text-sm mt-1">DNS resolver config is managed via GitOps. Changes must be pushed to Git.</p>
            </div>
          </div>
        )}
        <div className="rounded-lg border border-slate-700 bg-slate-900 p-8 text-center">
          <p className="text-slate-400 mb-4">DNS is not managed for this group.</p>
          {!gitopsEnabled && <Button onClick={initNewForm}>Configure DNS</Button>}
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {!embedded && <Breadcrumb items={[{ label: "Groups", href: "/groups" }, { label: group?.name ?? "Group", href: `/groups/${id}` }, { label: "DNS Resolver" }]} />}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white">DNS Resolver</h1>
        <div className="flex gap-2">
          {hasConfig && (
            <Button variant="outline" onClick={fetchPreview} disabled={previewLoading}>
              {previewLoading ? "Loading..." : "Preview"}
            </Button>
          )}
        </div>
      </div>

      {gitopsEnabled && (
        <div className="flex items-start gap-3 p-4 rounded-lg bg-blue-950 border border-blue-800">
          <GitBranch className="h-5 w-5 text-blue-400 flex-shrink-0 mt-0.5" />
          <div>
            <p className="text-blue-200 font-medium">GitOps Enabled</p>
            <p className="text-blue-300 text-sm mt-1">DNS resolver config is managed via GitOps. Changes must be pushed to Git.</p>
          </div>
        </div>
      )}

      {previewError && (
        <div className="text-red-400 text-sm">{previewError}</div>
      )}

      {preview !== null && (
        <div className="rounded-lg border border-slate-700 bg-slate-950 p-4">
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-sm font-medium text-slate-300">Resolver config preview</h3>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setPreview(null)}
              className="text-slate-400 hover:text-white"
            >
              Close
            </Button>
          </div>
          <pre className="text-xs text-slate-300 font-mono whitespace-pre overflow-x-auto">{preview}</pre>
        </div>
      )}

      <form onSubmit={handleSave} className="space-y-6">
        <div className="rounded-lg border border-slate-700 bg-slate-900 p-6 space-y-6">
          {/* Resolver Type */}
          <div className="space-y-2">
            <Label htmlFor="resolver-type">Resolver Type</Label>
            <select
              id="resolver-type"
              value={resolverType}
              onChange={(e) => setResolverType(e.target.value as typeof resolverType)}
              disabled={controlsDisabled}
              title={gitopsEnabled ? "Managed via GitOps" : undefined}
              className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30"
            >
              <option value="resolv_conf">{RESOLVER_TYPE_LABELS.resolv_conf}</option>
              <option value="systemd_resolved">{RESOLVER_TYPE_LABELS.systemd_resolved}</option>
              <option value="networkmanager">{RESOLVER_TYPE_LABELS.networkmanager}</option>
            </select>
          </div>

          {/* Nameservers */}
          <div className="space-y-2">
            <Label>Nameservers</Label>
            {nameservers.length > 0 && (
              <div className="space-y-1">
                {nameservers.map((ns, idx) => (
                  <div key={idx} className="flex items-center gap-2 rounded border border-slate-700 bg-slate-800 px-3 py-1.5">
                    <span className="text-sm font-mono text-slate-300 flex-1">{ns}</span>
                    <button
                      type="button"
                      onClick={() => removeNameserver(idx)}
                      disabled={controlsDisabled}
                      title={gitopsEnabled ? "Managed via GitOps" : undefined}
                      className="text-red-400 hover:text-red-300 text-sm px-1 disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      &times;
                    </button>
                  </div>
                ))}
              </div>
            )}
            <div className="flex gap-2">
              <Input
                type="text"
                placeholder="e.g. 8.8.8.8 or 2001:4860:4860::8888"
                value={nsInput}
                onChange={(e) => setNsInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") { e.preventDefault(); addNameserver() }
                }}
                disabled={controlsDisabled}
                title={gitopsEnabled ? "Managed via GitOps" : undefined}
                className="flex-1"
              />
              <Button type="button" variant="outline" size="sm" onClick={addNameserver} disabled={controlsDisabled} title={gitopsEnabled ? "Managed via GitOps" : undefined}>
                Add
              </Button>
            </div>
            <p className="text-xs text-slate-500">IP addresses of DNS servers, in order of preference</p>
          </div>

          {/* Search Domains */}
          <div className="space-y-2">
            <Label>Search Domains</Label>
            {searchDomains.length > 0 && (
              <div className="space-y-1">
                {searchDomains.map((sd, idx) => (
                  <div key={idx} className="flex items-center gap-2 rounded border border-slate-700 bg-slate-800 px-3 py-1.5">
                    <span className="text-sm font-mono text-slate-300 flex-1">{sd}</span>
                    <button
                      type="button"
                      onClick={() => removeSearchDomain(idx)}
                      disabled={controlsDisabled}
                      title={gitopsEnabled ? "Managed via GitOps" : undefined}
                      className="text-red-400 hover:text-red-300 text-sm px-1 disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      &times;
                    </button>
                  </div>
                ))}
              </div>
            )}
            <div className="flex gap-2">
              <Input
                type="text"
                placeholder="e.g. example.com"
                value={sdInput}
                onChange={(e) => setSdInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") { e.preventDefault(); addSearchDomain() }
                }}
                disabled={controlsDisabled}
                title={gitopsEnabled ? "Managed via GitOps" : undefined}
                className="flex-1"
              />
              <Button type="button" variant="outline" size="sm" onClick={addSearchDomain} disabled={controlsDisabled} title={gitopsEnabled ? "Managed via GitOps" : undefined}>
                Add
              </Button>
            </div>
            <p className="text-xs text-slate-500">Domain suffixes to search for unqualified hostnames</p>
          </div>

          {/* Options */}
          <div className="space-y-2">
            <Label>Options</Label>
            <div className="space-y-2">
              {options.map((opt, idx) => (
                <div key={idx} className="flex items-center gap-2">
                  <select
                    value={opt.key}
                    onChange={(e) => {
                      const updated = options.map((o, i) => i === idx ? { ...o, key: e.target.value } : o)
                      setOptions(updated)
                    }}
                    disabled={controlsDisabled}
                    title={gitopsEnabled ? "Managed via GitOps" : undefined}
                    className="w-40 rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30"
                  >
                    <option value="">Select option…</option>
                    {OPTION_KEYS.map((k) => (
                      <option key={k} value={k}>{k}</option>
                    ))}
                  </select>
                  <Input
                    type="text"
                    placeholder="value"
                    value={opt.value}
                    onChange={(e) => {
                      const updated = options.map((o, i) => i === idx ? { ...o, value: e.target.value } : o)
                      setOptions(updated)
                    }}
                    disabled={controlsDisabled}
                    title={gitopsEnabled ? "Managed via GitOps" : undefined}
                    className="flex-1 font-mono text-xs"
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => removeOption(idx)}
                    disabled={controlsDisabled}
                    title={gitopsEnabled ? "Managed via GitOps" : undefined}
                    className="text-red-400 hover:text-red-300 hover:bg-red-950 px-2"
                  >
                    &times;
                  </Button>
                </div>
              ))}
              <Button type="button" variant="outline" size="sm" onClick={addOption} disabled={controlsDisabled} title={gitopsEnabled ? "Managed via GitOps" : undefined}>
                + Add option
              </Button>
            </div>
          </div>

          {/* DNS-over-TLS (only for systemd-resolved) */}
          {resolverType === "systemd_resolved" && (
            <div className="flex items-center gap-2">
              <input
                id="dns-over-tls"
                type="checkbox"
                checked={dnsOverTls}
                onChange={(e) => setDnsOverTls(e.target.checked)}
                disabled={controlsDisabled}
                title={gitopsEnabled ? "Managed via GitOps" : undefined}
                className="rounded border-input"
              />
              <Label htmlFor="dns-over-tls">DNS-over-TLS</Label>
              <span className="text-xs text-slate-500">Encrypt DNS queries (systemd-resolved only)</span>
            </div>
          )}
        </div>

        {saveMutation.error && (
          <p className="text-sm text-red-400">{saveMutation.error.message}</p>
        )}

        <div className="flex gap-3">
          <Button type="submit" disabled={controlsDisabled} title={gitopsEnabled ? "Managed via GitOps" : undefined}>
            {saveMutation.isPending ? "Saving..." : hasConfig ? "Save Changes" : "Create Config"}
          </Button>
          {hasConfig && (
            <Button
              type="button"
              variant="destructive"
              onClick={handleDelete}
              disabled={deleteMutation.isPending || gitopsEnabled}
              title={gitopsEnabled ? "Managed via GitOps" : undefined}
            >
              {deleteMutation.isPending ? "Deleting..." : "Delete Config"}
            </Button>
          )}
        </div>
      </form>

      {confirmState && (
        <ConfirmDialog
          open={confirmState.open}
          onOpenChange={(open) => !open && setConfirmState(null)}
          title={confirmState.title}
          description={confirmState.description}
          confirmLabel="Delete"
          variant="destructive"
          loading={confirmState.loading}
          onConfirm={confirmState.action}
        />
      )}
    </div>
  )
}
