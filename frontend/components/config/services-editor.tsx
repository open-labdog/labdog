"use client"

import { useEffect, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { plural } from "@/lib/fleet"
import { SYSTEMD_STATE, def, enabledDef } from "@/lib/status"
import { serviceSchema, type ServiceInput } from "@/lib/schemas"
import type { ServiceRule } from "@/lib/types"
import { Banner, Confirm, Field, Modal, Seg, Table, Tag, Toolbar } from "@/components/ld"
import { GROUP_KEYS, GitOpsBanner, stop, useEditorGroup } from "./shared"

const defaults: ServiceInput = { service_name: "", state: "running", enabled: true, unit_content: "", deploy_mode: "override", priority: 100, comment: "" }

/**
 * systemd units a group declares: the state and boot enablement each
 * should have, and optionally a unit file (a full one, or a drop-in
 * override applied only where the unit already exists). Embedded in the
 * group page's Config tab.
 */
export function ServicesEditor({ groupId }: { groupId: number }) {
  const { group, gitops } = useEditorGroup(groupId)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<ServiceRule | null>(null)
  const [deleting, setDeleting] = useState<ServiceRule | null>(null)

  const form = useForm<ServiceInput>({ resolver: zodResolver(serviceSchema), defaultValues: defaults, mode: "onSubmit" })

  const { data: services, isLoading, error } = useQuery<ServiceRule[]>({
    queryKey: ["services", groupId],
    queryFn: () => apiFetch<ServiceRule[]>(`/api/groups/${groupId}/services`),
    enabled: !!groupId,
  })

  const saveMutation = useApiMutation({
    mutationFn: ({ serviceId, payload }: { serviceId?: number; payload: Record<string, unknown> }) =>
      serviceId
        ? apiFetch(`/api/groups/${groupId}/services/${serviceId}`, { method: "PUT", body: JSON.stringify(payload) })
        : apiFetch(`/api/groups/${groupId}/services`, { method: "POST", body: JSON.stringify(payload) }),
    invalidateKeys: [["services", groupId], ...GROUP_KEYS],
    onSuccess: () => setDialogOpen(false),
  })

  const deleteMutation = useApiMutation({
    mutationFn: (serviceId: number) => apiFetch(`/api/groups/${groupId}/services/${serviceId}`, { method: "DELETE" }),
    invalidateKeys: [["services", groupId], ...GROUP_KEYS],
    onSuccess: () => setDeleting(null),
  })

  function openCreate() {
    setEditing(null)
    form.reset(defaults)
    saveMutation.reset()
    setDialogOpen(true)
  }

  function openEdit(service: ServiceRule) {
    setEditing(service)
    saveMutation.reset()
    setDialogOpen(true)
  }

  useEffect(() => {
    if (dialogOpen && editing) {
      form.reset({
        service_name: editing.service_name,
        state: editing.state,
        enabled: editing.enabled,
        unit_content: editing.unit_content ?? "",
        deploy_mode: (editing.deploy_mode as "full" | "override") ?? "override",
        priority: editing.priority,
        comment: editing.comment ?? "",
      })
    }
  }, [dialogOpen, editing, form])

  const onSubmit = form.handleSubmit((data) => {
    saveMutation.mutate({ serviceId: editing?.id, payload: { ...data, comment: data.comment || null, unit_content: data.unit_content || null } })
  })

  const rows = services ?? []
  const mode = form.watch("deploy_mode")

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Toolbar
        actions={
          !gitops && (
            <button type="button" className="btn btn-sm btn-primary" onClick={openCreate}>
              Add service
            </button>
          )
        }
      >
        <span className="tt">{plural(rows.length, "unit")} declared here</span>
      </Toolbar>
      {gitops && <GitOpsBanner group={group} what="services are" />}
      {error && (
        <Banner tone="danger" flush>
          Could not load services: {error.message}
        </Banner>
      )}

      <Table<ServiceRule>
        cols={[
          { k: "name", label: "unit", w: "minmax(160px,1.2fr)", sortable: false, cell: (s) => <span className="mono font-medium text-text">{s.service_name}</span> },
          { k: "state", label: "state", w: "96px", sortable: false, cell: (s) => <Tag tone={def(SYSTEMD_STATE, s.state).tone}>{s.state}</Tag> },
          { k: "enabled", label: "at boot", w: "96px", sortable: false, cell: (s) => <Tag tone={enabledDef(s.enabled).tone}>{enabledDef(s.enabled).label}</Tag> },
          { k: "mode", label: "unit file", w: "110px", sortable: false, cell: (s) => (s.unit_content ? <span className="text-text-2">{s.deploy_mode === "full" ? "full file" : "drop-in"}</span> : <span className="text-text-faint">—</span>) },
          { k: "priority", label: "priority", w: "70px", right: true, sortable: false, cell: (s) => <span className="mono num">{s.priority}</span> },
          { k: "comment", label: "comment", w: "minmax(140px,1fr)", sortable: false, cell: (s) => <span className="text-[11.5px] text-text-3">{s.comment ?? ""}</span> },
          {
            k: "actions",
            label: "",
            w: "118px",
            right: true,
            sortable: false,
            cell: (s) => (
              <span className="flex gap-0.5" onClick={stop}>
                <button type="button" className="btn btn-sm btn-ghost" disabled={gitops} title={gitops ? "managed via GitOps" : undefined} onClick={() => openEdit(s)}>
                  edit
                </button>
                <button type="button" className="btn btn-sm btn-ghost text-danger" disabled={gitops || deleteMutation.isPending} title={gitops ? "managed via GitOps" : undefined} onClick={() => setDeleting(s)}>
                  delete
                </button>
              </span>
            ),
          },
        ]}
        rows={rows}
        keyOf={(s) => s.id}
        onRowClick={gitops ? undefined : openEdit}
        loading={isLoading}
        empty="Nothing declared. Add service declares this module for the group; the state is applied on the next plan."
      />

      {dialogOpen && (
        <Modal
          title={editing ? "Edit service" : "Add service"}
          meta={group ? `group: ${group.name}` : undefined}
          w={560}
          onClose={() => setDialogOpen(false)}
          onSubmit={onSubmit}
          footer={
            <>
              <span className="tt mr-auto">desired state only — nothing applies until a plan runs</span>
              <button type="button" className="btn" onClick={() => setDialogOpen(false)}>
                Cancel
              </button>
              <button type="submit" className="btn btn-primary" disabled={saveMutation.isPending}>
                {saveMutation.isPending ? "Saving…" : editing ? "Save changes" : "Add service"}
              </button>
            </>
          }
        >
          <div className="grid gap-[11px] grid-cols-1 sm:grid-cols-[1fr_120px_100px]">
            <Field label="unit" htmlFor="service-name" error={form.formState.errors.service_name?.message}>
              <input id="service-name" className="inp mono" placeholder="e.g. nginx, sshd, docker" {...form.register("service_name")} />
            </Field>
            <Field label="state" htmlFor="service-state">
              <select id="service-state" className="inp" {...form.register("state")}>
                <option value="running">running</option>
                <option value="stopped">stopped</option>
              </select>
            </Field>
            <Field label="priority" htmlFor="service-priority" error={form.formState.errors.priority?.message}>
              <input id="service-priority" type="number" min={0} className="inp mono num" {...form.register("priority", { valueAsNumber: true })} />
            </Field>
          </div>
          <label className="flex items-center gap-2 text-xs text-text">
            <input id="service-enabled" type="checkbox" {...form.register("enabled")} />
            enabled at boot
          </label>
          <Field as="div" label="unit file" hint={mode === "full" ? "deployed to /etc/systemd/system/<unit>.service on every host in the group" : "drop-in override — applied only on hosts where the unit already exists, skipped silently elsewhere"}>
            <Seg
              sm
              options={[
                { k: "override", label: "Override existing" },
                { k: "full", label: "New service (full file)" },
              ]}
              value={mode}
              onChange={(k) => form.setValue("deploy_mode", k as "full" | "override", { shouldDirty: true })}
            />
            <textarea
              id="service-unit-content"
              className="inp mono"
              rows={8}
              spellCheck={false}
              placeholder={mode === "full" ? "[Unit]\nDescription=My Service\n\n[Service]\nExecStart=/usr/bin/myapp\nRestart=always\n\n[Install]\nWantedBy=multi-user.target" : "[Service]\nMemoryLimit=512M"}
              {...form.register("unit_content")}
            />
          </Field>
          <Field label="comment" htmlFor="service-comment" hint="optional">
            <input id="service-comment" className="inp" {...form.register("comment")} />
          </Field>
          {saveMutation.error && <Banner tone="danger">{saveMutation.error.message}</Banner>}
        </Modal>
      )}

      {deleting && (
        <Confirm
          open
          onOpenChange={(open) => !open && setDeleting(null)}
          title="Delete service rule"
          description={`${deleting.service_name} is no longer declared by this group; hosts keep the unit as it is until a plan runs. This cannot be undone.`}
          confirmLabel="Delete"
          variant="destructive"
          loading={deleteMutation.isPending}
          onConfirm={() => deleteMutation.mutate(deleting.id)}
        />
      )}
    </div>
  )
}
