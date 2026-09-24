"use client"

import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { plural } from "@/lib/fleet"
import { Banner, Confirm, Field, Modal, Provenance, Seg, Table, Toolbar } from "@/components/ld"
import type { EffectiveFirewallRule, ChainPolicies, Host } from "@/lib/types"
import { CurrentStateSection, FirewallBackendTag, InstallFirewallSection, formatPorts } from "./shared"
import { def, FIREWALL_ACTION } from "@/lib/status"
import { Tag } from "@/components/ld"
import type { ModuleCurrentState } from "@/lib/types"

const defaults = {
  action: "allow", protocol: "tcp", direction: "input",
  sourceMode: "cidr" as "cidr" | "host", destMode: "cidr" as "cidr" | "host",
  sourceCidr: "", destCidr: "", sourceHostId: null as number | null, destHostId: null as number | null,
  portStart: "", portEnd: "", priority: "0", comment: "",
}

export function FirewallTab({
  hostId,
  host,
  currentState,
  syncBusy,
  onSync,
}: {
  hostId: number
  host: Host | undefined
  currentState: ModuleCurrentState[] | undefined
  syncBusy: boolean
  onSync: () => void
}) {
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<EffectiveFirewallRule | null>(null)
  const [deleting, setDeleting] = useState<EffectiveFirewallRule | null>(null)
  const [form, setForm] = useState(defaults)

  const { data: rules, isLoading, error } = useQuery<EffectiveFirewallRule[]>({
    queryKey: ["host-effective-rules", hostId],
    queryFn: () => apiFetch<EffectiveFirewallRule[]>(`/api/hosts/${hostId}/effective-rules`),
  })
  const { data: policies } = useQuery<ChainPolicies>({
    queryKey: ["host-effective-policies", hostId],
    queryFn: () => apiFetch<ChainPolicies>(`/api/hosts/${hostId}/effective-policies`),
  })
  const { data: hosts = [] } = useQuery<Host[]>({ queryKey: ["hosts"], queryFn: () => apiFetch<Host[]>("/api/hosts"), enabled: dialogOpen })

  const saveMutation = useApiMutation({
    mutationFn: ({ ruleId, payload }: { ruleId?: number; payload: Record<string, unknown> }) =>
      ruleId
        ? apiFetch(`/api/hosts/${hostId}/firewall-rules/${ruleId}`, { method: "PUT", body: JSON.stringify(payload) })
        : apiFetch(`/api/hosts/${hostId}/firewall-rules`, { method: "POST", body: JSON.stringify(payload) }),
    invalidateKeys: [["host-effective-rules", hostId]],
    onSuccess: () => setDialogOpen(false),
  })
  const deleteMutation = useApiMutation({
    mutationFn: (ruleId: number) => apiFetch(`/api/hosts/${hostId}/firewall-rules/${ruleId}`, { method: "DELETE" }),
    invalidateKeys: [["host-effective-rules", hostId]],
    onSuccess: () => setDeleting(null),
  })

  function openCreate() {
    setEditing(null)
    setForm(defaults)
    saveMutation.reset()
    setDialogOpen(true)
  }

  function openEdit(rule: EffectiveFirewallRule) {
    setEditing(rule)
    setForm({
      action: rule.action, protocol: rule.protocol, direction: rule.direction,
      sourceMode: rule.source_host_id != null ? "host" : "cidr",
      destMode: rule.destination_host_id != null ? "host" : "cidr",
      sourceCidr: rule.source_cidr ?? "", destCidr: rule.destination_cidr ?? "",
      sourceHostId: rule.source_host_id ?? null, destHostId: rule.destination_host_id ?? null,
      portStart: rule.port_start != null ? String(rule.port_start) : "",
      portEnd: rule.port_end != null ? String(rule.port_end) : "",
      priority: String(rule.priority), comment: rule.comment ?? "",
    })
    saveMutation.reset()
    setDialogOpen(true)
  }

  function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const payload = {
      action: form.action, protocol: form.protocol, direction: form.direction,
      source_cidr: form.sourceMode === "cidr" ? form.sourceCidr || null : null,
      source_host_id: form.sourceMode === "host" ? form.sourceHostId : null,
      destination_cidr: form.destMode === "cidr" ? form.destCidr || null : null,
      destination_host_id: form.destMode === "host" ? form.destHostId : null,
      port_start: form.portStart ? Number(form.portStart) : null,
      port_end: form.portEnd ? Number(form.portEnd) : null,
      priority: Number(form.priority),
      comment: form.comment || null,
    }
    const ruleId = editing?.source === "host" ? (editing.rule_id ?? undefined) : undefined
    saveMutation.mutate({ ruleId, payload })
  }

  const rows = rules ?? []
  const showPorts = form.protocol !== "icmp" && form.protocol !== "any"

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Toolbar
        actions={
          <>
            <button type="button" className="btn btn-sm btn-ghost" disabled={!host?.ssh_key_id || syncBusy} onClick={onSync}>
              sync rules
            </button>
            <button type="button" className="btn btn-sm btn-primary" onClick={openCreate}>
              add rule
            </button>
          </>
        }
      >
        <span className="tt">{plural(rows.length, "rule")} effective</span>
        {host && <FirewallBackendTag backend={host.firewall_backend} />}
        {policies && (
          <span className="tt text-text-3">
            · input <b className="mono text-text-2">{policies.input}</b> ({policies.input_source_group_name ?? "default"}) · output <b className="mono text-text-2">{policies.output}</b> ({policies.output_source_group_name ?? "default"})
          </span>
        )}
      </Toolbar>

      {error && <Banner tone="danger" flush>Could not load effective rules: {error.message}</Banner>}

      <Table<EffectiveFirewallRule>
        cols={[
          { k: "priority", label: "priority", w: "70px", sortable: false, cell: (r) => <span className="mono num">{r.group_priority ?? r.priority}</span> },
          { k: "action", label: "action", w: "80px", sortable: false, cell: (r) => <Tag tone={def(FIREWALL_ACTION, r.action).tone}>{r.action}</Tag> },
          { k: "protocol", label: "protocol", w: "76px", sortable: false, cell: (r) => <span className="mono uppercase text-[11px]">{r.protocol}</span> },
          { k: "direction", label: "direction", w: "84px", sortable: false, cell: (r) => <span className="capitalize">{r.direction}</span> },
          { k: "source", label: "source", w: "minmax(100px,1fr)", sortable: false, cell: (r) => <span className="mono trunc text-[11px]" title={r.source_host_name ?? r.source_cidr ?? "any"}>{r.source_host_name ?? r.source_cidr ?? "any"}</span> },
          { k: "destination", label: "destination", w: "minmax(100px,1fr)", sortable: false, cell: (r) => <span className="mono trunc text-[11px]" title={r.destination_host_name ?? r.destination_cidr ?? "any"}>{r.destination_host_name ?? r.destination_cidr ?? "any"}</span> },
          { k: "ports", label: "port(s)", w: "76px", sortable: false, cell: (r) => <span className="mono text-[11px]">{formatPorts(r)}</span> },
          { k: "group", label: "source", w: "minmax(110px,1fr)", sortable: false, cell: (r) => <Provenance origin={r.source} label={r.source === "system" ? "system" : r.source === "host" ? "this host" : (r.group_name ?? "")} priority={r.source === "group" ? r.group_priority : undefined} /> },
          { k: "comment", label: "comment", w: "minmax(100px,1fr)", sortable: false, cell: (r) => <span className="trunc text-text-3" title={r.comment ?? ""}>{r.comment ?? "—"}</span> },
          {
            k: "actions", label: "", w: "108px", right: true, sortable: false,
            cell: (r) =>
              r.is_system || r.source === "system" ? (
                <span className="text-[11px] text-text-faint">read-only</span>
              ) : r.source === "group" ? (
                <button type="button" className="btn btn-sm btn-ghost" onClick={() => openEdit(r)}>edit</button>
              ) : r.rule_id != null ? (
                <span className="flex gap-0.5">
                  <button type="button" className="btn btn-sm btn-ghost" onClick={() => openEdit(r)}>edit</button>
                  <button type="button" className="btn btn-sm btn-ghost text-danger" disabled={deleteMutation.isPending} onClick={() => setDeleting(r)}>delete</button>
                </span>
              ) : null,
          },
        ]}
        rows={rows}
        keyOf={(r) => `${r.rule_id ?? "sys"}-${r.group_id ?? "none"}`}
        loading={isLoading}
        empty="No effective rules. Assign this host to a group with rules, or add a host override."
      />

      {host?.firewall_backend === "unknown" && <InstallFirewallSection hostId={hostId} queryClient={queryClient} />}

      {dialogOpen && (
        <Modal
          title={editing ? "Edit firewall rule override" : "Add firewall rule override"}
          w={600}
          onClose={() => setDialogOpen(false)}
          onSubmit={onSubmit}
          footer={
            <>
              <span className="tt mr-auto">host overrides win over every group</span>
              <button type="button" className="btn" onClick={() => setDialogOpen(false)}>Cancel</button>
              <button type="submit" className="btn btn-primary" disabled={saveMutation.isPending}>
                {saveMutation.isPending ? "Saving…" : editing ? "Save changes" : "Create override"}
              </button>
            </>
          }
        >
          <div className="grid gap-[11px] grid-cols-1 sm:grid-cols-[1fr_1fr_1fr]">
            <Field label="action" htmlFor="fw-action">
              <select id="fw-action" className="inp" value={form.action} onChange={(e) => setForm((f) => ({ ...f, action: e.target.value }))}>
                <option value="allow">allow</option>
                <option value="deny">deny</option>
                <option value="reject">reject</option>
              </select>
            </Field>
            <Field label="protocol" htmlFor="fw-protocol">
              <select id="fw-protocol" className="inp" value={form.protocol} onChange={(e) => setForm((f) => ({ ...f, protocol: e.target.value }))}>
                <option value="tcp">tcp</option>
                <option value="udp">udp</option>
                <option value="icmp">icmp</option>
                <option value="any">any</option>
              </select>
            </Field>
            <Field label="direction" htmlFor="fw-direction">
              <select id="fw-direction" className="inp" value={form.direction} onChange={(e) => setForm((f) => ({ ...f, direction: e.target.value }))}>
                <option value="input">input</option>
                <option value="output">output</option>
              </select>
            </Field>
          </div>

          <div className="grid gap-[11px] grid-cols-1 sm:grid-cols-[1fr_1fr]">
            <Field as="div" label={<>source <span className="ml-auto normal-case tracking-normal"><Seg sm options={[{ k: "cidr", label: "CIDR" }, { k: "host", label: "Host" }]} value={form.sourceMode} onChange={(k) => setForm((f) => ({ ...f, sourceMode: k as "cidr" | "host" }))} /></span></>} className="flex-row items-center justify-between">
              {form.sourceMode === "cidr" ? (
                <input className="inp mono" placeholder="0.0.0.0/0" value={form.sourceCidr} onChange={(e) => setForm((f) => ({ ...f, sourceCidr: e.target.value }))} />
              ) : (
                <select className="inp mono" value={form.sourceHostId ?? ""} onChange={(e) => setForm((f) => ({ ...f, sourceHostId: e.target.value ? Number(e.target.value) : null }))}>
                  <option value="">— pick a host —</option>
                  {hosts.map((h) => <option key={h.id} value={h.id}>{h.hostname} · {h.ip_address}</option>)}
                </select>
              )}
            </Field>
            <Field as="div" label={<>destination <span className="ml-auto normal-case tracking-normal"><Seg sm options={[{ k: "cidr", label: "CIDR" }, { k: "host", label: "Host" }]} value={form.destMode} onChange={(k) => setForm((f) => ({ ...f, destMode: k as "cidr" | "host" }))} /></span></>}>
              {form.destMode === "cidr" ? (
                <input className="inp mono" placeholder="0.0.0.0/0" value={form.destCidr} onChange={(e) => setForm((f) => ({ ...f, destCidr: e.target.value }))} />
              ) : (
                <select className="inp mono" value={form.destHostId ?? ""} onChange={(e) => setForm((f) => ({ ...f, destHostId: e.target.value ? Number(e.target.value) : null }))}>
                  <option value="">— pick a host —</option>
                  {hosts.map((h) => <option key={h.id} value={h.id}>{h.hostname} · {h.ip_address}</option>)}
                </select>
              )}
            </Field>
          </div>

          {showPorts && (
            <div className="grid gap-[11px] grid-cols-1 sm:grid-cols-[1fr_1fr]">
              <Field label="port" htmlFor="fw-port-start" hint="blank = all ports">
                <input id="fw-port-start" type="number" min={1} max={65535} className="inp mono num" placeholder="80" value={form.portStart} onChange={(e) => setForm((f) => ({ ...f, portStart: e.target.value }))} />
              </Field>
              <Field label="port end" htmlFor="fw-port-end" hint="for a range">
                <input id="fw-port-end" type="number" min={1} max={65535} className="inp mono num" placeholder="443" value={form.portEnd} onChange={(e) => setForm((f) => ({ ...f, portEnd: e.target.value }))} />
              </Field>
            </div>
          )}

          <div className="grid gap-[11px] grid-cols-1 sm:grid-cols-[100px_1fr]">
            <Field label="priority" htmlFor="fw-priority">
              <input id="fw-priority" type="number" className="inp mono num" value={form.priority} onChange={(e) => setForm((f) => ({ ...f, priority: e.target.value }))} />
            </Field>
            <Field label="comment" htmlFor="fw-comment" hint="optional">
              <input id="fw-comment" className="inp" value={form.comment} onChange={(e) => setForm((f) => ({ ...f, comment: e.target.value }))} />
            </Field>
          </div>

          {saveMutation.error && <Banner tone="danger">{saveMutation.error.message}</Banner>}
        </Modal>
      )}

      {deleting && (
        <Confirm
          open
          onOpenChange={(open) => !open && setDeleting(null)}
          title="Delete firewall rule override"
          description="This removes the host-level override. The change takes effect on the next sync."
          confirmLabel="Delete"
          variant="destructive"
          loading={deleteMutation.isPending}
          onConfirm={() => deleteMutation.mutate(deleting.rule_id!)}
        />
      )}

      <CurrentStateSection moduleType="firewall" modules={currentState} hostId={hostId} />
    </div>
  )
}
