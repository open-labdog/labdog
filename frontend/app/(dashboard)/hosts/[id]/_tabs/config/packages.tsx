"use client"

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import Link from "next/link"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { plural } from "@/lib/fleet"
import { def, ITEM_STATE, PACKAGE_STATE } from "@/lib/status"
import { Banner, Confirm, Field, Modal, Provenance, Tag, Table, Toolbar } from "@/components/ld"
import type { EffectivePackage, HostGroup, ModuleCurrentState, PackageRepository, PackageRule } from "@/lib/types"
import { CurrentStateSection } from "./shared"

const defaults = { name: "", version: "", state: "present" as "present" | "absent" | "latest", manager: "auto" as "auto" | "apt" | "dnf" | "yum", comment: "", hold: false }

export function PackagesTab({
  hostId,
  currentState,
  syncDisabled,
  onSync,
}: {
  hostId: number
  currentState: ModuleCurrentState[] | undefined
  syncDisabled: boolean
  onSync: () => void
}) {
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [form, setForm] = useState(defaults)
  const [deleting, setDeleting] = useState<string | null>(null)

  const { data: packages, isLoading, error } = useQuery<EffectivePackage[]>({
    queryKey: ["host-effective-packages", hostId],
    queryFn: () => apiFetch<EffectivePackage[]>(`/api/hosts/${hostId}/effective-packages`),
  })
  const { data: overrides } = useQuery<PackageRule[]>({
    queryKey: ["host-package-overrides", hostId],
    queryFn: () => apiFetch<PackageRule[]>(`/api/hosts/${hostId}/packages`),
  })
  const { data: repos } = useQuery<PackageRepository[]>({
    queryKey: ["host-effective-repos", hostId],
    queryFn: () => apiFetch<PackageRepository[]>(`/api/hosts/${hostId}/effective-repos`),
  })
  const { data: groups } = useQuery<HostGroup[]>({ queryKey: ["groups"], queryFn: () => apiFetch<HostGroup[]>("/api/groups") })

  const saveMutation = useApiMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      editingId != null
        ? apiFetch(`/api/hosts/${hostId}/packages/${editingId}`, { method: "PUT", body: JSON.stringify(payload) })
        : apiFetch(`/api/hosts/${hostId}/packages`, { method: "POST", body: JSON.stringify(payload) }),
    invalidateKeys: [["host-effective-packages", hostId], ["host-package-overrides", hostId]],
    onSuccess: () => setDialogOpen(false),
  })
  const deleteMutation = useApiMutation({
    mutationFn: (overrideId: number) => apiFetch(`/api/hosts/${hostId}/packages/${overrideId}`, { method: "DELETE" }),
    invalidateKeys: [["host-effective-packages", hostId], ["host-package-overrides", hostId]],
    onSuccess: () => setDeleting(null),
  })

  function openCreate() {
    setEditingId(null)
    setForm(defaults)
    saveMutation.reset()
    setDialogOpen(true)
  }
  function openEdit(pkg: EffectivePackage) {
    const override = pkg.source === "host" ? overrides?.find((o) => o.package_name === pkg.package_name) : null
    setForm({ name: pkg.package_name, version: pkg.version ?? "", state: pkg.state, manager: pkg.package_manager, comment: override?.comment ?? "", hold: pkg.hold })
    setEditingId(override?.id ?? null)
    saveMutation.reset()
    setDialogOpen(true)
  }
  function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    saveMutation.mutate({ package_name: form.name, version: form.version || null, state: form.state, package_manager: form.manager, comment: form.comment || null, hold: form.hold })
  }
  function handleDelete(packageName: string) {
    const o = overrides?.find((x) => x.package_name === packageName)
    if (o) deleteMutation.mutate(o.id)
  }

  const packageErrors: Record<string, string> = {}
  const packageModule = currentState?.find((m) => m.module_type === "package")
  if (packageModule?.error_message) {
    for (const part of packageModule.error_message.split("; ")) {
      const idx = part.indexOf(": ")
      if (idx > 0) packageErrors[part.slice(0, idx)] = part.slice(idx + 2)
    }
  }

  const rows = packages ?? []

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Toolbar
        actions={
          <>
            <button type="button" className="btn btn-sm btn-ghost" disabled={syncDisabled} onClick={onSync}>sync</button>
            <button type="button" className="btn btn-sm btn-primary" onClick={openCreate}>add override</button>
          </>
        }
      >
        <span className="tt">{plural(rows.length, "package")} effective</span>
      </Toolbar>

      {error && <Banner tone="danger" flush>Could not load packages: {error.message}</Banner>}
      {deleteMutation.error && <Banner tone="danger" flush>{deleteMutation.error.message}</Banner>}

      <Table<EffectivePackage>
        cols={[
          { k: "name", label: "package", w: "minmax(150px,1fr)", sortable: false, cell: (p) => (
            <div>
              <span className={`mono font-medium ${packageErrors[p.package_name] ? "text-danger" : "text-text"}`}>{p.package_name}</span>
              {packageErrors[p.package_name] && <div className="mt-0.5 text-[10.5px] text-danger">{packageErrors[p.package_name]}</div>}
            </div>
          ) },
          { k: "version", label: "version", w: "100px", sortable: false, cell: (p) => <span className="mono text-[11px] text-text-3">{p.version ?? "any"}</span> },
          { k: "state", label: "state", w: "80px", sortable: false, cell: (p) => <Tag tone={def(PACKAGE_STATE, p.state).tone}>{p.state}</Tag> },
          { k: "manager", label: "manager", w: "90px", sortable: false, cell: (p) => <Tag>{p.package_manager}</Tag> },
          { k: "hold", label: "hold", w: "70px", sortable: false, cell: (p) => (p.hold ? <Tag tone="warn">held</Tag> : <span className="text-text-faint">—</span>) },
          { k: "origin", label: "comes from", w: "minmax(110px,1fr)", sortable: false, cell: (p) => <Provenance origin={p.source as "group" | "host"} label={p.source === "host" ? "this host" : p.source_name} /> },
          {
            k: "actions", label: "", w: "108px", right: true, sortable: false,
            cell: (p) =>
              p.source === "group" ? (
                <button type="button" className="btn btn-sm btn-ghost" onClick={() => openEdit(p)}>edit</button>
              ) : (
                <span className="flex gap-0.5">
                  <button type="button" className="btn btn-sm btn-ghost" onClick={() => openEdit(p)}>edit</button>
                  <button type="button" className="btn btn-sm btn-ghost text-danger" disabled={deleteMutation.isPending} onClick={() => setDeleting(p.package_name)}>delete</button>
                </span>
              ),
          },
        ]}
        rows={rows}
        keyOf={(p) => `${p.source}-${p.source_id}-${p.package_name}`}
        loading={isLoading}
        empty="No packages configured. Add a host override or assign packages to a group."
      />

      <div className="tt border-t border-line bg-surface-2 px-[13px] py-1.5">effective repositories — managed at the group level</div>
      <Table<PackageRepository>
        cols={[
          { k: "name", label: "name", w: "minmax(130px,1fr)", sortable: false, cell: (r) => <span className="font-medium text-text">{r.name}</span> },
          { k: "url", label: "url", w: "minmax(160px,1.6fr)", sortable: false, cell: (r) => <span className="mono trunc text-[11px] text-text-3">{r.url}</span> },
          { k: "type", label: "type", w: "70px", sortable: false, cell: (r) => <Tag>{r.repo_type}</Tag> },
          { k: "dist", label: "distribution", w: "110px", sortable: false, cell: (r) => <span className="mono text-[11px] text-text-3">{r.distribution ?? "—"}</span> },
          { k: "state", label: "state", w: "80px", sortable: false, cell: (r) => <Tag tone={def(ITEM_STATE, r.state).tone}>{r.state}</Tag> },
          { k: "group", label: "group", w: "minmax(110px,1fr)", sortable: false, cell: (r) => <Link href={`/groups/${r.group_id}`} className="tt text-ld-accent hover:no-underline">{groups?.find((g) => g.id === r.group_id)?.name ?? r.group_id} →</Link> },
        ]}
        rows={repos ?? []}
        keyOf={(r) => r.id}
        empty="No repositories configured. Add repositories at the group level."
      />

      {dialogOpen && (
        <Modal
          title={editingId != null ? "Edit package override" : "Add package override"}
          w={520}
          onClose={() => setDialogOpen(false)}
          onSubmit={onSubmit}
          footer={
            <>
              <button type="button" className="btn ml-auto" onClick={() => setDialogOpen(false)}>Cancel</button>
              <button type="submit" className="btn btn-primary" disabled={saveMutation.isPending}>{saveMutation.isPending ? "Saving…" : editingId != null ? "Save changes" : "Create override"}</button>
            </>
          }
        >
          <div className="grid gap-[11px] grid-cols-1 sm:grid-cols-[1fr_120px]">
            <Field label="package name" htmlFor="pp-name">
              <input id="pp-name" className="inp mono" placeholder="e.g. nginx, curl, htop" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} required />
            </Field>
            <Field label="version" htmlFor="pp-version" hint="any if empty">
              <input id="pp-version" className="inp mono" value={form.version} onChange={(e) => setForm((f) => ({ ...f, version: e.target.value }))} />
            </Field>
          </div>
          <div className="grid gap-[11px] grid-cols-1 sm:grid-cols-[1fr_1fr]">
            <Field label="state" htmlFor="pp-state">
              <select id="pp-state" className="inp" value={form.state} onChange={(e) => setForm((f) => ({ ...f, state: e.target.value as "present" | "absent" | "latest" }))}>
                <option value="present">present</option>
                <option value="absent">absent</option>
                <option value="latest">latest</option>
              </select>
            </Field>
            <Field label="package manager" htmlFor="pp-manager">
              <select id="pp-manager" className="inp" value={form.manager} onChange={(e) => setForm((f) => ({ ...f, manager: e.target.value as "auto" | "apt" | "dnf" | "yum" }))}>
                <option value="auto">auto-detect</option>
                <option value="apt">apt</option>
                <option value="dnf">dnf</option>
                <option value="yum">yum</option>
              </select>
            </Field>
          </div>
          <Field label="comment" htmlFor="pp-comment" hint="optional">
            <input id="pp-comment" className="inp" value={form.comment} onChange={(e) => setForm((f) => ({ ...f, comment: e.target.value }))} />
          </Field>
          <label className="flex items-center gap-2 text-xs text-text">
            <input type="checkbox" checked={form.hold} onChange={(e) => setForm((f) => ({ ...f, hold: e.target.checked }))} /> hold package
            <span className="text-text-faint">— prevent automatic upgrades</span>
          </label>

          {saveMutation.error && <Banner tone="danger">{saveMutation.error.message}</Banner>}
        </Modal>
      )}

      {deleting && (
        <Confirm
          open
          onOpenChange={(open) => !open && setDeleting(null)}
          title="Delete package override"
          description={`Delete the override for "${deleting}"? This cannot be undone.`}
          confirmLabel="Delete"
          variant="destructive"
          loading={deleteMutation.isPending}
          onConfirm={() => handleDelete(deleting)}
        />
      )}

      <CurrentStateSection moduleType="package" modules={currentState} hostId={hostId} />
    </div>
  )
}
