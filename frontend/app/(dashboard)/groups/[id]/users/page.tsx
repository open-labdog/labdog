"use client"

import { useState, type FormEvent } from "react"
import { useParams } from "next/navigation"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { apiFetch } from "@/lib/api"
import { showError } from "@/lib/toast"
import { useDelayedLoading } from "@/lib/utils"
import { TableSkeleton } from "@/components/ui/skeleton"
import type { LinuxUser, LinuxGroup, HostGroup } from "@/lib/types"

function UserStateBadge({ state }: { state: string }) {
  return (
    <Badge className={state === "present" ? "bg-green-600 text-white" : "bg-red-600 text-white"}>
      {state.charAt(0).toUpperCase() + state.slice(1)}
    </Badge>
  )
}

export default function GroupUsersPage() {
  const params = useParams()
  const id = Number(params.id)
  const queryClient = useQueryClient()

  // Linux Users state
  const [userDialogOpen, setUserDialogOpen] = useState(false)
  const [editingUser, setEditingUser] = useState<LinuxUser | null>(null)
  const [userDeletingId, setUserDeletingId] = useState<number | null>(null)
  const [userFormError, setUserFormError] = useState<string | null>(null)
  const [userFormLoading, setUserFormLoading] = useState(false)

  // User form fields
  const [username, setUsername] = useState("")
  const [uid, setUid] = useState("")
  const [shell, setShell] = useState("/bin/bash")
  const [homeDir, setHomeDir] = useState("")
  const [userState, setUserState] = useState<"present" | "absent">("present")
  const [comment, setComment] = useState("")
  const [sudoRule, setSudoRule] = useState("")
  const [authorizedKeys, setAuthorizedKeys] = useState("")
  const [supplementaryGroups, setSupplementaryGroups] = useState("")
  const [userPriority, setUserPriority] = useState(100)

  const [confirmState, setConfirmState] = useState<{
    open: boolean; title: string; description: string; action: () => void | Promise<void>; loading?: boolean
  } | null>(null)

  // Linux Groups state
  const [groupDialogOpen, setGroupDialogOpen] = useState(false)
  const [editingGroup, setEditingGroup] = useState<LinuxGroup | null>(null)
  const [groupDeletingId, setGroupDeletingId] = useState<number | null>(null)
  const [groupFormError, setGroupFormError] = useState<string | null>(null)
  const [groupFormLoading, setGroupFormLoading] = useState(false)

  // Group form fields
  const [groupname, setGroupname] = useState("")
  const [gid, setGid] = useState("")
  const [groupState, setGroupState] = useState<"present" | "absent">("present")
  const [groupPriority, setGroupPriority] = useState(100)

  // Queries
  const { data: linuxUsers, isLoading: usersLoading, error: usersError } = useQuery<LinuxUser[]>({
    queryKey: ["linux-users", id],
    queryFn: () => apiFetch<LinuxUser[]>(`/api/groups/${id}/linux-users`),
    enabled: !!id,
  })
  const showUsersLoading = useDelayedLoading(usersLoading)

  const { data: linuxGroups, isLoading: groupsLoading, error: groupsError } = useQuery<LinuxGroup[]>({
    queryKey: ["linux-groups", id],
    queryFn: () => apiFetch<LinuxGroup[]>(`/api/groups/${id}/linux-groups`),
    enabled: !!id,
  })
  const showGroupsLoading = useDelayedLoading(groupsLoading)

  // --- Linux Users CRUD ---

  function openCreateUserDialog() {
    setEditingUser(null)
    setUsername("")
    setUid("")
    setShell("/bin/bash")
    setHomeDir("")
    setUserState("present")
    setComment("")
    setSudoRule("")
    setAuthorizedKeys("")
    setSupplementaryGroups("")
    setUserPriority(100)
    setUserFormError(null)
    setUserDialogOpen(true)
  }

  function openEditUserDialog(user: LinuxUser) {
    setEditingUser(user)
    setUsername(user.username)
    setUid(user.uid != null ? String(user.uid) : "")
    setShell(user.shell)
    setHomeDir(user.home_dir ?? "")
    setUserState(user.state)
    setComment(user.comment ?? "")
    setSudoRule(user.sudo_rule ?? "")
    setAuthorizedKeys(user.authorized_keys.join("\n"))
    setSupplementaryGroups(user.supplementary_groups.join(", "))
    setUserPriority(user.priority)
    setUserFormError(null)
    setUserDialogOpen(true)
  }

  async function handleUserSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setUserFormError(null)
    setUserFormLoading(true)

    const payload = {
      username,
      uid: uid ? Number(uid) : null,
      shell,
      home_dir: homeDir || null,
      state: userState,
      comment: comment || null,
      sudo_rule: sudoRule || null,
      authorized_keys: authorizedKeys
        .split("\n")
        .map((k) => k.trim())
        .filter(Boolean),
      supplementary_groups: supplementaryGroups
        .split(",")
        .map((g) => g.trim())
        .filter(Boolean),
      priority: userPriority,
    }

    try {
      if (editingUser) {
        await apiFetch(`/api/groups/${id}/linux-users/${editingUser.id}`, {
          method: "PUT",
          body: JSON.stringify(payload),
        })
      } else {
        await apiFetch(`/api/groups/${id}/linux-users`, {
          method: "POST",
          body: JSON.stringify(payload),
        })
      }
      await queryClient.invalidateQueries({ queryKey: ["linux-users", id] })
      setUserDialogOpen(false)
    } catch (err) {
      setUserFormError(err instanceof Error ? err.message : "Failed to save user")
    } finally {
      setUserFormLoading(false)
    }
  }

  function handleUserDelete(user: LinuxUser) {
    setConfirmState({
      open: true,
      title: "Delete Linux User",
      description: `Delete Linux user "${user.username}"? This action cannot be undone.`,
      action: async () => {
        setConfirmState((prev) => prev ? { ...prev, loading: true } : null)
        setUserDeletingId(user.id)
        try {
          await apiFetch(`/api/groups/${id}/linux-users/${user.id}`, { method: "DELETE" })
          await queryClient.invalidateQueries({ queryKey: ["linux-users", id] })
          setConfirmState(null)
        } catch (err) {
          showError(err instanceof Error ? err.message : "Delete failed")
          setConfirmState(null)
        } finally {
          setUserDeletingId(null)
        }
      },
    })
  }

  // --- Linux Groups CRUD ---

  function openCreateGroupDialog() {
    setEditingGroup(null)
    setGroupname("")
    setGid("")
    setGroupState("present")
    setGroupPriority(100)
    setGroupFormError(null)
    setGroupDialogOpen(true)
  }

  function openEditGroupDialog(group: LinuxGroup) {
    setEditingGroup(group)
    setGroupname(group.groupname)
    setGid(group.gid != null ? String(group.gid) : "")
    setGroupState(group.state)
    setGroupPriority(group.priority)
    setGroupFormError(null)
    setGroupDialogOpen(true)
  }

  async function handleGroupSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setGroupFormError(null)
    setGroupFormLoading(true)

    const payload = {
      groupname,
      gid: gid ? Number(gid) : null,
      state: groupState,
      priority: groupPriority,
    }

    try {
      if (editingGroup) {
        await apiFetch(`/api/groups/${id}/linux-groups/${editingGroup.id}`, {
          method: "PUT",
          body: JSON.stringify(payload),
        })
      } else {
        await apiFetch(`/api/groups/${id}/linux-groups`, {
          method: "POST",
          body: JSON.stringify(payload),
        })
      }
      await queryClient.invalidateQueries({ queryKey: ["linux-groups", id] })
      setGroupDialogOpen(false)
    } catch (err) {
      setGroupFormError(err instanceof Error ? err.message : "Failed to save group")
    } finally {
      setGroupFormLoading(false)
    }
  }

  function handleGroupDelete(group: LinuxGroup) {
    setConfirmState({
      open: true,
      title: "Delete Linux Group",
      description: `Delete Linux group "${group.groupname}"? This action cannot be undone.`,
      action: async () => {
        setConfirmState((prev) => prev ? { ...prev, loading: true } : null)
        setGroupDeletingId(group.id)
        try {
          await apiFetch(`/api/groups/${id}/linux-groups/${group.id}`, { method: "DELETE" })
          await queryClient.invalidateQueries({ queryKey: ["linux-groups", id] })
          setConfirmState(null)
        } catch (err) {
          showError(err instanceof Error ? err.message : "Delete failed")
          setConfirmState(null)
        } finally {
          setGroupDeletingId(null)
        }
      },
    })
  }

  const { data: group } = useQuery<HostGroup>({
    queryKey: ["group", id],
    queryFn: () => apiFetch<HostGroup>(`/api/groups/${id}`),
    enabled: !!id,
  })

  return (
    <div className="space-y-8">
      <Breadcrumb items={[{ label: "Groups", href: "/groups" }, { label: group?.name ?? "Group", href: `/groups/${id}` }, { label: "Users" }]} />
      {/* Linux Users Section */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-white">Linux Users</h1>
            <p className="text-slate-400 text-sm mt-1">Group ID: {id}</p>
          </div>
          <Button onClick={openCreateUserDialog}>Add User</Button>
        </div>

        {showUsersLoading && <TableSkeleton rows={5} columns={4} />}

        {usersError && (
          <div className="text-red-400 py-8 text-center">Failed to load users</div>
        )}

        {!usersLoading && !usersError && linuxUsers && linuxUsers.length === 0 && (
          <div className="text-slate-400 py-8 text-center">
            No Linux users yet. Click <strong>Add User</strong> to create one.
          </div>
        )}

        {!usersLoading && !usersError && linuxUsers && linuxUsers.length > 0 && (
          <div className="rounded-lg border border-slate-700 bg-slate-900">
            <Table>
              <TableHeader>
                <TableRow className="border-slate-700">
                  <TableHead>Username</TableHead>
                  <TableHead>UID</TableHead>
                  <TableHead>Shell</TableHead>
                  <TableHead>State</TableHead>
                  <TableHead>Keys</TableHead>
                  <TableHead>Sudo</TableHead>
                  <TableHead className="w-16">Priority</TableHead>
                  <TableHead className="w-40">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {linuxUsers.map((user) => (
                  <TableRow key={user.id} className="border-slate-700">
                    <TableCell className="font-mono text-white text-sm">{user.username}</TableCell>
                    <TableCell className="font-mono text-slate-300 text-xs">{user.uid ?? "auto"}</TableCell>
                    <TableCell className="font-mono text-slate-300 text-xs">{user.shell}</TableCell>
                    <TableCell>
                      <UserStateBadge state={user.state} />
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline" className="text-xs">
                        {user.authorized_keys.length} {user.authorized_keys.length === 1 ? "key" : "keys"}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      {user.sudo_rule ? (
                        <Badge className="bg-amber-600 text-white">Yes</Badge>
                      ) : (
                        <span className="text-slate-600 text-xs">No</span>
                      )}
                    </TableCell>
                    <TableCell className="font-mono text-slate-300 text-xs">{user.priority}</TableCell>
                    <TableCell>
                      <div className="flex gap-1">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => openEditUserDialog(user)}
                        >
                          Edit
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={userDeletingId === user.id}
                          onClick={() => handleUserDelete(user)}
                          className="text-red-400 hover:text-red-300 hover:bg-red-950"
                        >
                          {userDeletingId === user.id ? "…" : "Delete"}
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </div>

      {/* Linux Groups Section */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-xl font-bold text-white">Linux Groups</h2>
            <p className="text-slate-400 text-sm mt-1">System groups managed for this host group.</p>
          </div>
          <Button onClick={openCreateGroupDialog}>Add Group</Button>
        </div>

        {showGroupsLoading && <TableSkeleton rows={5} columns={4} />}

        {groupsError && (
          <div className="text-red-400 py-8 text-center">Failed to load groups</div>
        )}

        {!groupsLoading && !groupsError && linuxGroups && linuxGroups.length === 0 && (
          <div className="text-slate-400 py-8 text-center">
            No Linux groups yet. Click <strong>Add Group</strong> to create one.
          </div>
        )}

        {!groupsLoading && !groupsError && linuxGroups && linuxGroups.length > 0 && (
          <div className="rounded-lg border border-slate-700 bg-slate-900">
            <Table>
              <TableHeader>
                <TableRow className="border-slate-700">
                  <TableHead>Group Name</TableHead>
                  <TableHead>GID</TableHead>
                  <TableHead>State</TableHead>
                  <TableHead className="w-16">Priority</TableHead>
                  <TableHead className="w-40">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {linuxGroups.map((group) => (
                  <TableRow key={group.id} className="border-slate-700">
                    <TableCell className="font-mono text-white text-sm">{group.groupname}</TableCell>
                    <TableCell className="font-mono text-slate-300 text-xs">{group.gid ?? "auto"}</TableCell>
                    <TableCell>
                      <UserStateBadge state={group.state} />
                    </TableCell>
                    <TableCell className="font-mono text-slate-300 text-xs">{group.priority}</TableCell>
                    <TableCell>
                      <div className="flex gap-1">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => openEditGroupDialog(group)}
                        >
                          Edit
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={groupDeletingId === group.id}
                          onClick={() => handleGroupDelete(group)}
                          className="text-red-400 hover:text-red-300 hover:bg-red-950"
                        >
                          {groupDeletingId === group.id ? "…" : "Delete"}
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </div>

      {/* Add/Edit User Dialog */}
      <Dialog open={userDialogOpen} onOpenChange={setUserDialogOpen}>
        <DialogContent className="sm:max-w-lg max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editingUser ? "Edit Linux User" : "Add Linux User"}</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleUserSubmit} className="space-y-4 mt-2">
            <div className="space-y-2">
              <Label htmlFor="lu-username">Username</Label>
              <Input
                id="lu-username"
                type="text"
                placeholder="e.g. deploy, appuser"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="lu-uid">UID (optional)</Label>
              <Input
                id="lu-uid"
                type="number"
                placeholder="Auto-assign if empty"
                value={uid}
                onChange={(e) => setUid(e.target.value)}
                min={1000}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="lu-shell">Shell</Label>
              <Input
                id="lu-shell"
                type="text"
                value={shell}
                onChange={(e) => setShell(e.target.value)}
                required
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="lu-home">Home Directory (optional)</Label>
              <Input
                id="lu-home"
                type="text"
                placeholder="e.g. /home/deploy"
                value={homeDir}
                onChange={(e) => setHomeDir(e.target.value)}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="lu-state">State</Label>
              <select
                id="lu-state"
                value={userState}
                onChange={(e) => setUserState(e.target.value as "present" | "absent")}
                className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30"
              >
                <option value="present">Present</option>
                <option value="absent">Absent</option>
              </select>
            </div>

            <div className="space-y-2">
              <Label htmlFor="lu-keys">SSH Authorized Keys</Label>
              <textarea
                id="lu-keys"
                placeholder="One SSH public key per line"
                value={authorizedKeys}
                onChange={(e) => setAuthorizedKeys(e.target.value)}
                rows={3}
                className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground font-mono focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30 resize-y"
              />
              <p className="text-xs text-slate-500">One SSH public key per line</p>
            </div>

            <div className="space-y-2">
              <Label htmlFor="lu-groups">Supplementary Groups</Label>
              <Input
                id="lu-groups"
                type="text"
                placeholder="e.g. docker, wheel, sudo"
                value={supplementaryGroups}
                onChange={(e) => setSupplementaryGroups(e.target.value)}
              />
              <p className="text-xs text-slate-500">Comma-separated group names</p>
            </div>

            <div className="space-y-2">
              <Label htmlFor="lu-sudo">Sudo Rule (optional)</Label>
              <Input
                id="lu-sudo"
                type="text"
                placeholder="e.g. ALL=(ALL) NOPASSWD: ALL"
                value={sudoRule}
                onChange={(e) => setSudoRule(e.target.value)}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="lu-comment">Comment (optional)</Label>
              <Input
                id="lu-comment"
                type="text"
                placeholder="GECOS / description"
                value={comment}
                onChange={(e) => setComment(e.target.value)}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="lu-priority">Priority</Label>
              <Input
                id="lu-priority"
                type="number"
                value={userPriority}
                onChange={(e) => setUserPriority(Number(e.target.value))}
                required
                min={0}
              />
            </div>

            {userFormError && (
              <p className="text-sm text-red-400">{userFormError}</p>
            )}

            <div className="flex gap-3 pt-2">
              <Button type="submit" disabled={userFormLoading}>
                {userFormLoading ? "Saving..." : editingUser ? "Save Changes" : "Create"}
              </Button>
              <Button
                type="button"
                variant="outline"
                onClick={() => setUserDialogOpen(false)}
              >
                Cancel
              </Button>
            </div>
          </form>
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

      {/* Add/Edit Group Dialog */}
      <Dialog open={groupDialogOpen} onOpenChange={setGroupDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{editingGroup ? "Edit Linux Group" : "Add Linux Group"}</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleGroupSubmit} className="space-y-4 mt-2">
            <div className="space-y-2">
              <Label htmlFor="lg-name">Group Name</Label>
              <Input
                id="lg-name"
                type="text"
                placeholder="e.g. docker, developers"
                value={groupname}
                onChange={(e) => setGroupname(e.target.value)}
                required
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="lg-gid">GID (optional)</Label>
              <Input
                id="lg-gid"
                type="number"
                placeholder="Auto-assign if empty"
                value={gid}
                onChange={(e) => setGid(e.target.value)}
                min={1000}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="lg-state">State</Label>
              <select
                id="lg-state"
                value={groupState}
                onChange={(e) => setGroupState(e.target.value as "present" | "absent")}
                className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30"
              >
                <option value="present">Present</option>
                <option value="absent">Absent</option>
              </select>
            </div>

            <div className="space-y-2">
              <Label htmlFor="lg-priority">Priority</Label>
              <Input
                id="lg-priority"
                type="number"
                value={groupPriority}
                onChange={(e) => setGroupPriority(Number(e.target.value))}
                required
                min={0}
              />
            </div>

            {groupFormError && (
              <p className="text-sm text-red-400">{groupFormError}</p>
            )}

            <div className="flex gap-3 pt-2">
              <Button type="submit" disabled={groupFormLoading}>
                {groupFormLoading ? "Saving..." : editingGroup ? "Save Changes" : "Create"}
              </Button>
              <Button
                type="button"
                variant="outline"
                onClick={() => setGroupDialogOpen(false)}
              >
                Cancel
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  )
}
