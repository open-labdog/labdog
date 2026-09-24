"use client"

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { plural } from "@/lib/fleet"
import { def, enabledDef, SYSTEMD_STATE } from "@/lib/status"
import { Banner, Confirm, Field, Modal, Provenance, Table, Tag, Toolbar } from "@/components/ld"
import type { EffectiveService, Host, LiveService, ServiceCommandResult, ServiceRule } from "@/lib/types"
import { CurrentStateSection } from "./shared"
import type { ModuleCurrentState } from "@/lib/types"

const defaults = { name: "", deployMode: "override" as "full" | "override", unitContent: "", state: "running" as "running" | "stopped", enabled: true }

export function ServicesTab({
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
  const [dialogOpen, setDialogOpen] = useState(false)
  const [mode, setMode] = useState<"add" | "edit">("add")
  const [editRuleId, setEditRuleId] = useState<number | null>(null)
  const [form, setForm] = useState(defaults)
  const [originalUnit, setOriginalUnit] = useState<string | null>(null)
  const [originalLoading, setOriginalLoading] = useState(false)
  const [originalAttempted, setOriginalAttempted] = useState(false)
  const [deleting, setDeleting] = useState<string | null>(null)

  const { data: services, isLoading, error } = useQuery<EffectiveService[]>({
    queryKey: ["host-effective-services", hostId],
    queryFn: () => apiFetch<EffectiveService[]>(`/api/hosts/${hostId}/effective-services`),
  })
  const { data: overrides } = useQuery<ServiceRule[]>({
    queryKey: ["host-service-overrides", hostId],
    queryFn: () => apiFetch<ServiceRule[]>(`/api/hosts/${hostId}/services`),
  })

  const saveMutation = useApiMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      mode === "edit" && editRuleId != null
        ? apiFetch(`/api/hosts/${hostId}/services/${editRuleId}`, { method: "PUT", body: JSON.stringify(payload) })
        : apiFetch(`/api/hosts/${hostId}/services`, { method: "POST", body: JSON.stringify(payload) }),
    invalidateKeys: [["host-effective-services", hostId], ["host-service-overrides", hostId]],
    onSuccess: () => setDialogOpen(false),
  })
  const deleteMutation = useApiMutation({
    mutationFn: (overrideId: number) => apiFetch(`/api/hosts/${hostId}/services/${overrideId}`, { method: "DELETE" }),
    invalidateKeys: [["host-effective-services", hostId], ["host-service-overrides", hostId]],
    onSuccess: () => setDeleting(null),
  })

  function openCreate() {
    setMode("add")
    setEditRuleId(null)
    setForm(defaults)
    setOriginalUnit(null)
    setOriginalAttempted(false)
    saveMutation.reset()
    setDialogOpen(true)
  }

  async function openEditFromEffective(svc: EffectiveService) {
    setForm({ name: svc.service_name, deployMode: svc.deploy_mode, unitContent: svc.unit_content ?? "", state: svc.state === "stopped" ? "stopped" : "running", enabled: svc.enabled })
    setOriginalUnit(null)
    setOriginalLoading(true)
    setOriginalAttempted(true)
    saveMutation.reset()
    if (svc.source === "host") {
      const override = overrides?.find((o) => o.service_name === svc.service_name)
      setMode("edit")
      setEditRuleId(override?.id ?? null)
    } else {
      setMode("add")
      setEditRuleId(null)
    }
    setDialogOpen(true)
    const unitName = svc.service_name.endsWith(".service") ? svc.service_name : `${svc.service_name}.service`
    try {
      const res = await apiFetch<{ content: string }>(`/api/services/hosts/${hostId}/unit-file/${unitName}`)
      setOriginalUnit(res.content)
    } catch {
      setOriginalUnit(null)
    } finally {
      setOriginalLoading(false)
    }
  }

  function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const base = { deploy_mode: form.deployMode, unit_content: form.unitContent || null, state: form.state, enabled: form.enabled }
    saveMutation.mutate(mode === "edit" ? base : { service_name: form.name, ...base })
  }

  function handleDelete(serviceName: string) {
    const override = overrides?.find((o) => o.service_name === serviceName)
    if (override) deleteMutation.mutate(override.id)
  }

  const rows = services ?? []

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Toolbar
        actions={
          <>
            <button type="button" className="btn btn-sm btn-ghost" disabled={!host?.ssh_key_id || syncBusy} onClick={onSync}>
              sync services
            </button>
            <button type="button" className="btn btn-sm btn-primary" onClick={openCreate}>
              add service
            </button>
          </>
        }
      >
        <span className="tt">{plural(rows.length, "unit")} effective</span>
      </Toolbar>

      {error && <Banner tone="danger" flush>Could not load effective services: {error.message}</Banner>}
      {deleteMutation.error && <Banner tone="danger" flush>{deleteMutation.error.message}</Banner>}

      <Table<EffectiveService>
        cols={[
          { k: "name", label: "unit", w: "minmax(160px,1.2fr)", sortable: false, cell: (s) => <span className="mono font-medium text-text">{s.service_name}</span> },
          { k: "state", label: "state", w: "96px", sortable: false, cell: (s) => <Tag tone={def(SYSTEMD_STATE, s.state).tone}>{s.state}</Tag> },
          { k: "enabled", label: "at boot", w: "96px", sortable: false, cell: (s) => <Tag tone={enabledDef(s.enabled).tone}>{enabledDef(s.enabled).label}</Tag> },
          { k: "source", label: "source", w: "minmax(120px,1fr)", sortable: false, cell: (s) => <Provenance origin={s.source} label={s.source === "host" ? "this host" : s.source_name} /> },
          {
            k: "actions", label: "", w: "118px", right: true, sortable: false,
            cell: (s) =>
              s.source === "group" ? (
                <button type="button" className="btn btn-sm btn-ghost" onClick={() => openEditFromEffective(s)}>edit</button>
              ) : (
                <span className="flex gap-0.5">
                  <button type="button" className="btn btn-sm btn-ghost" onClick={() => openEditFromEffective(s)}>edit</button>
                  <button type="button" className="btn btn-sm btn-ghost text-danger" disabled={deleteMutation.isPending} onClick={() => setDeleting(s.service_name)}>delete</button>
                </span>
              ),
          },
        ]}
        rows={rows}
        keyOf={(s) => `${s.source}-${s.source_id}-${s.service_name}`}
        loading={isLoading}
        empty="No services configured. Add a host override or assign service rules to a group."
      />

      {dialogOpen && (
        <Modal
          title={mode === "edit" ? "Edit service override" : originalAttempted ? `Add service override${form.name ? `: ${form.name}` : ""}` : "Add service"}
          w={620}
          onClose={() => setDialogOpen(false)}
          onSubmit={onSubmit}
          footer={
            <>
              <span className="tt mr-auto">desired state only — nothing applies until a sync runs</span>
              <button type="button" className="btn" onClick={() => setDialogOpen(false)}>Cancel</button>
              <button type="submit" className="btn btn-primary" disabled={saveMutation.isPending}>{saveMutation.isPending ? "Saving…" : "Save"}</button>
            </>
          }
        >
          <Field label="service name" htmlFor="svc-name">
            <input id="svc-name" className="inp mono" placeholder="e.g. nginx, sshd, docker" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} disabled={mode === "edit" || originalAttempted} required />
          </Field>

          {originalAttempted && (
            <Field as="div" label="current on-disk unit file (systemctl cat)">
              {originalLoading ? (
                <p className="text-[11px] text-text-faint">Fetching the current unit file from the host…</p>
              ) : typeof originalUnit === "string" ? (
                <pre className="mono max-h-40 overflow-y-auto rounded-r border border-line bg-bg p-2.5 text-[11px] text-text-2">{originalUnit}</pre>
              ) : (
                <Banner tone="warn">Could not fetch the on-disk unit file. Check SSH connectivity or whether the service exists on the target.</Banner>
              )}
            </Field>
          )}

          <Field label="unit file content" htmlFor="svc-unit-content" hint={mode === "add" ? "override existing, or a full new unit" : undefined}>
            <textarea
              id="svc-unit-content"
              className="inp mono"
              rows={8}
              placeholder={form.deployMode === "full" ? "[Unit]\nDescription=My Service\n\n[Service]\nExecStart=/usr/bin/myapp\nRestart=always\n\n[Install]\nWantedBy=multi-user.target" : "[Service]\nMemoryLimit=512M"}
              value={form.unitContent}
              onChange={(e) => setForm((f) => ({ ...f, unitContent: e.target.value }))}
            />
          </Field>

          <div className="grid gap-[11px] grid-cols-1 sm:grid-cols-[1fr_1fr]">
            <Field label="state" htmlFor="svc-state">
              <select id="svc-state" className="inp" value={form.state} onChange={(e) => setForm((f) => ({ ...f, state: e.target.value as "running" | "stopped" }))}>
                <option value="running">running</option>
                <option value="stopped">stopped</option>
              </select>
            </Field>
            <Field as="div" label="deploy mode">
              <label className="flex items-center gap-2 text-xs text-text-2">
                <input type="radio" checked={form.deployMode === "override"} onChange={() => setForm((f) => ({ ...f, deployMode: "override" }))} disabled={mode === "edit"} /> override existing
              </label>
              <label className="flex items-center gap-2 text-xs text-text-2">
                <input type="radio" checked={form.deployMode === "full"} onChange={() => setForm((f) => ({ ...f, deployMode: "full" }))} disabled={mode === "edit"} /> new service (full file)
              </label>
            </Field>
          </div>

          <label className="flex items-center gap-2 text-xs text-text">
            <input type="checkbox" checked={form.enabled} onChange={(e) => setForm((f) => ({ ...f, enabled: e.target.checked }))} /> enabled at boot
          </label>

          {saveMutation.error && <Banner tone="danger">{saveMutation.error.message}</Banner>}
        </Modal>
      )}

      {deleting && (
        <Confirm
          open
          onOpenChange={(open) => !open && setDeleting(null)}
          title="Delete service override"
          description={`${deleting} is no longer overridden on this host; it reverts to whatever its groups declare. This cannot be undone.`}
          confirmLabel="Delete"
          variant="destructive"
          loading={deleteMutation.isPending}
          onConfirm={() => handleDelete(deleting)}
        />
      )}

      <ServiceInventory hostId={hostId} overrides={overrides} onDelete={handleDelete} deleting={deleteMutation.isPending} />

      <CurrentStateSection moduleType="service" modules={currentState} hostId={hostId} />
    </div>
  )
}

/** Live systemd inventory, fetched over SSH on demand — separate from the
 *  desired-state table above; start/stop/restart act on the host directly. */
function ServiceInventory({
  hostId,
  overrides,
  onDelete,
  deleting,
}: {
  hostId: number
  overrides: ServiceRule[] | undefined
  onDelete: (serviceName: string) => void
  deleting: boolean
}) {
  const [loaded, setLoaded] = useState(false)
  const [loading, setLoading] = useState(false)
  const [inventory, setInventory] = useState<LiveService[]>([])
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState("")
  const [hideSystem, setHideSystem] = useState(true)
  const [hideManaged, setHideManaged] = useState(true)
  const [pending, setPending] = useState<{ service: string; action: string } | null>(null)
  const [actionLoading, setActionLoading] = useState(false)
  const [result, setResult] = useState<{ success: boolean; message: string } | null>(null)
  const [confirmTarget, setConfirmTarget] = useState<{ service: string; action: string } | null>(null)
  const [protectedTarget, setProtectedTarget] = useState<{ service: string; action: string } | null>(null)

  async function load() {
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      setInventory(await apiFetch<LiveService[]>(`/api/services/hosts/${hostId}/inventory`))
      setLoaded(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load inventory")
    } finally {
      setLoading(false)
    }
  }

  async function execute(serviceName: string, action: string) {
    setActionLoading(true)
    setResult(null)
    setPending({ service: serviceName, action })
    try {
      const r = await apiFetch<ServiceCommandResult>(`/api/services/hosts/${hostId}/command`, { method: "POST", body: JSON.stringify({ service_name: serviceName, action }) })
      if (r.success) {
        setResult({ success: true, message: `${action} ${serviceName}: success` })
        await load()
      } else {
        setResult({ success: false, message: `${action} ${serviceName} failed: ${r.stderr}` })
      }
    } catch (err) {
      setResult({ success: false, message: err instanceof Error ? err.message : "Command failed" })
    } finally {
      setActionLoading(false)
      setPending(null)
    }
  }

  function handleActionClick(service: LiveService, action: string) {
    if (service.is_protected) setProtectedTarget({ service: service.unit, action })
    else setConfirmTarget({ service: service.unit, action })
  }

  const filtered = inventory.filter(
    (svc) =>
      (!hideSystem || !svc.is_system) &&
      (!hideManaged || !svc.is_managed) &&
      (svc.unit.toLowerCase().includes(filter.toLowerCase()) || svc.description.toLowerCase().includes(filter.toLowerCase())),
  )

  return (
    <div className="flex min-h-0 flex-1 flex-col border-t border-line">
      <Toolbar actions={<button type="button" className="btn btn-sm" disabled={loading} onClick={load}>{loading ? "loading…" : loaded ? "refresh" : "load inventory"}</button>}>
        <span className="tt">live service inventory — fetched via SSH on demand</span>
      </Toolbar>

      {result && (
        <Banner tone={result.success ? "ok" : "danger"} flush action={<button type="button" className="btn btn-sm btn-ghost" onClick={() => setResult(null)}>dismiss</button>}>
          {result.message}
        </Banner>
      )}
      {error && <Banner tone="danger" flush>{error}</Banner>}

      {loaded && (
        <>
          <div className="flex flex-wrap items-center gap-2.5 border-b border-line bg-surface-2 px-[13px] py-[9px]">
            <input className="inp mono" style={{ width: 220 }} placeholder="filter services…" value={filter} onChange={(e) => setFilter(e.target.value)} />
            <label className="flex items-center gap-1.5 text-[11.5px] text-text-2">
              <input type="checkbox" checked={hideSystem} onChange={(e) => setHideSystem(e.target.checked)} /> hide system services
            </label>
            <label className="flex items-center gap-1.5 text-[11.5px] text-text-2">
              <input type="checkbox" checked={hideManaged} onChange={(e) => setHideManaged(e.target.checked)} /> hide managed
            </label>
            <span className="tt ml-auto text-text-faint">{filtered.length} of {inventory.length}</span>
          </div>
          <Table<LiveService>
            cols={[
              { k: "unit", label: "unit", w: "minmax(180px,1.2fr)", sortable: false, cell: (s) => <span className="mono font-medium text-text">{s.unit}{s.is_managed && <span className="ml-1.5"><Tag>managed</Tag></span>}</span> },
              { k: "active", label: "active", w: "100px", sortable: false, cell: (s) => <Tag tone={def(SYSTEMD_STATE, s.active_state).tone}>{s.active_state}</Tag> },
              { k: "sub", label: "sub state", w: "100px", sortable: false, cell: (s) => <span className="text-text-3">{s.sub_state}</span> },
              { k: "description", label: "description", w: "minmax(160px,1.6fr)", sortable: false, cell: (s) => <span className="trunc text-text-3">{s.description}</span> },
              {
                k: "actions", label: "", w: "220px", right: true, sortable: false,
                cell: (svc) => {
                  const override = overrides?.find((o) => o.service_name === svc.unit || o.service_name === svc.unit.replace(/\.service$/, ""))
                  return (
                    <span className="flex flex-wrap justify-end gap-0.5">
                      {(["start", "stop", "restart"] as const).map((action) => (
                        <button key={action} type="button" className="btn btn-sm btn-ghost" disabled={actionLoading && pending?.service === svc.unit} onClick={() => handleActionClick(svc, action)}>
                          {actionLoading && pending?.service === svc.unit && pending?.action === action ? "…" : action}
                        </button>
                      ))}
                      {svc.is_managed && !svc.is_protected && override && (
                        <button type="button" className="btn btn-sm btn-ghost text-danger" disabled={deleting} onClick={() => onDelete(svc.unit)}>remove</button>
                      )}
                    </span>
                  )
                },
              },
            ]}
            rows={filtered}
            keyOf={(s) => s.unit}
            empty="No services found."
          />
        </>
      )}

      {confirmTarget && (
        <Confirm
          open
          onOpenChange={(open) => !open && setConfirmTarget(null)}
          title={`${confirmTarget.action.charAt(0).toUpperCase()}${confirmTarget.action.slice(1)} service`}
          description={`${confirmTarget.action} ${confirmTarget.service}?`}
          confirmLabel={confirmTarget.action.charAt(0).toUpperCase() + confirmTarget.action.slice(1)}
          variant={confirmTarget.action === "stop" || confirmTarget.action === "restart" ? "destructive" : "default"}
          onConfirm={async () => {
            const t = confirmTarget
            setConfirmTarget(null)
            if (t) await execute(t.service, t.action)
          }}
        />
      )}

      {protectedTarget && (
        <Confirm
          open
          onOpenChange={(open) => !open && setProtectedTarget(null)}
          title="Protected service"
          description={`${protectedTarget.service} is a protected system service. ${protectedTarget.action} could cause system instability or loss of access. Proceed?`}
          confirmLabel={`Confirm ${protectedTarget.action}`}
          variant="destructive"
          onConfirm={async () => {
            const t = protectedTarget
            setProtectedTarget(null)
            if (t) await execute(t.service, t.action)
          }}
        />
      )}
    </div>
  )
}
