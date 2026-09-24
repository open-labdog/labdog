"use client"

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { Banner, Confirm, Empty, Facts, Field, Modal, Provenance, Tag, Toolbar } from "@/components/ld"
import type { EffectiveResolverConfig, ResolverConfig } from "@/lib/types"
import { CurrentStateSection } from "./shared"
import type { ModuleCurrentState } from "@/lib/types"

const notFoundNoRetry = (count: number, error: unknown) => {
  if (error && typeof error === "object" && "status" in error && (error as { status: number }).status === 404) return false
  return count < 3
}

const RESOLVER_TYPE_LABEL: Record<string, string> = { resolv_conf: "resolv.conf", systemd_resolved: "systemd-resolved", networkmanager: "NetworkManager" }

export function ResolverTab({
  hostId,
  currentState,
  syncBusy,
  onSync,
}: {
  hostId: number
  currentState: ModuleCurrentState[] | undefined
  syncBusy: boolean
  onSync: () => void
}) {
  const [dialogOpen, setDialogOpen] = useState(false)
  const [resolverType, setResolverType] = useState<"resolv_conf" | "systemd_resolved" | "networkmanager">("resolv_conf")
  const [nameservers, setNameservers] = useState<string[]>([])
  const [nsInput, setNsInput] = useState("")
  const [searchDomains, setSearchDomains] = useState<string[]>([])
  const [sdInput, setSdInput] = useState("")
  const [options, setOptions] = useState<{ key: string; value: string }[]>([])
  const [dnsOverTls, setDnsOverTls] = useState(false)
  const [deleteConfirm, setDeleteConfirm] = useState(false)

  const { data: effective, isLoading, error } = useQuery<EffectiveResolverConfig>({
    queryKey: ["host-effective-resolver", hostId],
    queryFn: () => apiFetch<EffectiveResolverConfig>(`/api/hosts/${hostId}/effective-resolver`),
    retry: notFoundNoRetry,
  })
  const { data: override } = useQuery<ResolverConfig>({
    queryKey: ["host-resolver-override", hostId],
    queryFn: () => apiFetch<ResolverConfig>(`/api/hosts/${hostId}/resolver`),
    retry: notFoundNoRetry,
  })
  const notConfigured = !isLoading && !error && effective === null

  const saveMutation = useApiMutation({
    mutationFn: (payload: Record<string, unknown>) => apiFetch(`/api/hosts/${hostId}/resolver`, { method: "PUT", body: JSON.stringify(payload) }),
    invalidateKeys: [["host-effective-resolver", hostId], ["host-resolver-override", hostId]],
    onSuccess: () => setDialogOpen(false),
  })
  const deleteMutation = useApiMutation({
    mutationFn: () => apiFetch(`/api/hosts/${hostId}/resolver`, { method: "DELETE" }),
    invalidateKeys: [["host-effective-resolver", hostId], ["host-resolver-override", hostId]],
    onSuccess: () => setDeleteConfirm(false),
  })

  function openEdit() {
    if (effective) {
      setResolverType(effective.resolver_type)
      setNameservers([...effective.nameservers])
      setSearchDomains([...effective.search_domains])
      setOptions(Object.entries(effective.options).map(([key, value]) => ({ key, value: String(value) })))
      setDnsOverTls(effective.dns_over_tls)
    } else {
      setResolverType("resolv_conf")
      setNameservers([])
      setSearchDomains([])
      setOptions([])
      setDnsOverTls(false)
    }
    setNsInput("")
    setSdInput("")
    saveMutation.reset()
    setDialogOpen(true)
  }

  function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const optionsObj: Record<string, number | string> = {}
    for (const o of options) {
      const k = o.key.trim()
      if (!k) continue
      const n = Number(o.value)
      optionsObj[k] = !isNaN(n) && o.value.trim() !== "" ? n : o.value
    }
    saveMutation.mutate({ nameservers, search_domains: searchDomains, options: optionsObj, resolver_type: resolverType, dns_over_tls: dnsOverTls })
  }

  function addNs() {
    const v = nsInput.trim()
    if (v && !nameservers.includes(v)) { setNameservers([...nameservers, v]); setNsInput("") }
  }
  function addSd() {
    const v = sdInput.trim()
    if (v && !searchDomains.includes(v)) { setSearchDomains([...searchDomains, v]); setSdInput("") }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Toolbar
        actions={
          <>
            <button type="button" className="btn btn-sm btn-ghost" disabled={syncBusy} onClick={onSync}>sync dns</button>
            {override && (
              <button type="button" className="btn btn-sm btn-ghost text-danger" disabled={deleteMutation.isPending} onClick={() => setDeleteConfirm(true)}>
                delete override
              </button>
            )}
            <button type="button" className="btn btn-sm btn-primary" onClick={openEdit}>
              {override ? "edit override" : effective ? "create override" : "configure dns"}
            </button>
          </>
        }
      >
        <span className="tt">effective dns resolver</span>
      </Toolbar>

      {error && <Banner tone="danger" flush>Could not load the DNS resolver: {error.message}</Banner>}
      {deleteMutation.error && <Banner tone="danger" flush>{deleteMutation.error.message}</Banner>}

      {notConfigured && <Empty title="DNS is not managed for this host" note="Configure DNS at the group level to get started, or configure it directly on this host." action={<button type="button" className="btn btn-sm btn-primary" onClick={openEdit}>configure dns</button>} />}

      {!isLoading && !error && effective && (
        <div className="scroll flex flex-1 flex-col gap-3 p-3.5">
          <Facts
            items={[
              { k: "source", v: <Provenance origin={effective.source} label={effective.source === "host" ? "this host" : effective.source_name} /> },
              { k: "resolver type", v: RESOLVER_TYPE_LABEL[effective.resolver_type] },
              { k: "nameservers", v: effective.nameservers.length ? effective.nameservers.join(", ") : "none", mono: true, span: 2 },
              { k: "search domains", v: effective.search_domains.length ? effective.search_domains.join(", ") : "none", mono: true, span: 2 },
              ...(Object.keys(effective.options).length ? [{ k: "options", v: Object.entries(effective.options).map(([k, v]) => `${k}=${v}`).join(", "), mono: true, span: 2 }] : []),
              ...(effective.resolver_type === "systemd_resolved" ? [{ k: "dns-over-tls", v: <Tag tone={effective.dns_over_tls ? "ok" : undefined}>{effective.dns_over_tls ? "enabled" : "disabled"}</Tag> }] : []),
            ]}
          />
          <span className="text-[11px] text-text-faint">{override ? "This host has a resolver override." : "Inherited from group configuration."}</span>
        </div>
      )}

      {dialogOpen && (
        <Modal
          title={override ? "Edit host override" : "Create host override"}
          w={620}
          onClose={() => setDialogOpen(false)}
          onSubmit={onSubmit}
          footer={
            <>
              <button type="button" className="btn ml-auto" onClick={() => setDialogOpen(false)}>Cancel</button>
              <button type="submit" className="btn btn-primary" disabled={saveMutation.isPending}>{saveMutation.isPending ? "Saving…" : override ? "Save changes" : "Create override"}</button>
            </>
          }
        >
          <Field as="div" label="resolver type">
            <select className="inp" value={resolverType} onChange={(e) => setResolverType(e.target.value as typeof resolverType)}>
              <option value="resolv_conf">resolv.conf</option>
              <option value="systemd_resolved">systemd-resolved</option>
              <option value="networkmanager">NetworkManager</option>
            </select>
          </Field>

          <Field as="div" label="nameservers">
            {nameservers.length > 0 && (
              <div className="flex flex-col gap-1">
                {nameservers.map((ns, idx) => (
                  <div key={idx} className="flex items-center gap-2 rounded-r border border-line bg-surface-2 px-2.5 py-1.5">
                    <span className="mono flex-1 text-[11.5px]">{ns}</span>
                    <button type="button" className="btn btn-sm btn-ghost text-danger" onClick={() => setNameservers(nameservers.filter((_, i) => i !== idx))}>&times;</button>
                  </div>
                ))}
              </div>
            )}
            <div className="flex gap-1.5">
              <input className="inp mono" style={{ flex: 1 }} placeholder="e.g. 8.8.8.8 or 2001:4860:4860::8888" value={nsInput} onChange={(e) => setNsInput(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addNs() } }} />
              <button type="button" className="btn btn-sm" onClick={addNs}>add</button>
            </div>
          </Field>

          <Field as="div" label="search domains">
            {searchDomains.length > 0 && (
              <div className="flex flex-col gap-1">
                {searchDomains.map((sd, idx) => (
                  <div key={idx} className="flex items-center gap-2 rounded-r border border-line bg-surface-2 px-2.5 py-1.5">
                    <span className="mono flex-1 text-[11.5px]">{sd}</span>
                    <button type="button" className="btn btn-sm btn-ghost text-danger" onClick={() => setSearchDomains(searchDomains.filter((_, i) => i !== idx))}>&times;</button>
                  </div>
                ))}
              </div>
            )}
            <div className="flex gap-1.5">
              <input className="inp mono" style={{ flex: 1 }} placeholder="e.g. example.com" value={sdInput} onChange={(e) => setSdInput(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addSd() } }} />
              <button type="button" className="btn btn-sm" onClick={addSd}>add</button>
            </div>
          </Field>

          <Field as="div" label="options">
            <div className="flex flex-col gap-1.5">
              {options.map((opt, idx) => (
                <div key={idx} className="flex items-center gap-1.5">
                  <select className="inp" style={{ width: 140 }} value={opt.key} onChange={(e) => setOptions(options.map((o, i) => (i === idx ? { ...o, key: e.target.value } : o)))}>
                    <option value="">select option…</option>
                    <option value="ndots">ndots</option>
                    <option value="timeout">timeout</option>
                    <option value="attempts">attempts</option>
                    <option value="rotate">rotate</option>
                    <option value="edns0">edns0</option>
                  </select>
                  <input className="inp mono" style={{ flex: 1 }} placeholder="value" value={opt.value} onChange={(e) => setOptions(options.map((o, i) => (i === idx ? { ...o, value: e.target.value } : o)))} />
                  <button type="button" className="btn btn-sm btn-ghost text-danger" onClick={() => setOptions(options.filter((_, i) => i !== idx))}>&times;</button>
                </div>
              ))}
              <button type="button" className="btn btn-sm" onClick={() => setOptions([...options, { key: "", value: "" }])}>+ add option</button>
            </div>
          </Field>

          {resolverType === "systemd_resolved" && (
            <label className="flex items-center gap-2 text-xs text-text">
              <input type="checkbox" checked={dnsOverTls} onChange={(e) => setDnsOverTls(e.target.checked)} /> dns-over-tls
              <span className="text-text-faint">— encrypt DNS queries (systemd-resolved only)</span>
            </label>
          )}

          {saveMutation.error && <Banner tone="danger">{saveMutation.error.message}</Banner>}
        </Modal>
      )}

      <Confirm
        open={deleteConfirm}
        onOpenChange={setDeleteConfirm}
        title="Delete resolver override"
        description="Remove this host's DNS resolver override? The host reverts to inheriting from the group."
        confirmLabel="Delete"
        variant="destructive"
        loading={deleteMutation.isPending}
        onConfirm={() => deleteMutation.mutate(undefined)}
      />

      <CurrentStateSection moduleType="resolver" modules={currentState} hostId={hostId} />
    </div>
  )
}
