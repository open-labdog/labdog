"use client"

import { useState } from "react"
import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { DataTable } from "@/components/ui/data-table"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { useDelayedLoading } from "@/lib/utils"
import { TableSkeleton } from "@/components/ui/skeleton"
import { useAuth } from "@/lib/auth"
import type { AdminUser } from "@/lib/types"

export default function UsersPage() {
  const { user: currentUser, loading: authLoading } = useAuth()

  const [createDialogOpen, setCreateDialogOpen] = useState(false)
  const [editDialogOpen, setEditDialogOpen] = useState(false)
  const [resetDialogOpen, setResetDialogOpen] = useState(false)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
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
  const showLoading = useDelayedLoading(isLoading)

  const createMutation = useApiMutation({
    mutationFn: (data: { email: string; password: string; is_superuser: boolean }) =>
      apiFetch("/api/admin/users", { method: "POST", body: JSON.stringify(data) }),
    invalidateKeys: [["admin-users"]],
    onSuccess: () => { setCreateDialogOpen(false); resetCreateForm() },
  })

  const editMutation = useApiMutation({
    mutationFn: ({ userId, ...data }: { userId: number; email: string; is_active: boolean; is_superuser: boolean }) =>
      apiFetch(`/api/admin/users/${userId}`, { method: "PATCH", body: JSON.stringify(data) }),
    invalidateKeys: [["admin-users"]],
    onSuccess: () => setEditDialogOpen(false),
  })

  const resetPasswordMutation = useApiMutation({
    mutationFn: ({ userId, password }: { userId: number; password: string }) =>
      apiFetch(`/api/admin/users/${userId}/reset-password`, { method: "POST", body: JSON.stringify({ password }) }),
    invalidateKeys: [["admin-users"]],
    onSuccess: () => setResetDialogOpen(false),
  })

  const deleteMutation = useApiMutation({
    mutationFn: (userId: number) =>
      apiFetch(`/api/admin/users/${userId}`, { method: "DELETE" }),
    invalidateKeys: [["admin-users"]],
    onSuccess: () => setDeleteDialogOpen(false),
  })

  if (authLoading) {
    return (
      <div className="space-y-6">
        <TableSkeleton rows={5} columns={3} />
      </div>
    )
  }

  if (!currentUser?.is_superuser) {
    return (
      <div className="space-y-6">
        <div className="text-center py-12">
          <p className="text-slate-400">Access denied. Only administrators can manage users.</p>
          <Link href="/dashboard" className="text-blue-400 hover:underline text-sm mt-2 inline-block">
            Back to Dashboard
          </Link>
        </div>
      </div>
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

  function openEditDialog(u: AdminUser) {
    setSelectedUser(u)
    setEditEmail(u.email)
    setEditIsActive(u.is_active)
    setEditIsSuperuser(u.is_superuser)
    editMutation.reset()
    setEditDialogOpen(true)
  }

  function openResetDialog(u: AdminUser) {
    setSelectedUser(u)
    setNewPassword("")
    setConfirmNewPassword("")
    setValidationError(null)
    resetPasswordMutation.reset()
    setResetDialogOpen(true)
  }

  function openDeleteDialog(u: AdminUser) {
    setSelectedUser(u)
    deleteMutation.reset()
    setDeleteDialogOpen(true)
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

  function handleDelete() {
    if (!selectedUser) return
    deleteMutation.mutate(selectedUser.id)
  }

  const formError = validationError || createMutation.error?.message || null
  const editFormError = editMutation.error?.message || null
  const resetFormError = validationError || resetPasswordMutation.error?.message || null
  const deleteFormError = deleteMutation.error?.message || null

  return (
    <div className="space-y-6">
      <Breadcrumb items={[{ label: "Users" }]} />
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Users</h1>
          <p className="text-slate-400 text-sm mt-1">Manage user accounts</p>
        </div>
        <Dialog open={createDialogOpen} onOpenChange={(open) => {
          setCreateDialogOpen(open)
          if (!open) resetCreateForm()
        }}>
          <DialogTrigger render={<Button />} onClick={() => { resetCreateForm(); setCreateDialogOpen(true) }}>
            New User
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Create User</DialogTitle>
            </DialogHeader>
            <form onSubmit={handleCreate} className="space-y-4 mt-2">
              <div className="space-y-2">
                <Label htmlFor="create-email">Email</Label>
                <Input id="create-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
              </div>
              <div className="space-y-2">
                <Label htmlFor="create-password">Password</Label>
                <Input id="create-password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
              </div>
              <div className="space-y-2">
                <Label htmlFor="create-confirm">Confirm Password</Label>
                <Input id="create-confirm" type="password" value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} required />
              </div>
              <div className="flex items-center gap-2">
                <input id="create-superuser" type="checkbox" checked={isSuperuser} onChange={(e) => setIsSuperuser(e.target.checked)} className="rounded border-input" />
                <Label htmlFor="create-superuser">Superuser</Label>
              </div>
              {formError && <p className="text-sm text-red-400">{formError}</p>}
              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => setCreateDialogOpen(false)}>Cancel</Button>
                <Button type="submit" disabled={createMutation.isPending}>{createMutation.isPending ? "Creating..." : "Create User"}</Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
      </div>

      {showLoading && <TableSkeleton rows={5} columns={3} />}
      {error && <div className="text-red-400 py-8 text-center">Failed to load users</div>}

      {!isLoading && !error && (
        <DataTable<AdminUser>
          tableId="admin-users"
          data={users}
          emptyMessage="No users found."
          getRowKey={(u) => u.id}
          columns={[
            {
              key: "email",
              label: "Email",
              accessor: (u) => u.email,
              cell: (u) => <span className="font-medium text-white">{u.email}</span>,
              defaultWidth: 240,
              filter: { type: "text", placeholder: "e.g. @company.com" },
            },
            {
              key: "is_active",
              label: "Status",
              accessor: (u) => u.is_active,
              cell: (u) => (
                <Badge className={u.is_active ? "bg-green-600 text-white" : "bg-red-600 text-white"}>
                  {u.is_active ? "Active" : "Inactive"}
                </Badge>
              ),
              defaultWidth: 100,
              filter: { type: "boolean", trueLabel: "Active", falseLabel: "Inactive" },
            },
            {
              key: "is_superuser",
              label: "Superuser",
              accessor: (u) => u.is_superuser,
              cell: (u) => u.is_superuser
                ? <Badge className="bg-purple-600 text-white">Superuser</Badge>
                : <span className="text-slate-500">—</span>,
              defaultWidth: 110,
              filter: { type: "boolean", trueLabel: "Yes", falseLabel: "No" },
            },
            {
              key: "created_at",
              label: "Created",
              accessor: (u) => u.created_at,
              cell: (u) => <span className="text-slate-400">{new Date(u.created_at).toLocaleDateString()}</span>,
              defaultWidth: 120,
              filter: { type: "dateRange" },
            },
            {
              key: "actions",
              label: "Actions",
              cell: (u) => (
                <div className="flex gap-2">
                  <Button variant="ghost" size="sm" onClick={() => openEditDialog(u)}>Edit</Button>
                  <Button variant="outline" size="sm" onClick={() => openResetDialog(u)}>Reset Password</Button>
                  <Button variant="destructive" size="sm" onClick={() => openDeleteDialog(u)}>Delete</Button>
                </div>
              ),
              defaultWidth: 260,
              resizable: false,
              sortable: false,
            },
          ]}
        />
      )}

      {/* Edit User Dialog */}
      <Dialog open={editDialogOpen} onOpenChange={(open) => { if (!open) setEditDialogOpen(false) }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit User</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleEdit} className="space-y-4 mt-2">
            <div className="space-y-2">
              <Label htmlFor="edit-email">Email</Label>
              <Input id="edit-email" type="email" value={editEmail} onChange={(e) => setEditEmail(e.target.value)} required />
            </div>
            <div className="flex items-center gap-2">
              <input id="edit-active" type="checkbox" checked={editIsActive} onChange={(e) => setEditIsActive(e.target.checked)} className="rounded border-input" />
              <Label htmlFor="edit-active">Active</Label>
            </div>
            <div className="flex items-center gap-2">
              <input id="edit-superuser" type="checkbox" checked={editIsSuperuser} onChange={(e) => setEditIsSuperuser(e.target.checked)} className="rounded border-input" />
              <Label htmlFor="edit-superuser">Superuser</Label>
            </div>
            {editFormError && <p className="text-sm text-red-400">{editFormError}</p>}
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setEditDialogOpen(false)}>Cancel</Button>
              <Button type="submit" disabled={editMutation.isPending}>{editMutation.isPending ? "Saving..." : "Save Changes"}</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Reset Password Dialog */}
      <Dialog open={resetDialogOpen} onOpenChange={(open) => { if (!open) setResetDialogOpen(false) }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reset Password — {selectedUser?.email}</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleResetPassword} className="space-y-4 mt-2">
            <div className="space-y-2">
              <Label htmlFor="reset-password">New Password</Label>
              <Input id="reset-password" type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} required />
            </div>
            <div className="space-y-2">
              <Label htmlFor="reset-confirm">Confirm Password</Label>
              <Input id="reset-confirm" type="password" value={confirmNewPassword} onChange={(e) => setConfirmNewPassword(e.target.value)} required />
            </div>
            {resetFormError && <p className="text-sm text-red-400">{resetFormError}</p>}
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setResetDialogOpen(false)}>Cancel</Button>
              <Button type="submit" disabled={resetPasswordMutation.isPending}>{resetPasswordMutation.isPending ? "Resetting..." : "Reset Password"}</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation Dialog */}
      <Dialog open={deleteDialogOpen} onOpenChange={(open) => { if (!open) setDeleteDialogOpen(false) }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete User</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-slate-400 mt-2">
            Are you sure you want to delete <span className="text-white font-medium">{selectedUser?.email}</span>? This action cannot be undone.
          </p>
          {deleteFormError && <p className="text-sm text-red-400 mt-2">{deleteFormError}</p>}
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteDialogOpen(false)}>Cancel</Button>
            <Button variant="destructive" onClick={handleDelete} disabled={deleteMutation.isPending}>
              {deleteMutation.isPending ? "Deleting..." : "Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
