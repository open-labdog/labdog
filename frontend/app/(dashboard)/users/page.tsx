"use client"

import { useMemo, useState } from "react"
import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { shortAgo } from "@/lib/fleet"
import { useAuth } from "@/lib/auth"
import type { AdminUser } from "@/lib/types"
import { Banner, Confirm, Empty, Field, Filter, Modal, PageHead, Table, Tag, type Sort } from "@/components/ld"

const CRUMBS = [
  { label: "settings", href: "/settings" },
  { label: "access", href: "/settings?section=access" },
]

/**
 * Users — the accounts that can sign in, and which of them are
 * superusers. Administrators only; everyone else is told where the gate
 * is rather than shown an empty table.
 */
export default function UsersPage() {
  const { user: currentUser, loading: authLoading } = useAuth()
  const [q, setQ] = useState("")
  const [status, setStatus] = useState("all")
  const [role, setRole] = useState("all")
  const [sort, setSort] = useState<Sort>({ k: "email", dir: 1 })

  const [createOpen, setCreateOpen] = useState(false)
  const [editOpen, setEditOpen] = useState(false)
  const [resetOpen, setResetOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [selectedUser, setSelectedUser] = useState<AdminUser | null>(null)

  // Create form
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [confirmPassword, setConfirmPassword] = useState("")
  const [isSuperuser, setIsSuperuser] = useState(false)

  // Edit form
  const [editEmail, setEditEmail] = useState("")
  const [editIsActive, setEditIsActive] = useState(true)
  const [editIsSuperuser, setEditIsSuperuser] = useState(false)

  // Reset password form
  const [newPassword, setNewPassword] = useState("")
  const [confirmNewPassword, setConfirmNewPassword] = useState("")

  // Client-side validation error (not API)
  const [validationError, setValidationError] = useState<string | null>(null)

  const { data: users, isLoading, error } = useQuery<AdminUser[]>({
    queryKey: ["admin-users"],
    queryFn: () => apiFetch<AdminUser[]>("/api/admin/users"),
    enabled: !!currentUser?.is_superuser,
  })

  const all = useMemo(() => users ?? [], [users])
  const rows = useMemo(() => {
    const ql = q.trim().toLowerCase()
    const r = all.filter(
      (u) =>
        (status === "all" || (status === "active") === u.is_active) &&
        (role === "all" || (role === "superuser") === u.is_superuser) &&
        (!ql || u.email.toLowerCase().includes(ql)),
    )
    const key: Record<string, (u: AdminUser) => string | number> = {
      email: (u) => u.email.toLowerCase(),
      status: (u) => (u.is_active ? 1 : 0),
      role: (u) => (u.is_superuser ? 1 : 0),
      created: (u) => u.created_at,
    }
    const f = key[sort.k] ?? key.email
    return r.sort((a, b) => {
      const x = f(a)
      const y = f(b)
      return (x > y ? 1 : x < y ? -1 : 0) * sort.dir
    })
  }, [all, q, status, role, sort])

  const createMutation = useApiMutation({
    mutationFn: (data: { email: string; password: string; is_superuser: boolean }) => apiFetch("/api/admin/users", { method: "POST", body: JSON.stringify(data) }),
    invalidateKeys: [["admin-users"]],
    onSuccess: () => {
      setCreateOpen(false)
      resetCreateForm()
    },
  })

  const editMutation = useApiMutation({
    mutationFn: ({ userId, ...data }: { userId: number; email: string; is_active: boolean; is_superuser: boolean }) =>
      apiFetch(`/api/admin/users/${userId}`, { method: "PATCH", body: JSON.stringify(data) }),
    invalidateKeys: [["admin-users"]],
    onSuccess: () => setEditOpen(false),
  })

  const resetPasswordMutation = useApiMutation({
    mutationFn: ({ userId, password }: { userId: number; password: string }) =>
      apiFetch(`/api/admin/users/${userId}/reset-password`, { method: "POST", body: JSON.stringify({ password }) }),
    invalidateKeys: [["admin-users"]],
    onSuccess: () => setResetOpen(false),
  })

  const deleteMutation = useApiMutation({
    mutationFn: (userId: number) => apiFetch(`/api/admin/users/${userId}`, { method: "DELETE" }),
    invalidateKeys: [["admin-users"]],
    onSuccess: () => setDeleteOpen(false),
  })

  if (authLoading) return null

  if (!currentUser?.is_superuser) {
    return (
      <>
        <PageHead crumbs={CRUMBS} title="Users" sub="Accounts and superuser status." />
        <Empty
          title="Administrators only"
          note="Only a superuser can manage accounts. Ask one to make the change, or to make you one."
          action={
            <Link href="/settings?section=access" className="btn btn-sm hover:no-underline">
              Settings › Access →
            </Link>
          }
        />
      </>
    )
  }

  function resetCreateForm() {
    setEmail("")
    setPassword("")
    setConfirmPassword("")
    setIsSuperuser(false)
    setValidationError(null)
    createMutation.reset()
  }

  function openCreate() {
    resetCreateForm()
    setCreateOpen(true)
  }

  function openEdit(u: AdminUser) {
    setSelectedUser(u)
    setEditEmail(u.email)
    setEditIsActive(u.is_active)
    setEditIsSuperuser(u.is_superuser)
    editMutation.reset()
    setEditOpen(true)
  }

  function openReset(u: AdminUser) {
    setSelectedUser(u)
    setNewPassword("")
    setConfirmNewPassword("")
    setValidationError(null)
    resetPasswordMutation.reset()
    setResetOpen(true)
  }

  function openDelete(u: AdminUser) {
    setSelectedUser(u)
    deleteMutation.reset()
    setDeleteOpen(true)
  }

  function handleCreate(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setValidationError(null)
    if (password !== confirmPassword) {
      setValidationError("Passwords do not match")
      return
    }
    createMutation.mutate({ email, password, is_superuser: isSuperuser })
  }

  function handleEdit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!selectedUser) return
    editMutation.mutate({ userId: selectedUser.id, email: editEmail, is_active: editIsActive, is_superuser: editIsSuperuser })
  }

  function handleResetPassword(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!selectedUser) return
    setValidationError(null)
    if (newPassword !== confirmNewPassword) {
      setValidationError("Passwords do not match")
      return
    }
    resetPasswordMutation.mutate({ userId: selectedUser.id, password: newPassword })
  }

  const formError = validationError || createMutation.error?.message || null
  const resetFormError = validationError || resetPasswordMutation.error?.message || null
  const isSelf = (u: AdminUser) => u.id === currentUser?.id

  return (
    <>
      <PageHead
        crumbs={CRUMBS}
        title={
          <>
            Users <span className="mono num text-[12.5px] font-normal text-text-faint">{all.length}</span>
          </>
        }
        sub="Accounts that can sign in. A superuser manages accounts, integrations and settings; everyone else operates the fleet."
        actions={
          <button type="button" className="btn btn-sm btn-primary" onClick={openCreate}>
            New user…
          </button>
        }
      >
        <div className="flex flex-wrap items-center gap-[7px]">
          <input className="inp mono" style={{ width: 200, fontSize: 11.5, padding: "4px 8px" }} placeholder="Search by email…" aria-label="search users" value={q} onChange={(e) => setQ(e.target.value)} />
          <Filter
            label="status"
            value={status}
            onChange={setStatus}
            options={[
              { k: "active", label: "active", n: all.filter((u) => u.is_active).length },
              { k: "inactive", label: "inactive", n: all.filter((u) => !u.is_active).length },
            ]}
          />
          <Filter
            label="role"
            value={role}
            onChange={setRole}
            options={[
              { k: "superuser", label: "superuser", n: all.filter((u) => u.is_superuser).length },
              { k: "user", label: "user", n: all.filter((u) => !u.is_superuser).length },
            ]}
          />
        </div>
      </PageHead>

      {error && (
        <Banner tone="danger" flush>
          Could not load users: {error.message}
        </Banner>
      )}

      <Table<AdminUser>
        cols={[
          {
            k: "email",
            label: "email",
            w: "minmax(220px,1.4fr)",
            cell: (u) => (
              <span className="flex items-center gap-1.5">
                <span className="mono trunc font-medium text-text">{u.email}</span>
                {isSelf(u) && <span className="tt text-text-faint">you</span>}
              </span>
            ),
          },
          { k: "status", label: "status", w: "96px", cell: (u) => <Tag tone={u.is_active ? "ok" : "danger"}>{u.is_active ? "active" : "inactive"}</Tag> },
          { k: "role", label: "role", w: "110px", cell: (u) => (u.is_superuser ? <Tag tone="hold">superuser</Tag> : <span className="text-text-faint">—</span>) },
          { k: "created", label: "created", w: "96px", cell: (u) => <span className="mono num text-[11px]" title={new Date(u.created_at).toLocaleString()}>{shortAgo(u.created_at)} ago</span> },
          {
            k: "actions",
            label: "",
            w: "230px",
            right: true,
            sortable: false,
            cell: (u) => (
              <span className="flex gap-0.5">
                <button type="button" className="btn btn-sm btn-ghost" onClick={() => openEdit(u)}>
                  edit
                </button>
                <button type="button" className="btn btn-sm btn-ghost" onClick={() => openReset(u)}>
                  reset password
                </button>
                <button type="button" className="btn btn-sm btn-ghost text-danger" disabled={isSelf(u)} title={isSelf(u) ? "you cannot delete your own account" : undefined} onClick={() => openDelete(u)}>
                  delete
                </button>
              </span>
            ),
          },
        ]}
        rows={rows}
        keyOf={(u) => u.id}
        sort={sort}
        onSort={(k) => setSort((s) => ({ k, dir: s.k === k ? ((-s.dir) as 1 | -1) : 1 }))}
        loading={isLoading}
        empty={all.length === 0 ? "No users." : "No user matches."}
      />

      {createOpen && (
        <Modal
          title="New user"
          w={460}
          onClose={() => {
            setCreateOpen(false)
            resetCreateForm()
          }}
          onSubmit={handleCreate}
          footer={
            <>
              <button type="button" className="btn ml-auto" onClick={() => setCreateOpen(false)}>
                Cancel
              </button>
              <button type="submit" className="btn btn-primary" disabled={createMutation.isPending}>
                {createMutation.isPending ? "Creating…" : "Create user"}
              </button>
            </>
          }
        >
          <Field label="email" htmlFor="create-email">
            <input id="create-email" type="email" className="inp mono" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="off" />
          </Field>
          <div className="grid gap-[11px]" style={{ gridTemplateColumns: "1fr 1fr" }}>
            <Field label="password" htmlFor="create-password">
              <input id="create-password" type="password" className="inp mono" value={password} onChange={(e) => setPassword(e.target.value)} required autoComplete="new-password" />
            </Field>
            <Field label="confirm password" htmlFor="create-confirm">
              <input id="create-confirm" type="password" className="inp mono" value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} required autoComplete="new-password" />
            </Field>
          </div>
          <label className="flex items-center gap-2 text-xs text-text">
            <input id="create-superuser" type="checkbox" checked={isSuperuser} onChange={(e) => setIsSuperuser(e.target.checked)} />
            superuser — manages accounts, integrations and settings
          </label>
          {formError && <Banner tone="danger">{formError}</Banner>}
        </Modal>
      )}

      {editOpen && selectedUser && (
        <Modal
          title="Edit user"
          meta={selectedUser.email}
          w={460}
          onClose={() => setEditOpen(false)}
          onSubmit={handleEdit}
          footer={
            <>
              <button type="button" className="btn ml-auto" onClick={() => setEditOpen(false)}>
                Cancel
              </button>
              <button type="submit" className="btn btn-primary" disabled={editMutation.isPending}>
                {editMutation.isPending ? "Saving…" : "Save changes"}
              </button>
            </>
          }
        >
          <Field label="email" htmlFor="edit-email">
            <input id="edit-email" type="email" className="inp mono" value={editEmail} onChange={(e) => setEditEmail(e.target.value)} required />
          </Field>
          <label className="flex items-center gap-2 text-xs text-text">
            <input id="edit-active" type="checkbox" checked={editIsActive} onChange={(e) => setEditIsActive(e.target.checked)} />
            active — an inactive account cannot sign in
          </label>
          <label className="flex items-center gap-2 text-xs text-text">
            <input id="edit-superuser" type="checkbox" checked={editIsSuperuser} onChange={(e) => setEditIsSuperuser(e.target.checked)} disabled={isSelf(selectedUser)} />
            superuser{isSelf(selectedUser) ? " — you cannot change your own role" : ""}
          </label>
          {editMutation.error && <Banner tone="danger">{editMutation.error.message}</Banner>}
        </Modal>
      )}

      {resetOpen && selectedUser && (
        <Modal
          title="Reset password"
          meta={selectedUser.email}
          w={460}
          onClose={() => setResetOpen(false)}
          onSubmit={handleResetPassword}
          footer={
            <>
              <span className="tt mr-auto">their sessions stay signed in</span>
              <button type="button" className="btn" onClick={() => setResetOpen(false)}>
                Cancel
              </button>
              <button type="submit" className="btn btn-primary" disabled={resetPasswordMutation.isPending}>
                {resetPasswordMutation.isPending ? "Resetting…" : "Reset password"}
              </button>
            </>
          }
        >
          <div className="grid gap-[11px]" style={{ gridTemplateColumns: "1fr 1fr" }}>
            <Field label="new password" htmlFor="reset-password">
              <input id="reset-password" type="password" className="inp mono" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} required autoComplete="new-password" />
            </Field>
            <Field label="confirm password" htmlFor="reset-confirm">
              <input id="reset-confirm" type="password" className="inp mono" value={confirmNewPassword} onChange={(e) => setConfirmNewPassword(e.target.value)} required autoComplete="new-password" />
            </Field>
          </div>
          {resetFormError && <Banner tone="danger">{resetFormError}</Banner>}
        </Modal>
      )}

      {selectedUser && (
        <Confirm
          open={deleteOpen}
          onOpenChange={(open) => !open && setDeleteOpen(false)}
          title="Delete user"
          description={`${selectedUser.email} loses access immediately. This cannot be undone.${deleteMutation.error ? ` ${deleteMutation.error.message}` : ""}`}
          confirmLabel="Delete"
          variant="destructive"
          loading={deleteMutation.isPending}
          onConfirm={() => deleteMutation.mutate(selectedUser.id)}
        />
      )}
    </>
  )
}
