"use client"

import { useState, type FormEvent } from "react"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import type { ResolverConfig } from "@/lib/types"
import { Banner, CodeBlock, Confirm, Empty, Field, Tag, Toolbar } from "@/components/ld"
import { GROUP_KEYS, GitOpsBanner, useEditorGroup } from "./shared"

type ResolverType = ResolverConfig["resolver_type"]

const RESOLVER_TYPE_LABELS: Record<ResolverType, string> = {
  resolv_conf: "resolv.conf",
  systemd_resolved: "systemd-resolved",
  networkmanager: "NetworkManager",
}

const OPTION_KEYS = ["ndots", "timeout", "attempts", "rotate", "edns0"] as const

/** A list of values with an input to add one and an × on each. */
function ChipList({ items, onRemove, input, onInput, onAdd, placeholder, disabled, label }: { items: string[]; onRemove: (i: number) => void; input: string; onInput: (v: string) => void; onAdd: () => void; placeholder: string; disabled: boolean; label: string }) {
  return (
    <div className="flex flex-col gap-1.5">
      {items.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {items.map((v, i) => (
            <span key={v} className="inline-flex items-center gap-1 rounded border border-line bg-surface-2 py-0.5 pl-2 pr-1 text-[11.5px]">
              <span className="mono">{v}</span>
              {!disabled && (
                <button type="button" className="border-0 bg-transparent p-0 text-[13px] leading-none text-text-3 hover:text-text" onClick={() => onRemove(i)} aria-label={`remove ${v}`}>
                  ×
                </button>
              )}
            </span>
          ))}
        </div>
      )}
      {!disabled && (
        <div className="flex gap-1.5">
          <input
            className="inp mono"
            style={{ maxWidth: 280 }}
            placeholder={placeholder}
            aria-label={label}
            value={input}
            onChange={(e) => onInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault()
                onAdd()
              }
            }}
          />
          <button type="button" className="btn btn-sm" onClick={onAdd} disabled={!input.trim()}>
            add
          </button>
        </div>
      )}
    </div>
  )
}

/**
 * The DNS resolver configuration a group declares — one document per
 * group rather than a list: nameservers, search domains, options and
 * the backend that writes them. Embedded in the group page's Config tab.
 */
export function ResolverEditor({ groupId }: { groupId: number }) {
  const { group, gitops } = useEditorGroup(groupId)

  const resolverQuery = useQuery<ResolverConfig | null>({
    queryKey: ["group-resolver", groupId],
    queryFn: () => apiFetch<ResolverConfig | null>(`/api/groups/${groupId}/resolver`),
    enabled: !!groupId,
  })
  // The endpoint returns 200+null when no resolver config exists yet,
  // which is the common case rather than an error. Distinguish "not
  // configured" (data === null) from "request failed" (error truthy).
  const notConfigured = !resolverQuery.isLoading && !resolverQuery.error && resolverQuery.data === null
  const hasConfig = !!resolverQuery.data && !resolverQuery.error

  const [resolverType, setResolverType] = useState<ResolverType>("resolv_conf")
  const [nameservers, setNameservers] = useState<string[]>([])
  const [nsInput, setNsInput] = useState("")
  const [searchDomains, setSearchDomains] = useState<string[]>([])
  const [sdInput, setSdInput] = useState("")
  const [options, setOptions] = useState<{ key: string; value: string }[]>([])
  const [dnsOverTls, setDnsOverTls] = useState(false)
  const [formReady, setFormReady] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [preview, setPreview] = useState<string | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)

  function populateForm(config: ResolverConfig) {
    setResolverType(config.resolver_type)
    setNameservers([...config.nameservers])
    setSearchDomains([...config.search_domains])
    setOptions(Object.entries(config.options).map(([key, value]) => ({ key, value: String(value) })))
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

  if (hasConfig && !formReady && resolverQuery.data) populateForm(resolverQuery.data)

  const saveMutation = useApiMutation<unknown, Record<string, unknown>, ResolverConfig>({
    mutationFn: (payload) => apiFetch(`/api/groups/${groupId}/resolver`, { method: "PUT", body: JSON.stringify(payload) }),
    invalidateKeys: [["group-resolver", groupId], ...GROUP_KEYS],
    successMessage: "DNS resolver configuration saved",
  })
  const deleteMutation = useApiMutation({
    mutationFn: () => apiFetch(`/api/groups/${groupId}/resolver`, { method: "DELETE" }),
    invalidateKeys: [["group-resolver", groupId], ...GROUP_KEYS],
    successMessage: "DNS resolver configuration deleted",
    onSuccess: () => {
      setFormReady(false)
      setConfirmDelete(false)
      setPreview(null)
    },
  })

  const disabled = gitops || saveMutation.isPending

  function handleSave(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const optionsObj: Record<string, number | string> = {}
    for (const o of options) {
      const k = o.key.trim()
      if (!k) continue
      const numVal = Number(o.value)
      optionsObj[k] = !isNaN(numVal) && o.value.trim() !== "" ? numVal : o.value
    }
    saveMutation.mutate({ nameservers, search_domains: searchDomains, options: optionsObj, resolver_type: resolverType, dns_over_tls: dnsOverTls })
  }

  const addTo = (list: string[], set: (v: string[]) => void, input: string, clear: () => void) => {
    const val = input.trim()
    if (val && !list.includes(val)) {
      set([...list, val])
      clear()
    }
  }

  async function fetchPreview() {
    setPreviewLoading(true)
    setPreviewError(null)
    try {
      const text = await apiFetch<string>(`/api/groups/${groupId}/resolver`, { headers: { Accept: "text/plain" } })
      setPreview(typeof text === "string" ? text : JSON.stringify(text, null, 2))
    } catch (err) {
      setPreviewError(err instanceof Error ? err.message : "Failed to load preview")
    } finally {
      setPreviewLoading(false)
    }
  }

  const showForm = formReady && (hasConfig || notConfigured)

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Toolbar
        actions={
          <>
            {hasConfig && (
              <button type="button" className="btn btn-sm" onClick={() => void fetchPreview()} disabled={previewLoading}>
                {previewLoading ? "Loading…" : "Preview file"}
              </button>
            )}
            {hasConfig && !gitops && (
              <button type="button" className="btn btn-sm btn-ghost text-danger" onClick={() => setConfirmDelete(true)} disabled={deleteMutation.isPending}>
                Delete config
              </button>
            )}
          </>
        }
      >
        <span className="tt">{hasConfig ? `${nameservers.length} nameserver${nameservers.length === 1 ? "" : "s"} · ${RESOLVER_TYPE_LABELS[resolverType]}` : "not declared"}</span>
      </Toolbar>
      {gitops && <GitOpsBanner group={group} what="the resolver configuration is" />}
      {resolverQuery.error && (
        <Banner tone="danger" flush>
          Could not load the resolver configuration: {resolverQuery.error.message}
        </Banner>
      )}

      {resolverQuery.isLoading && <div className="p-3.5 text-xs text-text-3">Loading…</div>}

      {notConfigured && !formReady && (
        <Empty
          title="No resolver configuration"
          note="Hosts keep whatever resolves their names today. Declaring one here writes it to every host in the group on the next plan."
          action={
            !gitops && (
              <button type="button" className="btn btn-sm btn-primary" onClick={initNewForm}>
                Configure DNS
              </button>
            )
          }
        />
      )}

      {showForm && (
        <form onSubmit={handleSave} className="scroll flex min-h-0 flex-1 flex-col gap-[11px] p-3.5" style={{ maxWidth: 720 }}>
          <div className="grid gap-[11px] grid-cols-1 sm:grid-cols-[220px_1fr]">
            <Field label="resolver" htmlFor="resolver-type" hint="what writes the file">
              <select id="resolver-type" className="inp" value={resolverType} onChange={(e) => setResolverType(e.target.value as ResolverType)} disabled={disabled}>
                {(Object.keys(RESOLVER_TYPE_LABELS) as ResolverType[]).map((t) => (
                  <option key={t} value={t}>
                    {RESOLVER_TYPE_LABELS[t]}
                  </option>
                ))}
              </select>
            </Field>
            {resolverType === "systemd_resolved" && (
              <label className="flex items-center gap-2 self-end pb-1.5 text-xs text-text">
                <input type="checkbox" checked={dnsOverTls} onChange={(e) => setDnsOverTls(e.target.checked)} disabled={disabled} />
                DNS over TLS
              </label>
            )}
          </div>
          <Field as="div" label="nameservers" hint="in the order they are tried">
            <ChipList items={nameservers} onRemove={(i) => setNameservers(nameservers.filter((_, j) => j !== i))} input={nsInput} onInput={setNsInput} onAdd={() => addTo(nameservers, setNameservers, nsInput, () => setNsInput(""))} placeholder="10.0.0.53" disabled={disabled} label="nameserver to add" />
          </Field>
          <Field as="div" label="search domains" hint="optional">
            <ChipList items={searchDomains} onRemove={(i) => setSearchDomains(searchDomains.filter((_, j) => j !== i))} input={sdInput} onInput={setSdInput} onAdd={() => addTo(searchDomains, setSearchDomains, sdInput, () => setSdInput(""))} placeholder="lab.internal" disabled={disabled} label="search domain to add" />
          </Field>
          <Field as="div" label="options" hint="resolv.conf options — a bare key for flags">
            <div className="flex flex-col gap-1.5">
              {options.map((o, idx) => (
                <div key={idx} className="flex items-center gap-1.5">
                  <select className="inp mono" style={{ width: 140 }} value={o.key} onChange={(e) => setOptions(options.map((x, i) => (i === idx ? { ...x, key: e.target.value } : x)))} disabled={disabled} aria-label={`option ${idx + 1}`}>
                    <option value="">— option —</option>
                    {OPTION_KEYS.map((k) => (
                      <option key={k} value={k}>
                        {k}
                      </option>
                    ))}
                  </select>
                  <input className="inp mono" style={{ width: 120 }} placeholder="value" value={o.value} onChange={(e) => setOptions(options.map((x, i) => (i === idx ? { ...x, value: e.target.value } : x)))} disabled={disabled} aria-label={`option ${idx + 1} value`} />
                  {!disabled && (
                    <button type="button" className="btn btn-sm btn-ghost text-danger" onClick={() => setOptions(options.filter((_, i) => i !== idx))} aria-label="remove option">
                      ×
                    </button>
                  )}
                </div>
              ))}
              {!disabled && (
                <div>
                  <button type="button" className="btn btn-sm" onClick={() => setOptions([...options, { key: "", value: "" }])}>
                    + add option
                  </button>
                </div>
              )}
            </div>
          </Field>

          {saveMutation.error && <Banner tone="danger">{saveMutation.error.message}</Banner>}
          {previewError && <Banner tone="danger">{previewError}</Banner>}
          {preview !== null && (
            <CodeBlock
              title={resolverType === "resolv_conf" ? "/etc/resolv.conf" : RESOLVER_TYPE_LABELS[resolverType]}
              tag={<Tag>preview</Tag>}
              actions={
                <button type="button" className="btn btn-sm btn-ghost" onClick={() => setPreview(null)}>
                  close
                </button>
              }
              wrap={false}
            >
              {preview}
            </CodeBlock>
          )}

          {!gitops && (
            <div className="flex items-center gap-2">
              <span className="tt mr-auto">desired state only — nothing applies until a plan runs</span>
              {notConfigured && (
                <button type="button" className="btn btn-sm" onClick={() => setFormReady(false)}>
                  Cancel
                </button>
              )}
              <button type="submit" className="btn btn-sm btn-primary" disabled={saveMutation.isPending || nameservers.length === 0}>
                {saveMutation.isPending ? "Saving…" : hasConfig ? "Save changes" : "Create config"}
              </button>
            </div>
          )}
        </form>
      )}

      <Confirm
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title="Delete resolver configuration"
        description="The group stops declaring DNS; hosts keep their current resolver file until a plan runs. This cannot be undone."
        confirmLabel="Delete"
        variant="destructive"
        loading={deleteMutation.isPending}
        onConfirm={() => deleteMutation.mutate()}
      />
    </div>
  )
}
