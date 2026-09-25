"use client"

import { useState, type FormEvent } from "react"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { plural, shortAgo } from "@/lib/fleet"
import { ITEM_STATE, JOB_STATUS, def } from "@/lib/status"
import type { CACertActionRun, CACertRule } from "@/lib/types"
import { Banner, Confirm, Dot, Field, Modal, Table, Tag, Toolbar } from "@/components/ld"
import { GROUP_KEYS, GitOpsBanner, stop, useEditorGroup } from "./shared"

function shortFingerprint(fp: string) {
  // Show first 6 and last 6 hex pairs for compactness
  const parts = fp.split(":")
  if (parts.length <= 14) return fp
  return `${parts.slice(0, 6).join(":")}…${parts.slice(-6).join(":")}`
}

/**
 * CA certificates a group declares for its hosts' trust stores. Unlike
 * the other modules these are deployed as a one-time action rather
 * than through a plan — no drift detection — so the editor also shows
 * the recent deployment runs. Embedded in the group page's Config tab.
 */
export function CaCertsEditor({ groupId }: { groupId: number }) {
  const { group, gitops } = useEditorGroup(groupId)

  const [addOpen, setAddOpen] = useState(false)
  const [name, setName] = useState("")
  const [pem, setPem] = useState("")
  const [comment, setComment] = useState("")

  const [editTarget, setEditTarget] = useState<CACertRule | null>(null)
  const [editName, setEditName] = useState("")
  const [editState, setEditState] = useState<"present" | "absent">("present")
  const [editComment, setEditComment] = useState("")

  const [deleteTarget, setDeleteTarget] = useState<CACertRule | null>(null)
  const [deployConfirm, setDeployConfirm] = useState(false)

  const { data: certs = [], isLoading, error } = useQuery<CACertRule[]>({
    queryKey: ["group-ca-certs", groupId],
    queryFn: () => apiFetch<CACertRule[]>(`/api/groups/${groupId}/ca-certs`),
    enabled: !!groupId,
  })

  const { data: runs = [] } = useQuery<CACertActionRun[]>({
    queryKey: ["group-ca-cert-runs", groupId],
    queryFn: () => apiFetch<CACertActionRun[]>(`/api/ca-certs/groups/${groupId}/runs`),
    enabled: !!groupId,
    // Only poll while something is actually in flight, matching the predicate
    // used by components/actions-tab.tsx. This was an unconditional 5s
    // interval, so an idle tab kept hitting the API forever even when the
    // run list had been terminal for weeks.
    refetchInterval: (query) => {
      const data = query.state.data as CACertActionRun[] | undefined
      if (!data) return false
      return data.some((r) => r.status === "pending" || r.status === "running") ? 5000 : false
    },
  })

  const createMutation = useApiMutation({
    mutationFn: (payload: Record<string, unknown>) => apiFetch(`/api/groups/${groupId}/ca-certs`, { method: "POST", body: JSON.stringify(payload) }),
    invalidateKeys: [["group-ca-certs", groupId], ...GROUP_KEYS],
    onSuccess: () => {
      setAddOpen(false)
      setName("")
      setPem("")
      setComment("")
    },
  })
  const updateMutation = useApiMutation({
    mutationFn: ({ ruleId, payload }: { ruleId: number; payload: Record<string, unknown> }) => apiFetch(`/api/groups/${groupId}/ca-certs/${ruleId}`, { method: "PUT", body: JSON.stringify(payload) }),
    invalidateKeys: [["group-ca-certs", groupId], ...GROUP_KEYS],
    onSuccess: () => setEditTarget(null),
  })
  const deleteMutation = useApiMutation({
    mutationFn: (ruleId: number) => apiFetch(`/api/groups/${groupId}/ca-certs/${ruleId}`, { method: "DELETE" }),
    invalidateKeys: [["group-ca-certs", groupId], ...GROUP_KEYS],
    onSuccess: () => setDeleteTarget(null),
  })
  const deployMutation = useApiMutation({
    mutationFn: () => apiFetch(`/api/ca-certs/groups/${groupId}/deploy`, { method: "POST" }),
    invalidateKeys: [["group-ca-cert-runs", groupId]],
    successMessage: "Deployment started on every host in the group",
    onSuccess: () => setDeployConfirm(false),
  })

  function handleCreate(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    createMutation.mutate({ name, pem_content: pem, state: "present", comment: comment || null })
  }

  function openEdit(c: CACertRule) {
    setEditTarget(c)
    setEditName(c.name)
    setEditState(c.state)
    setEditComment(c.comment ?? "")
    updateMutation.reset()
  }

  function handleEdit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!editTarget) return
    updateMutation.mutate({ ruleId: editTarget.id, payload: { name: editName, state: editState, comment: editComment || null } })
  }

  const expiry = (c: CACertRule) => {
    if (!c.not_after) return null
    const days = Math.floor((new Date(c.not_after).getTime() - Date.now()) / 86_400_000)
    return { days, tone: days < 0 ? ("danger" as const) : days < 90 ? ("warn" as const) : undefined }
  }
  const active = runs.some((r) => r.status === "pending" || r.status === "running")

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Toolbar
        actions={
          <>
            <button type="button" className="btn btn-sm" onClick={() => setDeployConfirm(true)} disabled={deployMutation.isPending || certs.length === 0 || active} title={active ? "a deployment is already running" : "run the deployment playbook on every host in the group"}>
              Deploy to all hosts
            </button>
            {!gitops && (
              <button type="button" className="btn btn-sm btn-primary" onClick={() => setAddOpen(true)}>
                Add certificate
              </button>
            )}
          </>
        }
      >
        <span className="tt">{plural(certs.length, "certificate")} declared here</span>
        <span className="hidden text-[11px] text-text-faint sm:inline">deployed as a one-time action, no drift detection · new hosts auto-deploy</span>
      </Toolbar>
      {gitops && <GitOpsBanner group={group} what="CA certificates are" />}
      {error && (
        <Banner tone="danger" flush>
          Could not load certificates: {error.message}
        </Banner>
      )}

      <div className="scroll flex min-h-0 flex-1 flex-col">
        <div className="flex shrink-0 flex-col">
        <Table<CACertRule>
          cols={[
            { k: "name", label: "certificate", w: "minmax(140px,1fr)", sortable: false, cell: (c) => <span className="font-medium text-text">{c.name}</span> },
            { k: "subject", label: "subject", w: "minmax(160px,1.4fr)", sortable: false, cell: (c) => <span className="mono text-[11px]" title={c.subject ?? ""}>{c.subject ?? <span className="text-text-faint">—</span>}</span> },
            {
              k: "expires",
              label: "expires",
              w: "110px",
              sortable: false,
              cell: (c) => {
                const e = expiry(c)
                if (!e) return <span className="text-text-faint">—</span>
                return (
                  <span className="mono num text-[11px]" style={{ color: e.tone ? `var(--${e.tone})` : undefined }} title={c.not_after ?? undefined}>
                    {e.days < 0 ? "expired" : `${e.days}d`}
                  </span>
                )
              },
            },
            { k: "fp", label: "sha-256", w: "minmax(170px,1fr)", sortable: false, cell: (c) => <span className="mono text-[10.5px] text-text-3" title={c.fingerprint_sha256}>{shortFingerprint(c.fingerprint_sha256)}</span> },
            { k: "state", label: "state", w: "90px", sortable: false, cell: (c) => <Tag tone={def(ITEM_STATE, c.state).tone}>{c.state}</Tag> },
            {
              k: "actions",
              label: "",
              w: "118px",
              right: true,
              sortable: false,
              cell: (c) => (
                <span className="flex gap-0.5" onClick={stop}>
                  <button type="button" className="btn btn-sm btn-ghost" disabled={gitops} onClick={() => openEdit(c)}>
                    edit
                  </button>
                  <button type="button" className="btn btn-sm btn-ghost text-danger" disabled={gitops || deleteMutation.isPending} onClick={() => setDeleteTarget(c)}>
                    delete
                  </button>
                </span>
              ),
            },
          ]}
          rows={certs}
          keyOf={(c) => c.id}
          onRowClick={gitops ? undefined : openEdit}
          loading={isLoading}
          empty="Nothing declared. Add certificate declares this module for the group; Deploy pushes the trust store to every host."
        />
        </div>

        <div className="tt shrink-0 border-y border-line bg-surface-2 px-[13px] py-[7px]">recent deployment runs · {plural(runs.length, "run")}</div>
        <div className="flex shrink-0 flex-col">
        <Table<CACertActionRun>
          cols={[
            { k: "id", label: "run", w: "70px", sortable: false, cell: (r) => <span className="mono num text-text-faint">#{r.id}</span> },
            { k: "host", label: "host", w: "minmax(140px,1fr)", sortable: false, cell: (r) => <span className="mono text-text">{r.hostname ?? `host ${r.host_id}`}</span> },
            {
              k: "status",
              label: "status",
              w: "110px",
              sortable: false,
              cell: (r) => {
                const s = def(JOB_STATUS, r.status)
                return (
                  <span className="inline-flex items-center gap-1.5 text-[11.5px] font-medium" style={{ color: `var(--${s.tone})` }} title={r.error_message ?? undefined}>
                    <Dot tone={s.tone} pulse={r.status === "running"} />
                    {s.label}
                  </span>
                )
              },
            },
            { k: "started", label: "started", w: "110px", sortable: false, cell: (r) => <span className="mono num text-[11px]" title={r.started_at ?? undefined}>{r.started_at ? `${shortAgo(r.started_at)} ago` : "—"}</span> },
            { k: "completed", label: "completed", w: "110px", sortable: false, cell: (r) => <span className="mono num text-[11px]" title={r.completed_at ?? undefined}>{r.completed_at ? `${shortAgo(r.completed_at)} ago` : "—"}</span> },
            { k: "err", label: "", w: "minmax(120px,1fr)", sortable: false, cell: (r) => (r.error_message ? <span className="text-[11px] text-danger">{r.error_message}</span> : null) },
          ]}
          rows={runs}
          keyOf={(r) => r.id}
          rowTone={(r) => (r.status === "failed" ? "danger" : undefined)}
          empty="No deployments yet."
        />
        </div>
      </div>

      {addOpen && (
        <Modal
          title="Add CA certificate"
          meta={group ? `group: ${group.name}` : undefined}
          w={620}
          onClose={() => setAddOpen(false)}
          onSubmit={handleCreate}
          footer={
            <>
              <span className="tt mr-auto">metadata is read from the PEM</span>
              <button type="button" className="btn" onClick={() => setAddOpen(false)}>
                Cancel
              </button>
              <button type="submit" className="btn btn-primary" disabled={createMutation.isPending || !name.trim() || !pem.trim()}>
                {createMutation.isPending ? "Adding…" : "Add certificate"}
              </button>
            </>
          }
        >
          <Field label="display name" htmlFor="ca-name">
            <input id="ca-name" className="inp" placeholder="Internal Root CA" value={name} onChange={(e) => setName(e.target.value)} required />
          </Field>
          <Field label="pem" htmlFor="ca-pem">
            <textarea id="ca-pem" className="inp mono" rows={10} spellCheck={false} placeholder={"-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----"} value={pem} onChange={(e) => setPem(e.target.value)} required />
          </Field>
          <Field label="comment" htmlFor="ca-comment" hint="optional">
            <input id="ca-comment" className="inp" value={comment} onChange={(e) => setComment(e.target.value)} />
          </Field>
          {createMutation.error && <Banner tone="danger">{createMutation.error.message}</Banner>}
        </Modal>
      )}

      {editTarget && (
        <Modal
          title="Edit CA certificate"
          meta={editTarget.name}
          w={480}
          onClose={() => setEditTarget(null)}
          onSubmit={handleEdit}
          footer={
            <>
              <span className="tt mr-auto">the certificate itself cannot change — add a new one</span>
              <button type="button" className="btn" onClick={() => setEditTarget(null)}>
                Cancel
              </button>
              <button type="submit" className="btn btn-primary" disabled={updateMutation.isPending}>
                {updateMutation.isPending ? "Saving…" : "Save changes"}
              </button>
            </>
          }
        >
          <div className="grid gap-[11px] grid-cols-1 sm:grid-cols-[1fr_110px]">
            <Field label="display name" htmlFor="ca-edit-name">
              <input id="ca-edit-name" className="inp" value={editName} onChange={(e) => setEditName(e.target.value)} required />
            </Field>
            <Field label="state" htmlFor="ca-edit-state">
              <select id="ca-edit-state" className="inp" value={editState} onChange={(e) => setEditState(e.target.value as "present" | "absent")}>
                <option value="present">present</option>
                <option value="absent">absent</option>
              </select>
            </Field>
          </div>
          <Field label="comment" htmlFor="ca-edit-comment" hint="optional">
            <input id="ca-edit-comment" className="inp" value={editComment} onChange={(e) => setEditComment(e.target.value)} />
          </Field>
          {updateMutation.error && <Banner tone="danger">{updateMutation.error.message}</Banner>}
        </Modal>
      )}

      {deleteTarget && (
        <Confirm
          open
          onOpenChange={(open) => !open && setDeleteTarget(null)}
          title="Delete CA certificate"
          description={`${deleteTarget.name} is no longer declared by this group; it is removed from the hosts on the next deploy run.`}
          confirmLabel="Delete"
          variant="destructive"
          loading={deleteMutation.isPending}
          onConfirm={() => deleteMutation.mutate(deleteTarget.id)}
        />
      )}

      <Confirm
        open={deployConfirm}
        onOpenChange={setDeployConfirm}
        title="Deploy CA certificates to all hosts?"
        description="Runs the CA certificate deployment playbook on every host in this group. Hosts already running a deploy are skipped."
        confirmLabel="Deploy"
        loading={deployMutation.isPending}
        onConfirm={() => deployMutation.mutate()}
      />
    </div>
  )
}
