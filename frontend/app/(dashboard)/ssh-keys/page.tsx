"use client"

import { useMemo, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { plural, shortAgo } from "@/lib/fleet"
import { showSuccess, showError } from "@/lib/toast"
import { sshKeySchema, type SshKeyInput } from "@/lib/schemas"
import type { SSHKey, Host } from "@/lib/types"
import { Banner, BulkBar, Confirm, Field, Filter, Modal, PageHead, Table, Tag, type Sort } from "@/components/ld"

const CRUMBS = [
  { label: "settings", href: "/settings" },
  { label: "access", href: "/settings?section=access" },
]

/**
 * SSH keys — the credential every host is reached with. A key is named,
 * carries the user it logs in as, and one of them is the default a new
 * host gets. The private key is written once and never shown again.
 */
export default function SSHKeysPage() {
  const queryClient = useQueryClient()
  const [q, setQ] = useState("")
  const [user, setUser] = useState("all")
  const [sort, setSort] = useState<Sort>({ k: "name", dir: 1 })
  const [sel, setSel] = useState<Set<string | number>>(new Set())
  const [uploadOpen, setUploadOpen] = useState(false)
  const [confirmState, setConfirmState] = useState<{ title: string; description: string; action: () => void | Promise<void>; loading?: boolean } | null>(null)
  const [bulkDeleting, setBulkDeleting] = useState(false)
  const [bulkProgress, setBulkProgress] = useState<{ done: number; total: number } | null>(null)
  const [bulkConfirmOpen, setBulkConfirmOpen] = useState(false)
  const [editingKey, setEditingKey] = useState<SSHKey | null>(null)
  const [editName, setEditName] = useState("")
  const [editSshUser, setEditSshUser] = useState("")
  const [editIsDefault, setEditIsDefault] = useState(false)
  const [editSaving, setEditSaving] = useState(false)
  const [editError, setEditError] = useState<string | null>(null)

  const form = useForm<SshKeyInput>({
    resolver: zodResolver(sshKeySchema),
    defaultValues: { name: "", private_key: "", ssh_user: "root", is_default: false },
    mode: "onSubmit",
  })

  const { data: sshKeys, isLoading, error } = useQuery<SSHKey[]>({
    queryKey: ["ssh-keys"],
    queryFn: () => apiFetch<SSHKey[]>("/api/ssh-keys"),
  })
  const { data: hosts } = useQuery<Host[]>({
    queryKey: ["hosts"],
    queryFn: () => apiFetch<Host[]>("/api/hosts"),
  })
  const hostCountByKey = useMemo(() => {
    const m = new Map<number, number>()
    hosts?.forEach((h) => {
      if (h.ssh_key_id != null) m.set(h.ssh_key_id, (m.get(h.ssh_key_id) ?? 0) + 1)
    })
    return m
  }, [hosts])

  const all = useMemo(() => sshKeys ?? [], [sshKeys])
  const users = useMemo(() => [...new Set(all.map((k) => k.ssh_user))].sort(), [all])
  const rows = useMemo(() => {
    const ql = q.trim().toLowerCase()
    const r = all.filter((k) => (user === "all" || k.ssh_user === user) && (!ql || k.name.toLowerCase().includes(ql) || (k.public_key ?? "").toLowerCase().includes(ql)))
    const key: Record<string, (k: SSHKey) => string | number> = {
      name: (k) => k.name.toLowerCase(),
      user: (k) => k.ssh_user,
      hosts: (k) => hostCountByKey.get(k.id) ?? 0,
      created: (k) => k.created_at,
    }
    const f = key[sort.k] ?? key.name
    return r.sort((a, b) => {
      const x = f(a)
      const y = f(b)
      return (x > y ? 1 : x < y ? -1 : 0) * sort.dir
    })
  }, [all, q, user, sort, hostCountByKey])

  const uploadMutation = useApiMutation({
    mutationFn: (data: SshKeyInput) =>
      apiFetch("/api/ssh-keys", {
        method: "POST",
        body: JSON.stringify({
          name: data.name,
          private_key: data.private_key,
          ssh_user: data.ssh_user,
          is_default: data.is_default ?? false,
        }),
      }),
    invalidateKeys: [["ssh-keys"]],
    onSuccess: () => {
      setUploadOpen(false)
      form.reset()
    },
  })

  const deleteMutation = useApiMutation<unknown, number, SSHKey>({
    mutationFn: (keyId) => apiFetch(`/api/ssh-keys/${keyId}`, { method: "DELETE" }),
    invalidateKeys: [["ssh-keys"]],
    successMessage: "SSH key deleted",
    optimisticUpdate: {
      queryKey: ["ssh-keys"],
      updater: (old, keyId) => old.filter((k) => k.id !== keyId),
    },
  })

  const closeUpload = () => {
    setUploadOpen(false)
    form.reset()
    uploadMutation.reset()
  }

  const onUpload = form.handleSubmit((data) => uploadMutation.mutate(data))

  function handleDelete(key: SSHKey) {
    const n = hostCountByKey.get(key.id) ?? 0
    setConfirmState({
      title: "Delete SSH key",
      description: n > 0 ? `${plural(n, "host")} reach the fleet with ${key.name}; they will need another key. This cannot be undone.` : `Delete ${key.name}? This cannot be undone.`,
      action: async () => {
        setConfirmState((prev) => (prev ? { ...prev, loading: true } : null))
        try {
          await deleteMutation.mutateAsync(key.id)
        } finally {
          setConfirmState(null)
        }
      },
    })
  }

  async function handleBulkDelete() {
    const ids = Array.from(sel) as number[]
    setBulkDeleting(true)
    setBulkProgress({ done: 0, total: ids.length })
    let success = 0
    let failed = 0
    for (const id of ids) {
      try {
        await apiFetch(`/api/ssh-keys/${id}`, { method: "DELETE" })
        success++
      } catch {
        failed++
      }
      setBulkProgress({ done: success + failed, total: ids.length })
    }
    setBulkDeleting(false)
    setBulkProgress(null)
    setSel(new Set())
    await queryClient.invalidateQueries({ queryKey: ["ssh-keys"] })
    if (failed === 0) showSuccess(`Deleted ${plural(success, "SSH key")}`)
    else showError(`Deleted ${success} of ${ids.length}. ${failed} failed.`)
    setBulkConfirmOpen(false)
  }

  function openEdit(key: SSHKey) {
    setEditingKey(key)
    setEditName(key.name)
    setEditSshUser(key.ssh_user)
    setEditIsDefault(key.is_default)
    setEditError(null)
  }

  async function handleEditSave() {
    if (!editingKey) return
    setEditSaving(true)
    setEditError(null)
    try {
      await apiFetch(`/api/ssh-keys/${editingKey.id}`, {
        method: "PUT",
        body: JSON.stringify({
          name: editName !== editingKey.name ? editName : undefined,
          ssh_user: editSshUser !== editingKey.ssh_user ? editSshUser : undefined,
          is_default: editIsDefault !== editingKey.is_default ? editIsDefault : undefined,
        }),
      })
      await queryClient.invalidateQueries({ queryKey: ["ssh-keys"] })
      showSuccess("SSH key updated")
      setEditingKey(null)
    } catch (err) {
      setEditError(err instanceof Error ? err.message : "Failed to update")
    } finally {
      setEditSaving(false)
    }
  }

  return (
    <>
      <PageHead
        crumbs={CRUMBS}
        title={
          <>
            SSH keys <span className="mono num text-[12.5px] font-normal text-text-faint">{all.length}</span>
          </>
        }
        sub="The credential every host is reached with. The default key is what a new host gets; the private key is encrypted at rest and never shown again."
        actions={
          <button type="button" className="btn btn-sm btn-primary" onClick={() => setUploadOpen(true)}>
            Upload key…
          </button>
        }
      >
        <div className="flex flex-wrap items-center gap-[7px]">
          <input className="inp mono" style={{ width: 200, fontSize: 11.5, padding: "4px 8px" }} placeholder="Search keys…" aria-label="search keys" value={q} onChange={(e) => setQ(e.target.value)} />
          <Filter label="user" value={user} onChange={setUser} options={users.map((u) => ({ k: u, label: u, n: all.filter((k) => k.ssh_user === u).length }))} />
          <span className="tt ml-auto hidden sm:inline">a host with no key of its own uses the default</span>
        </div>
      </PageHead>

      {error && (
        <Banner tone="danger" flush>
          Could not load SSH keys: {error.message}
        </Banner>
      )}

      <Table<SSHKey>
        cols={[
          {
            k: "name",
            label: "name",
            w: "minmax(220px,1.4fr)",
            cell: (k) => (
              <span className="flex min-w-0 flex-col">
                <span className="flex items-center gap-1.5">
                  <span className="mono trunc font-medium text-text">{k.name}</span>
                  {k.is_default && <Tag tone="accent">default</Tag>}
                </span>
                {k.public_key && (
                  <span className="mono trunc text-[10.5px] text-text-faint" title={k.public_key}>
                    {k.public_key.split(" ").slice(0, 2).join(" ").substring(0, 48)}…
                  </span>
                )}
              </span>
            ),
          },
          { k: "user", label: "user", w: "110px", cell: (k) => <span className="mono text-[11px]">{k.ssh_user}</span> },
          {
            k: "hosts",
            label: "hosts",
            w: "70px",
            right: true,
            cell: (k) => {
              const n = hostCountByKey.get(k.id) ?? 0
              return <span className={`mono num ${n ? "text-text" : "text-text-faint"}`}>{n}</span>
            },
          },
          { k: "created", label: "added", w: "84px", cell: (k) => <span className="mono num text-[11px]" title={new Date(k.created_at).toLocaleString()}>{shortAgo(k.created_at)} ago</span> },
          {
            k: "actions",
            label: "",
            w: "118px",
            right: true,
            sortable: false,
            cell: (k) => (
              <span className="flex gap-0.5">
                <button type="button" className="btn btn-sm btn-ghost" onClick={() => openEdit(k)}>
                  edit
                </button>
                <button type="button" className="btn btn-sm btn-ghost text-danger" disabled={deleteMutation.isPending} onClick={() => handleDelete(k)}>
                  delete
                </button>
              </span>
            ),
          },
        ]}
        rows={rows}
        keyOf={(k) => k.id}
        sort={sort}
        onSort={(k) => setSort((s) => ({ k, dir: s.k === k ? ((-s.dir) as 1 | -1) : 1 }))}
        selected={sel}
        onSelect={setSel}
        loading={isLoading}
        empty={all.length === 0 ? "No SSH keys yet. Upload one before adding hosts — it is how LabDog reaches them." : "No key matches."}
      />

      <BulkBar n={sel.size} onClear={() => setSel(new Set())} status={bulkProgress ? `Deleting ${bulkProgress.done}/${bulkProgress.total}…` : undefined}>
        <button type="button" className="btn btn-sm btn-danger" disabled={bulkDeleting} onClick={() => setBulkConfirmOpen(true)}>
          Delete selected
        </button>
      </BulkBar>

      {uploadOpen && (
        <Modal
          title="Upload SSH key"
          meta="encrypted at rest"
          w={520}
          onClose={closeUpload}
          onSubmit={onUpload}
          footer={
            <>
              <span className="tt mr-auto">AES-256-GCM before it touches the database</span>
              <button type="button" className="btn" onClick={closeUpload}>
                Cancel
              </button>
              <button type="submit" className="btn btn-primary" disabled={uploadMutation.isPending}>
                {uploadMutation.isPending ? "Uploading…" : "Upload key"}
              </button>
            </>
          }
        >
          <div className="grid gap-[11px]" style={{ gridTemplateColumns: "1fr 140px" }}>
            <Field label="name" htmlFor="key-name" error={form.formState.errors.name?.message}>
              <input id="key-name" className="inp mono" placeholder="e.g. production-key" {...form.register("name")} />
            </Field>
            <Field label="ssh user" htmlFor="ssh-user" error={form.formState.errors.ssh_user?.message}>
              <input id="ssh-user" className="inp mono" placeholder="root" {...form.register("ssh_user")} />
            </Field>
          </div>
          <Field label="private key" htmlFor="private-key" hint="never shown again" error={form.formState.errors.private_key?.message}>
            <textarea id="private-key" className="inp mono" rows={7} placeholder="-----BEGIN OPENSSH PRIVATE KEY-----" spellCheck={false} {...form.register("private_key")} />
          </Field>
          <label className="flex items-center gap-2 text-xs text-text">
            <input id="is-default" type="checkbox" {...form.register("is_default")} />
            set as the default key
          </label>
          {uploadMutation.error && <Banner tone="danger">{uploadMutation.error.message}</Banner>}
        </Modal>
      )}

      {editingKey && (
        <Modal
          title="Edit SSH key"
          meta={editingKey.name}
          w={460}
          onClose={() => setEditingKey(null)}
          onSubmit={(e) => {
            e.preventDefault()
            void handleEditSave()
          }}
          footer={
            <>
              <span className="tt mr-auto">the key material cannot be changed — upload a new one</span>
              <button type="button" className="btn" onClick={() => setEditingKey(null)}>
                Cancel
              </button>
              <button type="submit" className="btn btn-primary" disabled={editSaving}>
                {editSaving ? "Saving…" : "Save"}
              </button>
            </>
          }
        >
          <div className="grid gap-[11px]" style={{ gridTemplateColumns: "1fr 140px" }}>
            <Field label="name" htmlFor="edit-name">
              <input id="edit-name" className="inp mono" value={editName} onChange={(e) => setEditName(e.target.value)} />
            </Field>
            <Field label="ssh user" htmlFor="edit-ssh-user">
              <input id="edit-ssh-user" className="inp mono" value={editSshUser} onChange={(e) => setEditSshUser(e.target.value)} />
            </Field>
          </div>
          <label className="flex items-center gap-2 text-xs text-text">
            <input id="edit-default" type="checkbox" checked={editIsDefault} onChange={(e) => setEditIsDefault(e.target.checked)} />
            set as the default key
          </label>
          {editError && <Banner tone="danger">{editError}</Banner>}
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

      <Confirm
        open={bulkConfirmOpen}
        onOpenChange={setBulkConfirmOpen}
        title={`Delete ${plural(sel.size, "key")}?`}
        description="Hosts that use these keys will need another one before LabDog can reach them again. This cannot be undone."
        confirmLabel="Delete All"
        variant="destructive"
        loading={bulkDeleting}
        onConfirm={handleBulkDelete}
      />
    </>
  )
}
