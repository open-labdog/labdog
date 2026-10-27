"use client"

import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { showSuccess, showError } from "@/lib/toast"
import { Banner, Confirm, Field, Modal, PageHead, Panel, Table, Tag } from "@/components/ld"
import { MetricsScrapeCard } from "@/components/metrics-scrape-card"
import type { GrafanaInstance, GrafanaKind, GrafanaAuthType } from "@/lib/types"

const CRUMBS = [
  { label: "settings", href: "/settings" },
  { label: "integrations", href: "/settings" },
]

interface FormState {
  name: string
  kind: GrafanaKind
  url: string
  org_id: string
  auth_type: GrafanaAuthType
  username: string
  token: string
  verify_ssl: boolean
  ca_cert_pem: string
  ca_cert_clear: boolean
  is_default: boolean
}

const emptyForm: FormState = {
  name: "",
  kind: "mimir",
  url: "",
  org_id: "",
  auth_type: "none",
  username: "",
  token: "",
  verify_ssl: false,
  ca_cert_pem: "",
  ca_cert_clear: false,
  is_default: false,
}

const URL_PLACEHOLDER: Record<GrafanaKind, string> = {
  mimir: "https://mimir.example.com/api/v1/push",
  loki: "https://loki.example.com/loki/api/v1/push",
}

type TestResult = { success: boolean; message: string }

/**
 * Grafana — the Mimir (metrics) and Loki (logs) endpoints LabDog reads
 * host state from and hands to the Alloy install action, plus the
 * scrape endpoint LabDog itself exposes.
 */
export default function GrafanaPage() {
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<GrafanaInstance | null>(null)
  const [form, setForm] = useState<FormState>(emptyForm)
  const [formError, setFormError] = useState<string | null>(null)
  const [formSaving, setFormSaving] = useState(false)
  const [draftTesting, setDraftTesting] = useState(false)
  const [draftTestResult, setDraftTestResult] = useState<TestResult | null>(null)
  const [testingId, setTestingId] = useState<number | null>(null)
  const [confirmState, setConfirmState] = useState<{ title: string; description: string; action: () => void | Promise<void>; loading?: boolean } | null>(null)

  const queryClient = useQueryClient()

  const { data: instances, isLoading, error } = useQuery<GrafanaInstance[]>({
    queryKey: ["grafana-instances"],
    queryFn: () => apiFetch<GrafanaInstance[]>("/api/grafana/instances"),
  })

  const deleteMutation = useApiMutation<unknown, number, GrafanaInstance>({
    mutationFn: (id) => apiFetch(`/api/grafana/instances/${id}`, { method: "DELETE" }),
    invalidateKeys: [["grafana-instances"]],
    successMessage: "Grafana instance deleted",
    optimisticUpdate: {
      queryKey: ["grafana-instances"],
      updater: (old, id) => old.filter((n) => n.id !== id),
    },
  })

  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => setForm((p) => ({ ...p, [k]: v }))

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
      kind: inst.kind,
      url: inst.url,
      org_id: inst.org_id ?? "",
      auth_type: inst.auth_type,
      username: inst.username ?? "",
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

  function closeDialog() {
    setDialogOpen(false)
    setFormError(null)
  }

  async function handleDraftTest() {
    setDraftTesting(true)
    setDraftTestResult(null)
    try {
      const result = await apiFetch<TestResult>("/api/grafana/instances/test", {
        method: "POST",
        json: {
          name: form.name || "draft",
          kind: form.kind,
          url: form.url,
          org_id: form.org_id || undefined,
          auth_type: form.auth_type,
          username: form.auth_type === "basic" ? form.username : undefined,
          token: form.auth_type !== "none" ? form.token || undefined : undefined,
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
        const payload: Record<string, unknown> = {
          name: form.name || undefined,
          kind: form.kind,
          url: form.url || undefined,
          org_id: form.org_id,
          auth_type: form.auth_type,
          username: form.auth_type === "basic" ? form.username : "",
          verify_ssl: form.verify_ssl,
          is_default: form.is_default,
        }
        if (form.token) payload.token = form.token
        if (form.ca_cert_pem.trim()) payload.ca_cert_pem = form.ca_cert_pem
        else if (form.ca_cert_clear) payload.ca_cert_pem = ""
        await apiFetch(`/api/grafana/instances/${editing.id}`, { method: "PUT", json: payload })
        showSuccess("Grafana instance updated")
      } else {
        const payload: Record<string, unknown> = {
          name: form.name,
          kind: form.kind,
          url: form.url,
          org_id: form.org_id || undefined,
          auth_type: form.auth_type,
          username: form.auth_type === "basic" ? form.username : undefined,
          verify_ssl: form.verify_ssl,
          is_default: form.is_default,
        }
        if (form.auth_type !== "none" && form.token) payload.token = form.token
        if (form.ca_cert_pem.trim()) payload.ca_cert_pem = form.ca_cert_pem
        await apiFetch("/api/grafana/instances", { method: "POST", json: payload })
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
      title: "Delete Grafana instance",
      description: `${inst.name} stops serving host ${inst.kind === "loki" ? "logs" : "metrics"}; actions that pushed to it keep their configuration on the hosts. This cannot be undone.`,
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
      const result = await apiFetch<TestResult>(`/api/grafana/instances/${inst.id}/test`, { method: "POST" })
      if (result.success) showSuccess(result.message)
      else showError(`Connection failed: ${result.message}`)
    } catch (err) {
      showError(err instanceof Error ? err.message : "Test failed")
    } finally {
      setTestingId(null)
    }
  }

  const all = instances ?? []

  return (
    <>
      <PageHead
        crumbs={CRUMBS}
        title={
          <>
            Grafana <span className="mono num text-[12.5px] font-normal text-text-faint">{all.length}</span>
          </>
        }
        sub="Register Mimir (metrics) and Loki (logs) endpoints separately: one ingest URL each. LabDog hands it to the Alloy install action and derives the query URL from it."
        actions={
          <button type="button" className="btn btn-sm btn-primary" onClick={openCreate}>
            Add instance…
          </button>
        }
      />

      {error && (
        <Banner tone="danger" flush>
          Could not load Grafana instances: {error.message}
        </Banner>
      )}

      <div className="scroll flex flex-1 flex-col gap-3 p-3.5">
        <Panel title="metrics in" meta="query backends · host cpu, memory, disk and logs">
          <Table<GrafanaInstance>
            cols={[
              {
                k: "name",
                label: "name",
                w: "minmax(140px,1fr)",
                sortable: false,
                cell: (n) => (
                  <span className="flex items-center gap-1.5">
                    <span className="mono trunc font-medium text-text">{n.name}</span>
                    {n.is_default && <Tag tone="accent">default</Tag>}
                  </span>
                ),
              },
              { k: "kind", label: "kind", w: "80px", sortable: false, cell: (n) => <Tag tone={n.kind === "loki" ? "hold" : "sync"}>{n.kind}</Tag> },
              { k: "url", label: "ingest url", w: "minmax(220px,1.8fr)", sortable: false, cell: (n) => <span className="mono text-[11px]" title={n.url}>{n.url}</span> },
              { k: "org", label: "tenant", w: "110px", sortable: false, cell: (n) => (n.org_id ? <span className="mono text-[11px]">{n.org_id}</span> : <span className="text-text-faint">—</span>) },
              { k: "auth", label: "auth", w: "80px", sortable: false, cell: (n) => <span className="mono text-[11px]">{n.auth_type}</span> },
              { k: "tls", label: "tls", w: "90px", sortable: false, cell: (n) => (n.verify_ssl ? <Tag tone="ok">verified</Tag> : <Tag tone="warn">unverified</Tag>) },
              {
                k: "actions",
                label: "",
                w: "170px",
                right: true,
                sortable: false,
                cell: (inst) => (
                  <span className="flex gap-0.5">
                    <button type="button" className="btn btn-sm btn-ghost" disabled={testingId === inst.id} onClick={() => handleTestConnection(inst)}>
                      {testingId === inst.id ? "testing…" : "test"}
                    </button>
                    <button type="button" className="btn btn-sm btn-ghost" onClick={() => openEdit(inst)}>
                      edit
                    </button>
                    <button type="button" className="btn btn-sm btn-ghost text-danger" disabled={deleteMutation.isPending} onClick={() => handleDelete(inst)}>
                      delete
                    </button>
                  </span>
                ),
              },
            ]}
            rows={all}
            keyOf={(n) => n.id}
            loading={isLoading}
            empty="No Grafana instances. Add a Mimir instance to see host metrics on the host page; add Loki for logs."
          />
        </Panel>

        <Panel title="metrics out" meta="scrape endpoint · LabDog's own fleet state and health">
          <div className="p-[11px]">
            <MetricsScrapeCard />
          </div>
        </Panel>
      </div>

      {dialogOpen && (
        <Modal
          title={editing ? "Edit Grafana instance" : "Add Grafana instance"}
          meta={editing?.name}
          w={560}
          onClose={closeDialog}
          onSubmit={(e) => {
            e.preventDefault()
            void handleSave()
          }}
          footer={
            <>
              <button type="button" className="btn btn-sm mr-auto" onClick={() => void handleDraftTest()} disabled={draftTesting || !form.url}>
                {draftTesting ? "Testing…" : "Test connection"}
              </button>
              <button type="button" className="btn" onClick={closeDialog}>
                Cancel
              </button>
              <button type="submit" className="btn btn-primary" disabled={formSaving}>
                {formSaving ? "Saving…" : editing ? "Save changes" : "Add instance"}
              </button>
            </>
          }
        >
          <div className="grid gap-[11px]" style={{ gridTemplateColumns: "1fr 200px" }}>
            <Field label="name" htmlFor="g-name">
              <input id="g-name" className="inp mono" placeholder="e.g. homelab" value={form.name} onChange={(e) => set("name", e.target.value)} />
            </Field>
            <Field label="kind" htmlFor="g-kind">
              <select id="g-kind" className="inp" value={form.kind} onChange={(e) => set("kind", e.target.value as GrafanaKind)}>
                <option value="mimir">Mimir / Prometheus (metrics)</option>
                <option value="loki">Loki (logs)</option>
              </select>
            </Field>
          </div>
          <Field label="ingest url" htmlFor="g-url" hint={`the remote-write / push URL — LabDog strips the path and queries the ${form.kind === "loki" ? "Loki" : "Mimir"} API from it`}>
            <input id="g-url" className="inp mono" placeholder={URL_PLACEHOLDER[form.kind]} value={form.url} onChange={(e) => set("url", e.target.value)} />
          </Field>
          <div className="grid gap-[11px]" style={{ gridTemplateColumns: "1fr 1fr" }}>
            <Field label="tenant / org id" htmlFor="g-org" hint="optional">
              <input id="g-org" className="inp mono" placeholder="anonymous" value={form.org_id} onChange={(e) => set("org_id", e.target.value)} />
            </Field>
            <Field label="authentication" htmlFor="g-auth">
              <select id="g-auth" className="inp" value={form.auth_type} onChange={(e) => set("auth_type", e.target.value as GrafanaAuthType)}>
                <option value="none">None</option>
                <option value="bearer">Bearer token</option>
                <option value="basic">Basic (username / password)</option>
              </select>
            </Field>
          </div>
          {form.auth_type !== "none" && (
            <div className="grid gap-[11px]" style={{ gridTemplateColumns: form.auth_type === "basic" ? "1fr 1fr" : "1fr" }}>
              {form.auth_type === "basic" && (
                <Field label="username" htmlFor="g-username">
                  <input id="g-username" className="inp mono" value={form.username} onChange={(e) => set("username", e.target.value)} />
                </Field>
              )}
              <Field label={form.auth_type === "basic" ? "password" : "bearer token"} htmlFor="g-token" hint={editing ? "blank keeps the current one" : undefined}>
                <input id="g-token" type="password" className="inp mono" autoComplete="off" placeholder={editing?.has_token ? "leave blank to keep current" : ""} value={form.token} onChange={(e) => set("token", e.target.value)} />
              </Field>
            </div>
          )}
          <div className="flex flex-wrap gap-4">
            <label className="flex items-center gap-2 text-xs text-text">
              <input id="g-default" type="checkbox" checked={form.is_default} onChange={(e) => set("is_default", e.target.checked)} />
              default {form.kind} instance
            </label>
            <label className="flex items-center gap-2 text-xs text-text">
              <input id="g-verify-ssl" type="checkbox" checked={form.verify_ssl} onChange={(e) => set("verify_ssl", e.target.checked)} />
              verify TLS certificate
            </label>
          </div>
          {form.verify_ssl && (
            <Field label="ca certificate" htmlFor="g-ca" hint="PEM, optional — for a private CA">
              {editing?.has_ca_cert && !form.ca_cert_clear && (
                <span className="text-[11.5px] text-text-3">
                  A CA is configured — paste a new PEM to replace it, or{" "}
                  <button type="button" className="text-danger underline" onClick={() => setForm((p) => ({ ...p, ca_cert_pem: "", ca_cert_clear: true }))}>
                    clear it
                  </button>
                  .
                </span>
              )}
              {editing?.has_ca_cert && form.ca_cert_clear && (
                <span className="text-[11.5px] text-warn">
                  The CA will be cleared on save.{" "}
                  <button type="button" className="text-text-2 underline" onClick={() => set("ca_cert_clear", false)}>
                    undo
                  </button>
                </span>
              )}
              <textarea
                id="g-ca"
                className="inp mono"
                rows={5}
                placeholder={"-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----"}
                value={form.ca_cert_pem}
                onChange={(e) => setForm((p) => ({ ...p, ca_cert_pem: e.target.value, ca_cert_clear: e.target.value.trim() ? false : p.ca_cert_clear }))}
              />
            </Field>
          )}
          {draftTestResult && <Banner tone={draftTestResult.success ? "ok" : "danger"}>{draftTestResult.message}</Banner>}
          {formError && <Banner tone="danger">{formError}</Banner>}
        </Modal>
      )}

      {confirmState && (
        <Confirm
          open
          onOpenChange={(open) => !open && setConfirmState(null)}
          title={confirmState.title}
          description={confirmState.description}
          confirmLabel="Delete"
          variant="destructive"
          loading={confirmState.loading}
          onConfirm={confirmState.action}
        />
      )}
    </>
  )
}
