"use client"

import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { showSuccess, showError } from "@/lib/toast"
import { Banner, Confirm, Field, Modal, PageHead, Table, Tag } from "@/components/ld"
import type { ProxmoxNode, VMMapping } from "@/lib/types"

const CRUMBS = [
  { label: "settings", href: "/settings" },
  { label: "integrations", href: "/settings" },
]

interface NodeFormState {
  name: string
  api_url: string
  token_id: string
  token_secret: string
  verify_ssl: boolean
  ca_cert_pem: string
  ca_cert_clear: boolean
}

const emptyForm: NodeFormState = {
  name: "",
  api_url: "",
  token_id: "",
  token_secret: "",
  verify_ssl: true,
  ca_cert_pem: "",
  ca_cert_clear: false,
}

/**
 * Proxmox — the hypervisors LabDog snapshots guests on before a
 * destructive action, and rolls back to when verification fails. Served
 * at `/hypervisors`; `/settings/proxmox` redirects there.
 */
export default function ProxmoxSettingsPage() {
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingNode, setEditingNode] = useState<ProxmoxNode | null>(null)
  const [form, setForm] = useState<NodeFormState>(emptyForm)
  const [formError, setFormError] = useState<string | null>(null)
  const [formSaving, setFormSaving] = useState(false)
  const [testingId, setTestingId] = useState<number | null>(null)
  const [cleaningUp, setCleaningUp] = useState(false)
  const [discovering, setDiscovering] = useState(false)
  const [confirmState, setConfirmState] = useState<{ title: string; description: string; action: () => void | Promise<void>; loading?: boolean } | null>(null)

  const queryClient = useQueryClient()

  const { data: nodes, isLoading, error } = useQuery<ProxmoxNode[]>({
    queryKey: ["proxmox-nodes"],
    queryFn: () => apiFetch<ProxmoxNode[]>("/api/proxmox/nodes"),
  })

  const deleteMutation = useApiMutation<unknown, number, ProxmoxNode>({
    mutationFn: (nodeId) => apiFetch(`/api/proxmox/nodes/${nodeId}`, { method: "DELETE" }),
    invalidateKeys: [["proxmox-nodes"]],
    successMessage: "Proxmox node deleted",
    optimisticUpdate: {
      queryKey: ["proxmox-nodes"],
      updater: (old, nodeId) => old.filter((n) => n.id !== nodeId),
    },
  })

  const set = <K extends keyof NodeFormState>(k: K, v: NodeFormState[K]) => setForm((p) => ({ ...p, [k]: v }))

  function openCreate() {
    setEditingNode(null)
    setForm(emptyForm)
    setFormError(null)
    setDialogOpen(true)
  }

  function openEdit(node: ProxmoxNode) {
    setEditingNode(node)
    setForm({
      name: node.name,
      api_url: node.api_url,
      token_id: node.token_id,
      token_secret: "",
      verify_ssl: node.verify_ssl,
      ca_cert_pem: "",
      ca_cert_clear: false,
    })
    setFormError(null)
    setDialogOpen(true)
  }

  function closeDialog() {
    setDialogOpen(false)
    setFormError(null)
  }

  async function handleSave() {
    setFormSaving(true)
    setFormError(null)
    try {
      if (editingNode) {
        const payload: Record<string, unknown> = {
          name: form.name || undefined,
          api_url: form.api_url || undefined,
          token_id: form.token_id || undefined,
          verify_ssl: form.verify_ssl,
        }
        if (form.token_secret) payload.token_secret = form.token_secret
        // CA cert is tri-state on PUT: a pasted value replaces, an explicit
        // clear sends "" (NULL the column), and omitting leaves it unchanged.
        if (form.ca_cert_pem.trim()) payload.ca_cert_pem = form.ca_cert_pem
        else if (form.ca_cert_clear) payload.ca_cert_pem = ""
        await apiFetch(`/api/proxmox/nodes/${editingNode.id}`, { method: "PUT", json: payload })
        showSuccess("Proxmox node updated")
      } else {
        const payload: Record<string, unknown> = {
          name: form.name,
          api_url: form.api_url,
          token_id: form.token_id,
          token_secret: form.token_secret,
          verify_ssl: form.verify_ssl,
        }
        // Only send a CA cert on create when one was actually pasted.
        if (form.ca_cert_pem.trim()) payload.ca_cert_pem = form.ca_cert_pem
        await apiFetch("/api/proxmox/nodes", { method: "POST", json: payload })
        showSuccess("Proxmox node created")
      }
      await queryClient.invalidateQueries({ queryKey: ["proxmox-nodes"] })
      setDialogOpen(false)
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to save")
    } finally {
      setFormSaving(false)
    }
  }

  function handleDelete(node: ProxmoxNode) {
    setConfirmState({
      title: "Delete Proxmox node",
      description: `Guests on ${node.name} lose snapshot-before-change and rollback until it is added again. This cannot be undone.`,
      action: async () => {
        setConfirmState((prev) => (prev ? { ...prev, loading: true } : null))
        try {
          await deleteMutation.mutateAsync(node.id)
        } finally {
          setConfirmState(null)
        }
      },
    })
  }

  async function handleCleanupSnapshots() {
    setCleaningUp(true)
    try {
      const result = await apiFetch<{ deleted: number; errors: string[] }>("/api/proxmox/nodes/cleanup-snapshots", { method: "POST" })
      showSuccess(`Cleaned up ${result.deleted} orphaned snapshot(s)`)
    } catch (err) {
      showError(err instanceof Error ? err.message : "Cleanup failed")
    } finally {
      setCleaningUp(false)
    }
  }

  async function handleDiscoverAll() {
    setDiscovering(true)
    try {
      const result = await apiFetch<VMMapping[]>("/api/proxmox/discover", { method: "POST" })
      showSuccess(`Discovered ${result.length} VM mapping(s)`)
      // Refresh the hosts overview column and any open host detail pages.
      await queryClient.invalidateQueries({ queryKey: ["vm-mappings"] })
      await queryClient.invalidateQueries({ queryKey: ["host-vm-mapping"] })
    } catch (err) {
      showError(err instanceof Error ? err.message : "Discovery failed")
    } finally {
      setDiscovering(false)
    }
  }

  async function handleTestConnection(node: ProxmoxNode) {
    setTestingId(node.id)
    try {
      const result = await apiFetch<{ success: boolean; message: string; version: string | null }>(`/api/proxmox/nodes/${node.id}/test`, { method: "POST" })
      if (result.success) showSuccess(result.version ? `Connected — Proxmox ${result.version}` : "Connection successful")
      else showError(`Connection failed: ${result.message}`)
    } catch (err) {
      showError(err instanceof Error ? err.message : "Test failed")
    } finally {
      setTestingId(null)
    }
  }

  const all = nodes ?? []

  return (
    <>
      <PageHead
        crumbs={CRUMBS}
        title={
          <>
            Proxmox <span className="mono num text-[12.5px] font-normal text-text-faint">{all.length}</span>
          </>
        }
        sub="A connected node lets LabDog snapshot a guest before a destructive action and roll it back if verification fails. Guests are matched to hosts by discovering VM mappings."
        actions={
          <>
            <button type="button" className="btn btn-sm" onClick={() => void handleDiscoverAll()} disabled={discovering || all.length === 0}>
              {discovering ? "Discovering…" : "Discover VM mappings"}
            </button>
            <button type="button" className="btn btn-sm" onClick={() => void handleCleanupSnapshots()} disabled={cleaningUp || all.length === 0}>
              {cleaningUp ? "Cleaning…" : "Clean up orphaned snapshots"}
            </button>
            <button type="button" className="btn btn-sm btn-primary" onClick={openCreate}>
              Add node…
            </button>
          </>
        }
      />

      {error && (
        <Banner tone="danger" flush>
          Could not load Proxmox nodes: {error.message}
        </Banner>
      )}

      <Table<ProxmoxNode>
        cols={[
          { k: "name", label: "name", w: "minmax(140px,1fr)", sortable: false, cell: (n) => <span className="mono font-medium text-text">{n.name}</span> },
          { k: "api_url", label: "api url", w: "minmax(220px,1.6fr)", sortable: false, cell: (n) => <span className="mono text-[11px]">{n.api_url}</span> },
          { k: "token_id", label: "token id", w: "minmax(150px,1fr)", sortable: false, cell: (n) => <span className="mono text-[11px]">{n.token_id}</span> },
          { k: "tls", label: "tls", w: "96px", sortable: false, cell: (n) => (n.verify_ssl ? <Tag tone="ok">verified</Tag> : <Tag tone="warn">unverified</Tag>) },
          {
            k: "ca",
            label: "ca cert",
            w: "150px",
            sortable: false,
            cell: (n) =>
              n.has_ca_cert ? (
                <span className="mono text-[11px] text-ok" title={n.ca_cert_fingerprint ?? undefined}>
                  {n.ca_cert_fingerprint ? `${n.ca_cert_fingerprint.slice(0, 16)}…` : "yes"}
                </span>
              ) : (
                <span className="text-text-faint">system trust store</span>
              ),
          },
          {
            k: "actions",
            label: "",
            w: "170px",
            right: true,
            sortable: false,
            cell: (node) => (
              <span className="flex gap-0.5">
                <button type="button" className="btn btn-sm btn-ghost" disabled={testingId === node.id} onClick={() => handleTestConnection(node)}>
                  {testingId === node.id ? "testing…" : "test"}
                </button>
                <button type="button" className="btn btn-sm btn-ghost" onClick={() => openEdit(node)}>
                  edit
                </button>
                <button type="button" className="btn btn-sm btn-ghost text-danger" disabled={deleteMutation.isPending} onClick={() => handleDelete(node)}>
                  delete
                </button>
              </span>
            ),
          },
        ]}
        rows={all}
        keyOf={(n) => n.id}
        loading={isLoading}
        empty="No Proxmox nodes. Add one to snapshot guests before destructive actions and roll back when verification fails."
      />

      {dialogOpen && (
        <Modal
          title={editingNode ? "Edit Proxmox node" : "Add Proxmox node"}
          meta={editingNode?.name}
          w={520}
          onClose={closeDialog}
          onSubmit={(e) => {
            e.preventDefault()
            void handleSave()
          }}
          footer={
            <>
              <span className="tt mr-auto">an API token with VM.Snapshot and VM.Audit is enough</span>
              <button type="button" className="btn" onClick={closeDialog}>
                Cancel
              </button>
              <button type="submit" className="btn btn-primary" disabled={formSaving}>
                {formSaving ? "Saving…" : editingNode ? "Save changes" : "Add node"}
              </button>
            </>
          }
        >
          <div className="grid gap-[11px]" style={{ gridTemplateColumns: "140px 1fr" }}>
            <Field label="name" htmlFor="node-name">
              <input id="node-name" className="inp mono" placeholder="e.g. pve-01" value={form.name} onChange={(e) => set("name", e.target.value)} />
            </Field>
            <Field label="api url" htmlFor="node-api-url">
              <input id="node-api-url" className="inp mono" placeholder="https://pve.example.com:8006" value={form.api_url} onChange={(e) => set("api_url", e.target.value)} />
            </Field>
          </div>
          <div className="grid gap-[11px]" style={{ gridTemplateColumns: "1fr 1fr" }}>
            <Field label="token id" htmlFor="node-token-id">
              <input id="node-token-id" className="inp mono" placeholder="user@realm!tokenname" value={form.token_id} onChange={(e) => set("token_id", e.target.value)} />
            </Field>
            <Field label="token secret" htmlFor="node-token-secret" hint={editingNode ? "blank keeps the current one" : undefined}>
              <input id="node-token-secret" type="password" className="inp mono" autoComplete="off" placeholder={editingNode ? "leave blank to keep current" : "API token secret UUID"} value={form.token_secret} onChange={(e) => set("token_secret", e.target.value)} />
            </Field>
          </div>
          <label className="flex items-center gap-2 text-xs text-text">
            <input id="node-verify-ssl" type="checkbox" checked={form.verify_ssl} onChange={(e) => set("verify_ssl", e.target.checked)} />
            verify the node&apos;s TLS certificate
          </label>
          {form.verify_ssl && (
            <Field label="ca certificate" htmlFor="node-ca-cert" hint="PEM, optional — blank uses the system trust store">
              {editingNode?.has_ca_cert && !form.ca_cert_clear && (
                <span className="text-[11.5px] text-text-3">
                  A CA is configured{editingNode.ca_cert_fingerprint && <> (<span className="mono break-all text-text-2">{editingNode.ca_cert_fingerprint}</span>)</>} — paste a new PEM to replace it, or{" "}
                  <button type="button" className="text-danger underline" onClick={() => setForm((p) => ({ ...p, ca_cert_pem: "", ca_cert_clear: true }))}>
                    clear it
                  </button>
                  .
                </span>
              )}
              {editingNode?.has_ca_cert && form.ca_cert_clear && (
                <span className="text-[11.5px] text-warn">
                  The CA will be cleared on save.{" "}
                  <button type="button" className="text-text-2 underline" onClick={() => set("ca_cert_clear", false)}>
                    undo
                  </button>
                </span>
              )}
              <textarea
                id="node-ca-cert"
                className="inp mono"
                rows={6}
                placeholder={"-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----"}
                value={form.ca_cert_pem}
                onChange={(e) =>
                  setForm((p) => ({
                    ...p,
                    ca_cert_pem: e.target.value,
                    // Typing a PEM supersedes an explicit clear.
                    ca_cert_clear: e.target.value.trim() ? false : p.ca_cert_clear,
                  }))
                }
              />
            </Field>
          )}
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
