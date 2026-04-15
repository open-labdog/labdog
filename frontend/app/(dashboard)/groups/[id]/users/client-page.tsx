"use client"

import { useState, type FormEvent } from "react"
import { useParams } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { DataTable } from "@/components/ui/data-table"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
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

export default function GroupUsersPage({ embedded = false }: { embedded?: boolean } = {}) {
  const params = useParams()
  const id = Number(params.id)

  const [userDialogOpen, setUserDialogOpen] = useState(false)
  const [editingUser, setEditingUser] = useState<LinuxUser | null>(null)

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

  const userSaveMutation = useApiMutation({
    mutationFn: ({ userId, payload }: { userId?: number; payload: Record<string, unknown> }) => {
      if (userId) return apiFetch(`/api/groups/${id}/linux-users/${userId}`, { method: "PUT", body: JSON.stringify(payload) })
      return apiFetch(`/api/groups/${id}/linux-users`, { method: "POST", body: JSON.stringify(payload) })
    },
    invalidateKeys: [["linux-users", id]],
    onSuccess: () => setUserDialogOpen(false),
  })

  const userDeleteMutation = useApiMutation({
    mutationFn: (userId: number) => apiFetch(`/api/groups/${id}/linux-users/${userId}`, { method: "DELETE" }),
    invalidateKeys: [["linux-users", id]],
  })

  const groupSaveMutation = useApiMutation({
    mutationFn: ({ groupId, payload }: { groupId?: number; payload: Record<string, unknown> }) => {
      if (groupId) return apiFetch(`/api/groups/${id}/linux-groups/${groupId}`, { method: "PUT", body: JSON.stringify(payload) })
      return apiFetch(`/api/groups/${id}/linux-groups`, { method: "POST", body: JSON.stringify(payload) })
    },
    invalidateKeys: [["linux-groups", id]],
    onSuccess: () => setGroupDialogOpen(false),
  })

  const groupDeleteMutation = useApiMutation({
    mutationFn: (groupId: number) => apiFetch(`/api/groups/${id}/linux-groups/${groupId}`, { method: "DELETE" }),
    invalidateKeys: [["linux-groups", id]],
  })

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
    userSaveMutation.reset()
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
    userSaveMutation.reset()
    setUserDialogOpen(true)
  }

  function handleUserSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const payload = {
      username, uid: uid ? Number(uid) : null, shell, home_dir: homeDir || null,
      state: userState, comment: comment || null, sudo_rule: sudoRule || null,
      authorized_keys: authorizedKeys.split("\n").map((k) => k.trim()).filter(Boolean),
      supplementary_groups: supplementaryGroups.split(",").map((g) => g.trim()).filter(Boolean),
      priority: userPriority,
    }
    userSaveMutation.mutate({ userId: editingUser?.id, payload })
  }

  function handleUserDelete(user: LinuxUser) {
    setConfirmState({
      open: true,
      title: "Delete Linux User",
      description: `Delete Linux user "${user.username}"? This action cannot be undone.`,
      action: async () => {
        setConfirmState((prev) => prev ? { ...prev, loading: true } : null)
        try { await userDeleteMutation.mutateAsync(user.id) } finally { setConfirmState(null) }
      },
    })
  }

  function openCreateGroupDialog() {
    setEditingGroup(null)
    setGroupname("")
    setGid("")
    setGroupState("present")
    setGroupPriority(100)
    groupSaveMutation.reset()
    setGroupDialogOpen(true)
  }

  function openEditGroupDialog(group: LinuxGroup) {
    setEditingGroup(group)
    setGroupname(group.groupname)
    setGid(group.gid != null ? String(group.gid) : "")
    setGroupState(group.state)
    setGroupPriority(group.priority)
    groupSaveMutation.reset()
    setGroupDialogOpen(true)
  }

  function handleGroupSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const payload = { groupname, gid: gid ? Number(gid) : null, state: groupState, priority: groupPriority }
    groupSaveMutation.mutate({ groupId: editingGroup?.id, payload })
  }

  function handleGroupDelete(group: LinuxGroup) {
    setConfirmState({
      open: true,
      title: "Delete Linux Group",
      description: `Delete Linux group "${group.groupname}"? This action cannot be undone.`,
      action: async () => {
        setConfirmState((prev) => prev ? { ...prev, loading: true } : null)
        try { await groupDeleteMutation.mutateAsync(group.id) } finally { setConfirmState(null) }
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
      {!embedded && <Breadcrumb items={[{ label: "Groups", href: "/groups" }, { label: group?.name ?? "Group", href: `/groups/${id}` }, { label: "Users" }]} />}
      {/* Linux Users Section */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-white">Linux Users</h1>
          </div>
          <Button onClick={openCreateUserDialog}>Add User</Button>
        </div>

        {showUsersLoading && <TableSkeleton rows={5} columns={4} />}

        {usersError && (
          <div className="text-red-400 py-8 text-center">Failed to load users</div>
        )}

        {!usersLoading && !usersError && (
          <DataTable<LinuxUser>
            tableId="group-linux-users"
            data={linuxUsers}
            emptyMessage={
              <div className="text-slate-400 py-8 text-center">
                No Linux users yet. Click <strong>Add User</strong> to create one.
              </div>
            }
            getRowKey={(user) => user.id}
            columns={[
              {
                key: "username",
                label: "Username",
                accessor: (user) => user.username,
                cell: (user) => <span className="font-mono text-white text-sm">{user.username}</span>,
                defaultWidth: 160,
                filter: { type: "text", placeholder: "e.g. deploy" },
              },
              {
                key: "uid",
                label: "UID",
                accessor: (user) => user.uid ?? "auto",
                cell: (user) => <span className="font-mono text-slate-300 text-xs">{user.uid ?? "auto"}</span>,
                defaultWidth: 80,
                filter: { type: "text" },
              },
              {
                key: "shell",
                label: "Shell",
                accessor: (user) => user.shell,
                cell: (user) => <span className="font-mono text-slate-300 text-xs">{user.shell}</span>,
                defaultWidth: 140,
                filter: { type: "text" },
              },
              {
                key: "state",
                label: "State",
                accessor: (user) => user.state,
                cell: (user) => <UserStateBadge state={user.state} />,
                defaultWidth: 120,
                filter: { type: "enum", from: "accessor" },
              },
              {
                key: "keys",
                label: "Keys",
                accessor: (user) => user.authorized_keys.length,
                cell: (user) => (
                  <Badge variant="outline" className="text-xs">
                    {user.authorized_keys.length} {user.authorized_keys.length === 1 ? "key" : "keys"}
                  </Badge>
                ),
                defaultWidth: 80,
              },
              {
                key: "sudo",
                label: "Sudo",
                accessor: (user) => !!user.sudo_rule,
                cell: (user) => user.sudo_rule ? (
                  <Badge className="bg-amber-600 text-white">Yes</Badge>
                ) : (
                  <span className="text-slate-600 text-xs">No</span>
                ),
                defaultWidth: 80,
                filter: { type: "boolean" },
              },
              {
                key: "priority",
                label: "Priority",
                accessor: (user) => user.priority,
                cell: (user) => <span className="font-mono text-slate-300 text-xs">{user.priority}</span>,
                defaultWidth: 64,
                filter: { type: "text" },
              },
              {
                key: "actions",
                label: "Actions",
                cell: (user) => (
                  <div className="flex gap-1">
                    <Button size="sm" variant="ghost" onClick={() => openEditUserDialog(user)}>
                      Edit
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={userDeleteMutation.isPending}
                      onClick={() => handleUserDelete(user)}
                      className="text-red-400 hover:text-red-300 hover:bg-red-950"
                    >
                      {userDeleteMutation.isPending ? "…" : "Delete"}
                    </Button>
                  </div>
                ),
                defaultWidth: 160,
                resizable: false,
                sortable: false,
              },
            ]}
          />
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

        {!groupsLoading && !groupsError && (
          <DataTable<LinuxGroup>
            tableId="group-linux-groups"
            data={linuxGroups}
            emptyMessage={
              <div className="text-slate-400 py-8 text-center">
                No Linux groups yet. Click <strong>Add Group</strong> to create one.
              </div>
            }
            getRowKey={(g) => g.id}
            columns={[
              {
                key: "groupname",
                label: "Group Name",
                accessor: (g) => g.groupname,
                cell: (g) => <span className="font-mono text-white text-sm">{g.groupname}</span>,
                defaultWidth: 180,
                filter: { type: "text" },
              },
              {
                key: "gid",
                label: "GID",
                accessor: (g) => g.gid ?? "auto",
                cell: (g) => <span className="font-mono text-slate-300 text-xs">{g.gid ?? "auto"}</span>,
                defaultWidth: 80,
                filter: { type: "text" },
              },
              {
                key: "state",
                label: "State",
                accessor: (g) => g.state,
                cell: (g) => <UserStateBadge state={g.state} />,
                defaultWidth: 120,
                filter: { type: "enum", from: "accessor" },
              },
              {
                key: "priority",
                label: "Priority",
                accessor: (g) => g.priority,
                cell: (g) => <span className="font-mono text-slate-300 text-xs">{g.priority}</span>,
                defaultWidth: 64,
                filter: { type: "text" },
              },
              {
                key: "actions",
                label: "Actions",
                cell: (g) => (
                  <div className="flex gap-1">
                    <Button size="sm" variant="ghost" onClick={() => openEditGroupDialog(g)}>
                      Edit
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={groupDeleteMutation.isPending}
                      onClick={() => handleGroupDelete(g)}
                      className="text-red-400 hover:text-red-300 hover:bg-red-950"
                    >
                      {groupDeleteMutation.isPending ? "…" : "Delete"}
                    </Button>
                  </div>
                ),
                defaultWidth: 160,
                resizable: false,
                sortable: false,
              },
            ]}
          />
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

            {userSaveMutation.error && (
              <p className="text-sm text-red-400">{userSaveMutation.error.message}</p>
            )}

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setUserDialogOpen(false)}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={userSaveMutation.isPending}>
                {userSaveMutation.isPending ? "Saving..." : editingUser ? "Save Changes" : "Create"}
              </Button>
            </DialogFooter>
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

            {groupSaveMutation.error && (
              <p className="text-sm text-red-400">{groupSaveMutation.error.message}</p>
            )}

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setGroupDialogOpen(false)}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={groupSaveMutation.isPending}>
                {groupSaveMutation.isPending ? "Saving..." : editingGroup ? "Save Changes" : "Create"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  )
}
