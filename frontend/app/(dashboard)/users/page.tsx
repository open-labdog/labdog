"use client"

import { useState } from "react"
import Link from "next/link"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { apiFetch } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import type { AdminUser } from "@/lib/types"

export default function UsersPage() {
  const { user: currentUser, loading: authLoading } = useAuth()
  const queryClient = useQueryClient()

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

  const [formError, setFormError] = useState<string | null>(null)
  const [formLoading, setFormLoading] = useState(false)

  const { data: users, isLoading, error } = useQuery<AdminUser[]>({
    queryKey: ["admin-users"],
    queryFn: () => apiFetch<AdminUser[]>("/api/admin/users"),
    enabled: !!currentUser?.is_superuser,
  })

  if (authLoading) {
    return <div className="text-slate-400 py-8 text-center">Loading...</div>
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
    setFormError(null)
  }

  function openEditDialog(u: AdminUser) {
    setSelectedUser(u)
    setEditEmail(u.email)
    setEditIsActive(u.is_active)
    setEditIsSuperuser(u.is_superuser)
    setFormError(null)
    setEditDialogOpen(true)
  }

  function openResetDialog(u: AdminUser) {
    setSelectedUser(u)
    setNewPassword("")
    setConfirmNewPassword("")
    setFormError(null)
    setResetDialogOpen(true)
  }

  function openDeleteDialog(u: AdminUser) {
    setSelectedUser(u)
    setFormError(null)
    setDeleteDialogOpen(true)
  }

  async function handleCreate(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setFormError(null)
    if (password !== confirmPassword) {
      setFormError("Passwords do not match")
      return
    }
    setFormLoading(true)
    try {
      await apiFetch("/api/admin/users", {
        method: "POST",
        body: JSON.stringify({ email, password, is_superuser: isSuperuser }),
      })
      await queryClient.invalidateQueries({ queryKey: ["admin-users"] })
      setCreateDialogOpen(false)
      resetCreateForm()
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to create user")
    } finally {
      setFormLoading(false)
    }
  }

  async function handleEdit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!selectedUser) return
    setFormError(null)
    setFormLoading(true)
    try {
      await apiFetch(`/api/admin/users/${selectedUser.id}`, {
        method: "PATCH",
        body: JSON.stringify({ email: editEmail, is_active: editIsActive, is_superuser: editIsSuperuser }),
      })
      await queryClient.invalidateQueries({ queryKey: ["admin-users"] })
      setEditDialogOpen(false)
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to update user")
    } finally {
      setFormLoading(false)
    }
  }

  async function handleResetPassword(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!selectedUser) return
    setFormError(null)
    if (newPassword !== confirmNewPassword) {
      setFormError("Passwords do not match")
      return
    }
    setFormLoading(true)
    try {
      await apiFetch(`/api/admin/users/${selectedUser.id}/reset-password`, {
        method: "POST",
        body: JSON.stringify({ password: newPassword }),
      })
      await queryClient.invalidateQueries({ queryKey: ["admin-users"] })
      setResetDialogOpen(false)
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to reset password")
    } finally {
      setFormLoading(false)
    }
  }

  async function handleDelete() {
    if (!selectedUser) return
    setFormError(null)
    setFormLoading(true)
    try {
      await apiFetch(`/api/admin/users/${selectedUser.id}`, { method: "DELETE" })
      await queryClient.invalidateQueries({ queryKey: ["admin-users"] })
      setDeleteDialogOpen(false)
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to delete user")
    } finally {
      setFormLoading(false)
    }
  }

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
          <DialogTrigger>
            <Button onClick={() => { resetCreateForm(); setCreateDialogOpen(true) }}>New User</Button>
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
              <div className="flex gap-3 pt-2">
                <Button type="submit" disabled={formLoading}>{formLoading ? "Creating..." : "Create User"}</Button>
                <Button type="button" variant="outline" onClick={() => setCreateDialogOpen(false)}>Cancel</Button>
              </div>
            </form>
          </DialogContent>
        </Dialog>
      </div>

      {isLoading && <div className="text-slate-400 py-8 text-center">Loading users...</div>}
      {error && <div className="text-red-400 py-8 text-center">Failed to load users</div>}

      {!isLoading && !error && users && users.length === 0 && (
        <div className="text-slate-400 py-8 text-center">No users found.</div>
      )}

      {!isLoading && !error && users && users.length > 0 && (
        <div className="rounded-lg border border-slate-700 bg-slate-900">
          <Table>
            <TableHeader>
              <TableRow className="border-slate-700">
                <TableHead>Email</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Superuser</TableHead>
                <TableHead>Created</TableHead>
                <TableHead>Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {users.map((u) => (
                <TableRow key={u.id} className="border-slate-700">
                  <TableCell className="font-medium text-white">{u.email}</TableCell>
                  <TableCell>
                    <Badge className={u.is_active ? "bg-green-600 text-white" : "bg-red-600 text-white"}>
                      {u.is_active ? "Active" : "Inactive"}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    {u.is_superuser ? (
                      <Badge className="bg-purple-600 text-white">Superuser</Badge>
                    ) : (
                      <span className="text-slate-500">—</span>
                    )}
                  </TableCell>
                  <TableCell className="text-slate-400">{new Date(u.created_at).toLocaleDateString()}</TableCell>
                  <TableCell>
                    <div className="flex gap-2">
                      <Button variant="ghost" size="sm" onClick={() => openEditDialog(u)}>Edit</Button>
                      <Button variant="outline" size="sm" onClick={() => openResetDialog(u)}>Reset Password</Button>
                      <Button variant="destructive" size="sm" onClick={() => openDeleteDialog(u)}>Delete</Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
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
            {formError && <p className="text-sm text-red-400">{formError}</p>}
            <div className="flex gap-3 pt-2">
              <Button type="submit" disabled={formLoading}>{formLoading ? "Saving..." : "Save Changes"}</Button>
              <Button type="button" variant="outline" onClick={() => setEditDialogOpen(false)}>Cancel</Button>
            </div>
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
            {formError && <p className="text-sm text-red-400">{formError}</p>}
            <div className="flex gap-3 pt-2">
              <Button type="submit" disabled={formLoading}>{formLoading ? "Resetting..." : "Reset Password"}</Button>
              <Button type="button" variant="outline" onClick={() => setResetDialogOpen(false)}>Cancel</Button>
            </div>
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
          {formError && <p className="text-sm text-red-400 mt-2">{formError}</p>}
          <div className="flex gap-3 pt-4">
            <Button variant="destructive" onClick={handleDelete} disabled={formLoading}>
              {formLoading ? "Deleting..." : "Delete"}
            </Button>
            <Button variant="outline" onClick={() => setDeleteDialogOpen(false)}>Cancel</Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
