"use client"

import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { useDelayedLoading } from "@/lib/utils"
import { showSuccess, showError } from "@/lib/toast"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import { TableSkeleton } from "@/components/ui/skeleton"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { DataTable } from "@/components/ui/data-table"
import type { GrafanaInstance } from "@/lib/types"

interface FormState {
  name: string
  prometheus_query_url: string
  prometheus_push_url: string
  loki_push_url: string
  org_id: string
  token: string
  verify_ssl: boolean
  ca_cert_pem: string
  ca_cert_clear: boolean
  is_default: boolean
}

const emptyForm: FormState = {
  name: "",
  prometheus_query_url: "",
  prometheus_push_url: "",
  loki_push_url: "",
  org_id: "",
  token: "",
  verify_ssl: true,
  ca_cert_pem: "",
  ca_cert_clear: false,
  is_default: false,
}

type TestResult = { success: boolean; message: string }

export default function GrafanaPage() {
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<GrafanaInstance | null>(null)
  const [form, setForm] = useState<FormState>(emptyForm)
  const [formError, setFormError] = useState<string | null>(null)
  const [formSaving, setFormSaving] = useState(false)
  const [draftTesting, setDraftTesting] = useState(false)
  const [draftTestResult, setDraftTestResult] = useState<TestResult | null>(null)
  const [testingId, setTestingId] = useState<number | null>(null)
  const [confirmState, setConfirmState] = useState<{
    open: boolean
    title: string
    description: string
    action: () => void | Promise<void>
    loading?: boolean
  } | null>(null)

  const queryClient = useQueryClient()

  const { data: instances, isLoading, error } = useQuery<GrafanaInstance[]>({
    queryKey: ["grafana-instances"],
    queryFn: () => apiFetch<GrafanaInstance[]>("/api/grafana/instances"),
  })
  const showLoading = useDelayedLoading(isLoading)

  const deleteMutation = useApiMutation<unknown, number, GrafanaInstance>({
    mutationFn: (id) => apiFetch(`/api/grafana/instances/${id}`, { method: "DELETE" }),
    invalidateKeys: [["grafana-instances"]],
    successMessage: "Grafana instance deleted",
    optimisticUpdate: {
      queryKey: ["grafana-instances"],
      updater: (old, id) => old.filter((n) => n.id !== id),
    },
  })

  function openCreate() {
    setEditing(null)
    setForm(emptyForm)
    setFormError(null)
    setDraftTestResult(null)
    setDialogOpen(true)
  }

  function openEdit(inst: GrafanaInstance) {
    setEditing(inst)
    setForm({
      name: inst.name,
      prometheus_query_url: inst.prometheus_query_url,
      prometheus_push_url: inst.prometheus_push_url,
      loki_push_url: inst.loki_push_url ?? "",
      org_id: inst.org_id ?? "",
      token: "",
      verify_ssl: inst.verify_ssl,
      ca_cert_pem: "",
      ca_cert_clear: false,
      is_default: inst.is_default,
    })
    setFormError(null)
    setDraftTestResult(null)
    setDialogOpen(true)
  }

  // Shared payload builder for save + pre-save test.
  function buildPayload(forUpdate: boolean): Record<string, unknown> {
    const payload: Record<string, unknown> = {
      name: forUpdate ? form.name || undefined : form.name,
      prometheus_query_url: form.prometheus_query_url || undefined,
      prometheus_push_url: form.prometheus_push_url || undefined,
      loki_push_url: form.loki_push_url || (forUpdate ? "" : undefined),
      org_id: form.org_id || (forUpdate ? "" : undefined),
      verify_ssl: form.verify_ssl,
      is_default: form.is_default,
    }
    if (!forUpdate) {
      payload.prometheus_query_url = form.prometheus_query_url
      payload.prometheus_push_url = form.prometheus_push_url
    }
    if (form.token) payload.token = form.token
    else if (forUpdate && editing?.has_token === false) {
      /* nothing */
    }
    if (form.ca_cert_pem.trim()) payload.ca_cert_pem = form.ca_cert_pem
    else if (forUpdate && form.ca_cert_clear) payload.ca_cert_pem = ""
    return payload
  }

  async function handleDraftTest() {
    setDraftTesting(true)
    setDraftTestResult(null)
    try {
      const result = await apiFetch<TestResult>("/api/grafana/instances/test", {
        method: "POST",
        json: {
          name: form.name || "draft",
          prometheus_query_url: form.prometheus_query_url,
          prometheus_push_url: form.prometheus_push_url,
          loki_push_url: form.loki_push_url || undefined,
          org_id: form.org_id || undefined,
          token: form.token || undefined,
          verify_ssl: form.verify_ssl,
          ca_cert_pem: form.ca_cert_pem.trim() || undefined,
        },
      })
      setDraftTestResult(result)
    } catch (err) {
      setDraftTestResult({ success: false, message: err instanceof Error ? err.message : "Test failed" })
    } finally {
      setDraftTesting(false)
    }
  }

  async function handleSave() {
    setFormSaving(true)
    setFormError(null)
    try {
      if (editing) {
        await apiFetch(`/api/grafana/instances/${editing.id}`, {
          method: "PUT",
          json: buildPayload(true),
        })
        showSuccess("Grafana instance updated")
      } else {
        await apiFetch("/api/grafana/instances", { method: "POST", json: buildPayload(false) })
        showSuccess("Grafana instance created")
      }
      await queryClient.invalidateQueries({ queryKey: ["grafana-instances"] })
      setDialogOpen(false)
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to save")
    } finally {
      setFormSaving(false)
    }
  }

  function handleDelete(inst: GrafanaInstance) {
    setConfirmState({
      open: true,
      title: "Delete Grafana Instance",
      description: `Are you sure you want to delete "${inst.name}"? This action cannot be undone.`,
      action: async () => {
        setConfirmState((prev) => (prev ? { ...prev, loading: true } : null))
        try {
          await deleteMutation.mutateAsync(inst.id)
        } finally {
          setConfirmState(null)
        }
      },
    })
  }

  async function handleTestConnection(inst: GrafanaInstance) {
    setTestingId(inst.id)
    try {
      const result = await apiFetch<TestResult>(`/api/grafana/instances/${inst.id}/test`, {
        method: "POST",
      })
      if (result.success) showSuccess(result.message)
      else showError(`Connection failed: ${result.message}`)
    } catch (err) {
      showError(err instanceof Error ? err.message : "Test failed")
    } finally {
      setTestingId(null)
    }
  }

  return (
    <div className="space-y-6">
      <Breadcrumb items={[{ label: "Grafana" }]} />

      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Grafana Metrics</h1>
          <p className="text-slate-400 text-sm mt-1">
            Register a Mimir/Prometheus + Loki endpoint to show live host metrics and to
            feed the bundled Alloy install action.
          </p>
        </div>
        <Button onClick={openCreate}>Add Instance</Button>
      </div>

      {showLoading && <TableSkeleton rows={3} columns={5} />}

      {error && (
        <div className="text-red-400 py-8 text-center">Failed to load Grafana instances</div>
      )}

      {!isLoading && !error && (
        <DataTable<GrafanaInstance>
          tableId="grafana-instances"
          data={instances}
          emptyMessage={<>No Grafana instances configured. Click <strong>Add Instance</strong> to get started.</>}
          getRowKey={(n) => n.id}
          columns={[
            {
              key: "name",
              label: "Name",
              accessor: (n) => n.name,
              cell: (n) => (
                <span className="font-medium text-white">
                  {n.name}
                  {n.is_default && (
                    <span className="ml-2 rounded bg-sky-500/15 px-1.5 py-0.5 text-xs text-sky-300">
                      default
                    </span>
                  )}
                </span>
              ),
              defaultWidth: 180,
              filter: { type: "text" },
            },
            {
              key: "prometheus_query_url",
              label: "Query URL",
              accessor: (n) => n.prometheus_query_url,
              cell: (n) => <span className="font-mono text-slate-300 text-sm">{n.prometheus_query_url}</span>,
              defaultWidth: 260,
              filter: { type: "text" },
            },
            {
              key: "prometheus_push_url",
              label: "Push URL",
              accessor: (n) => n.prometheus_push_url,
              cell: (n) => <span className="font-mono text-slate-300 text-sm">{n.prometheus_push_url}</span>,
              defaultWidth: 240,
              filter: { type: "text" },
            },
            {
              key: "org_id",
              label: "Tenant",
              accessor: (n) => n.org_id ?? "",
              cell: (n) => n.org_id
                ? <span className="font-mono text-slate-300 text-sm">{n.org_id}</span>
                : <span className="text-slate-500 text-sm">—</span>,
              defaultWidth: 120,
              filter: { type: "text" },
            },
            {
              key: "verify_ssl",
              label: "TLS Verify",
              accessor: (n) => n.verify_ssl,
              cell: (n) => n.verify_ssl
                ? <span className="text-green-400 text-sm">Yes</span>
                : <span className="text-yellow-400 text-sm">No</span>,
              defaultWidth: 110,
              filter: { type: "boolean" },
            },
            {
              key: "actions",
              label: "Actions",
              cell: (inst) => (
                <div className="flex gap-1">
                  <Button size="sm" variant="ghost" disabled={testingId === inst.id} onClick={() => handleTestConnection(inst)}>
                    {testingId === inst.id ? "Testing..." : "Test"}
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => openEdit(inst)}>Edit</Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="text-red-400 hover:text-red-300 hover:bg-red-950"
                    onClick={() => handleDelete(inst)}
                    disabled={deleteMutation.isPending}
                  >
                    Delete
                  </Button>
                </div>
              ),
              defaultWidth: 220,
              resizable: false,
              sortable: false,
            },
          ]}
        />
      )}

      <Dialog
        open={dialogOpen}
        onOpenChange={(open) => {
          if (!open) {
            setDialogOpen(false)
            setFormError(null)
          }
        }}
      >
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{editing ? "Edit Grafana Instance" : "Add Grafana Instance"}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 mt-2 max-h-[70vh] overflow-y-auto pr-1">
            <div className="space-y-2">
              <Label htmlFor="g-name">Name</Label>
              <Input
                id="g-name"
                placeholder="e.g. homelab"
                value={form.name}
                onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="g-query">Mimir / Prometheus query URL</Label>
              <Input
                id="g-query"
                placeholder="http://mimir:9009/prometheus"
                value={form.prometheus_query_url}
                onChange={(e) => setForm((p) => ({ ...p, prometheus_query_url: e.target.value }))}
                className="font-mono"
              />
              <p className="text-xs text-slate-500">What LabDog queries for host metrics.</p>
            </div>

            <div className="space-y-2">
              <Label htmlFor="g-push">Prometheus remote-write URL</Label>
              <Input
                id="g-push"
                placeholder="http://mimir:9009/api/v1/push"
                value={form.prometheus_push_url}
                onChange={(e) => setForm((p) => ({ ...p, prometheus_push_url: e.target.value }))}
                className="font-mono"
              />
              <p className="text-xs text-slate-500">Handed to the Alloy install action so the agent ships here.</p>
            </div>

            <div className="space-y-2">
              <Label htmlFor="g-loki">Loki push URL (optional)</Label>
              <Input
                id="g-loki"
                placeholder="http://loki:3100/loki/api/v1/push"
                value={form.loki_push_url}
                onChange={(e) => setForm((p) => ({ ...p, loki_push_url: e.target.value }))}
                className="font-mono"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="g-org">Tenant / Org ID (optional)</Label>
              <Input
                id="g-org"
                placeholder="anonymous"
                value={form.org_id}
                onChange={(e) => setForm((p) => ({ ...p, org_id: e.target.value }))}
                className="font-mono"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="g-token">
                Bearer token{editing && " (leave blank to keep current)"}
              </Label>
              <Input
                id="g-token"
                type="password"
                placeholder={editing?.has_token ? "Leave blank to keep current" : "Optional"}
                value={form.token}
                onChange={(e) => setForm((p) => ({ ...p, token: e.target.value }))}
                className="font-mono"
              />
            </div>

            <div className="flex items-center gap-2">
              <input
                id="g-default"
                type="checkbox"
                checked={form.is_default}
                onChange={(e) => setForm((p) => ({ ...p, is_default: e.target.checked }))}
                className="rounded border-input"
              />
              <Label htmlFor="g-default">Default instance (queried for host metrics)</Label>
            </div>

            <div className="flex items-center gap-2">
              <input
                id="g-verify-ssl"
                type="checkbox"
                checked={form.verify_ssl}
                onChange={(e) => setForm((p) => ({ ...p, verify_ssl: e.target.checked }))}
                className="rounded border-input"
              />
              <Label htmlFor="g-verify-ssl">Verify TLS certificate</Label>
            </div>

            {form.verify_ssl && (
              <div className="space-y-2">
                <Label htmlFor="g-ca">CA certificate (PEM, optional)</Label>
                {editing?.has_ca_cert && !form.ca_cert_clear && (
                  <p className="text-sm text-slate-400">
                    CA configured — paste a new PEM to replace, or{" "}
                    <button
                      type="button"
                      className="text-red-400 hover:text-red-300 underline"
                      onClick={() => setForm((p) => ({ ...p, ca_cert_pem: "", ca_cert_clear: true }))}
                    >
                      Clear CA
                    </button>{" "}
                    to remove.
                  </p>
                )}
                {editing?.has_ca_cert && form.ca_cert_clear && (
                  <p className="text-sm text-yellow-400">
                    CA will be cleared on save.{" "}
                    <button
                      type="button"
                      className="text-slate-300 hover:text-white underline"
                      onClick={() => setForm((p) => ({ ...p, ca_cert_clear: false }))}
                    >
                      Undo
                    </button>
                  </p>
                )}
                <textarea
                  id="g-ca"
                  rows={5}
                  placeholder={"-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----"}
                  value={form.ca_cert_pem}
                  onChange={(e) =>
                    setForm((p) => ({
                      ...p,
                      ca_cert_pem: e.target.value,
                      ca_cert_clear: e.target.value.trim() ? false : p.ca_cert_clear,
                    }))
                  }
                  className="w-full min-w-0 rounded-lg border border-input bg-transparent px-2.5 py-1.5 font-mono text-sm transition-colors outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30"
                />
              </div>
            )}

            <div className="flex items-center gap-3">
              <Button variant="outline" onClick={handleDraftTest} disabled={draftTesting || !form.prometheus_query_url}>
                {draftTesting ? "Testing..." : "Test connection"}
              </Button>
              {draftTestResult && (
                <span className={draftTestResult.success ? "text-sm text-green-400" : "text-sm text-red-400"}>
                  {draftTestResult.message}
                </span>
              )}
            </div>

            {formError && <p className="text-sm text-red-400">{formError}</p>}

            <DialogFooter>
              <Button variant="outline" onClick={() => { setDialogOpen(false); setFormError(null) }}>
                Cancel
              </Button>
              <Button onClick={handleSave} disabled={formSaving}>
                {formSaving ? "Saving..." : editing ? "Save Changes" : "Add Instance"}
              </Button>
            </DialogFooter>
          </div>
        </DialogContent>
      </Dialog>

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
