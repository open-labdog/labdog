"use client"

import { useMemo, useState } from "react"
import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { shortAgo } from "@/lib/fleet"
import { can, ROLE_INFO, useAuth, type Role } from "@/lib/auth"
import type { AdminUser } from "@/lib/types"
import { userPanels } from "@/lib/extensions"
import { Banner, Confirm, Empty, Field, Filter, Modal, PageHead, Table, Tag, type Sort } from "@/components/ld"

const CRUMBS = [
  { label: "settings", href: "/settings" },
  { label: "access", href: "/settings?section=access" },
]

const ROLES: Role[] = ["viewer", "operator", "admin"]
const ROLE_TONE: Record<Role, "hold" | "accent" | "idle"> = { admin: "hold", operator: "accent", viewer: "idle" }

function RoleTag({ role }: { role: AdminUser["role"] }) {
  if (!role) return <span className="text-text-faint" title="signs in, can do nothing">no role</span>
  return <Tag tone={ROLE_TONE[role]}>{ROLE_INFO[role].label}</Tag>
}

/** One radio per role, each with what it can do, so nobody picks blind. */
function RolePicker({ name, value, onChange, disabled }: { name: string; value: Role; onChange: (r: Role) => void; disabled?: boolean }) {
  return (
    <fieldset className="grid gap-1.5" disabled={disabled}>
      <legend className="tt mb-1">role</legend>
      {ROLES.map((r) => (
        <label key={r} className="flex items-start gap-2 text-xs text-text">
          <input type="radio" name={name} value={r} checked={value === r} onChange={() => onChange(r)} className="mt-0.5" />
          <span>
            <span className="font-medium">{ROLE_INFO[r].label}</span> <span className="text-text-faint">— {ROLE_INFO[r].note}</span>
          </span>
        </label>
      ))}
    </fieldset>
  )
}

/**
 * Users — the accounts that can sign in and the role each one holds.
 * Needs `users.manage` (admins); everyone else is told where the gate
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
  const [displayName, setDisplayName] = useState("")
  const [createRole, setCreateRole] = useState<Role>("viewer")
  const [mustChange, setMustChange] = useState(true)

  // Edit form
  const [editEmail, setEditEmail] = useState("")
  const [editDisplayName, setEditDisplayName] = useState("")
  const [editIsActive, setEditIsActive] = useState(true)
  // null while the account has no role and the admin has not picked one.
  const [editRole, setEditRole] = useState<Role | null>(null)

  // Reset password form
  const [newPassword, setNewPassword] = useState("")
  const [confirmNewPassword, setConfirmNewPassword] = useState("")
  const [resetMustChange, setResetMustChange] = useState(true)

  // Client-side validation error (not API)
  const [validationError, setValidationError] = useState<string | null>(null)

  const canManage = can(currentUser, "users.manage")
  const { data: users, isLoading, error } = useQuery<AdminUser[]>({
    queryKey: ["admin-users"],
    queryFn: () => apiFetch<AdminUser[]>("/api/admin/users"),
    enabled: canManage,
  })

  const all = useMemo(() => users ?? [], [users])
  const rows = useMemo(() => {
    const ql = q.trim().toLowerCase()
    const r = all.filter(
      (u) =>
        (status === "all" || (status === "active") === u.is_active) &&
        (role === "all" || (u.role ?? "none") === role) &&
        (!ql || u.email.toLowerCase().includes(ql) || (u.display_name ?? "").toLowerCase().includes(ql)),
    )
    const key: Record<string, (u: AdminUser) => string | number> = {
      email: (u) => u.email.toLowerCase(),
      status: (u) => (u.is_active ? 1 : 0),
      role: (u) => (u.role ? ROLES.indexOf(u.role) : -1),
      login: (u) => u.last_login_at ?? "",
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
    mutationFn: (data: { email: string; password: string; display_name: string | null; role: Role; must_change_password: boolean }) => apiFetch("/api/admin/users", { method: "POST", body: JSON.stringify(data) }),
    invalidateKeys: [["admin-users"]],
    onSuccess: () => {
      setCreateOpen(false)
      resetCreateForm()
    },
  })

  const editMutation = useApiMutation({
    mutationFn: ({ userId, ...data }: { userId: number; email: string; display_name: string | null; is_active?: boolean; role?: Role }) =>
      apiFetch(`/api/admin/users/${userId}`, { method: "PATCH", body: JSON.stringify(data) }),
    invalidateKeys: [["admin-users"]],
    onSuccess: () => setEditOpen(false),
  })

  const resetPasswordMutation = useApiMutation({
    mutationFn: ({ userId, ...data }: { userId: number; password: string; must_change_password: boolean }) =>
      apiFetch(`/api/admin/users/${userId}/reset-password`, { method: "POST", body: JSON.stringify(data) }),
    invalidateKeys: [["admin-users"]],
    onSuccess: () => setResetOpen(false),
  })

  const deleteMutation = useApiMutation({
    mutationFn: (userId: number) => apiFetch(`/api/admin/users/${userId}`, { method: "DELETE" }),
    invalidateKeys: [["admin-users"]],
    onSuccess: () => setDeleteOpen(false),
  })

  if (authLoading) return null

  if (!canManage) {
    return (
      <>
        <PageHead crumbs={CRUMBS} title="Users" sub="Accounts and their roles." />
        <Empty
          title="Administrators only"
          note="Only an admin can manage accounts. Ask one to make the change, or to change your role."
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
    setDisplayName("")
    setCreateRole("viewer")
    setMustChange(true)
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
    setEditDisplayName(u.display_name ?? "")
    setEditIsActive(u.is_active)
    setEditRole(u.role)
    editMutation.reset()
    setEditOpen(true)
  }

  function openReset(u: AdminUser) {
    setSelectedUser(u)
    setNewPassword("")
    setConfirmNewPassword("")
    setResetMustChange(u.id !== currentUser?.id)
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
    createMutation.mutate({ email, password, display_name: displayName.trim() || null, role: createRole, must_change_password: mustChange })
  }

  function handleEdit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!selectedUser) return
    // Your own role and active flag are not yours to change; the API
    // refuses it, so they are not sent at all.
    const self = selectedUser.id === currentUser?.id
    editMutation.mutate({
      userId: selectedUser.id,
      email: editEmail,
      display_name: editDisplayName.trim() || null,
      ...(self ? {} : { is_active: editIsActive, ...(editRole ? { role: editRole } : {}) }),
    })
  }

  function handleResetPassword(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!selectedUser) return
    setValidationError(null)
    if (newPassword !== confirmNewPassword) {
      setValidationError("Passwords do not match")
      return
    }
    resetPasswordMutation.mutate({ userId: selectedUser.id, password: newPassword, must_change_password: resetMustChange })
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
        sub="Accounts that can sign in. A viewer sees everything and changes nothing, an operator runs the fleet, an admin also manages integrations, settings and accounts."
        actions={
          <button type="button" className="btn btn-sm btn-primary" onClick={openCreate}>
            New user…
          </button>
        }
      >
        <div className="flex flex-wrap items-center gap-[7px]">
          <input className="inp mono" style={{ width: 200, fontSize: 11.5, padding: "4px 8px" }} placeholder="Search by name or email…" aria-label="search users" value={q} onChange={(e) => setQ(e.target.value)} />
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
              ...ROLES.map((r) => ({ k: r, label: r, n: all.filter((u) => u.role === r).length })),
              ...(all.some((u) => !u.role) ? [{ k: "none", label: "no role", n: all.filter((u) => !u.role).length }] : []),
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
            label: "user",
            w: "minmax(220px,1.4fr)",
            cell: (u) => (
              <span className="flex min-w-0 items-center gap-1.5">
                <span className="flex min-w-0 flex-col">
                  {u.display_name && <span className="trunc font-medium text-text">{u.display_name}</span>}
                  <span className={`mono trunc ${u.display_name ? "text-[11px] text-text-faint" : "font-medium text-text"}`}>{u.email}</span>
                </span>
                {isSelf(u) && <span className="tt text-text-faint">you</span>}
                {u.must_change_password && <Tag tone="warn">must change password</Tag>}
              </span>
            ),
          },
          { k: "status", label: "status", w: "96px", cell: (u) => <Tag tone={u.is_active ? "ok" : "danger"}>{u.is_active ? "active" : "inactive"}</Tag> },
          { k: "role", label: "role", w: "100px", cell: (u) => <RoleTag role={u.role} /> },
          {
            k: "login",
            label: "last sign-in",
            w: "104px",
            cell: (u) =>
              u.last_login_at ? (
                <span className="mono num text-[11px]" title={new Date(u.last_login_at).toLocaleString()}>
                  {shortAgo(u.last_login_at)} ago
                </span>
              ) : (
                <span className="text-text-faint">never</span>
              ),
          },
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
          <div className="grid gap-[11px]" style={{ gridTemplateColumns: "1fr 1fr" }}>
            <Field label="email" htmlFor="create-email">
              <input id="create-email" type="email" className="inp mono" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="off" />
            </Field>
            <Field label="display name" htmlFor="create-name" hint="optional">
              <input id="create-name" className="inp" value={displayName} onChange={(e) => setDisplayName(e.target.value)} maxLength={255} autoComplete="off" />
            </Field>
          </div>
          <div className="grid gap-[11px]" style={{ gridTemplateColumns: "1fr 1fr" }}>
            <Field label="password" htmlFor="create-password">
              <input id="create-password" type="password" className="inp mono" value={password} onChange={(e) => setPassword(e.target.value)} required autoComplete="new-password" />
            </Field>
            <Field label="confirm password" htmlFor="create-confirm">
              <input id="create-confirm" type="password" className="inp mono" value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} required autoComplete="new-password" />
            </Field>
          </div>
          <RolePicker name="create-role" value={createRole} onChange={setCreateRole} />
          <label className="flex items-center gap-2 text-xs text-text">
            <input id="create-must-change" type="checkbox" checked={mustChange} onChange={(e) => setMustChange(e.target.checked)} />
            must choose a new password at first sign-in
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
          <div className="grid gap-[11px]" style={{ gridTemplateColumns: "1fr 1fr" }}>
            <Field label="email" htmlFor="edit-email">
              <input id="edit-email" type="email" className="inp mono" value={editEmail} onChange={(e) => setEditEmail(e.target.value)} required />
            </Field>
            <Field label="display name" htmlFor="edit-name" hint="optional">
              <input id="edit-name" className="inp" value={editDisplayName} onChange={(e) => setEditDisplayName(e.target.value)} maxLength={255} />
            </Field>
          </div>
          {editRole === null && !isSelf(selectedUser) && (
            <Banner tone="warn">This account has no role, so it can sign in but do nothing. Pick one to give it access.</Banner>
          )}
          <RolePicker name="edit-role" value={editRole ?? ("" as Role)} onChange={setEditRole} disabled={isSelf(selectedUser)} />
          <label className="flex items-center gap-2 text-xs text-text">
            <input id="edit-active" type="checkbox" checked={editIsActive} onChange={(e) => setEditIsActive(e.target.checked)} disabled={isSelf(selectedUser)} />
            active — an inactive account cannot sign in
          </label>
          {isSelf(selectedUser) && <p className="tt">You cannot change your own role or disable yourself. Ask another admin.</p>}
          {!isSelf(selectedUser) && editRole !== selectedUser.role && selectedUser.role && (
            <p className="tt">Changing the role signs them out everywhere.</p>
          )}
          {/* Panels an extension adds (Enterprise: directory accounts). They
              sit inside this form, so their buttons are type="button". */}
          {userPanels
            .filter((x) => !x.permission || can(currentUser, x.permission))
            .map(({ key, Component }) => (
              <Component key={key} user={selectedUser} />
            ))}
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
              <span className="tt mr-auto">{isSelf(selectedUser) ? "signs you out everywhere" : "signs them out everywhere"}</span>
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
          {!isSelf(selectedUser) && (
            <label className="flex items-center gap-2 text-xs text-text">
              <input id="reset-must-change" type="checkbox" checked={resetMustChange} onChange={(e) => setResetMustChange(e.target.checked)} />
              must choose a new password at next sign-in
            </label>
          )}
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
