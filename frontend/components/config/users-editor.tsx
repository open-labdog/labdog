"use client"

import { useState, type FormEvent } from "react"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { plural } from "@/lib/fleet"
import { ITEM_STATE, def } from "@/lib/status"
import type { LinuxGroup, LinuxUser } from "@/lib/types"
import { Banner, Confirm, Field, Modal, Table, Tag, Toolbar } from "@/components/ld"
import { GROUP_KEYS, GitOpsBanner, stop, useEditorGroup } from "./shared"

/**
 * Linux users and groups a host group declares — accounts with their
 * shell, home, sudo rule, SSH keys and supplementary groups; and the
 * Linux groups those refer to. Embedded in the group page's Config tab.
 */
export function UsersEditor({ groupId }: { groupId: number }) {
  const { group, gitops } = useEditorGroup(groupId)

  const [userDialogOpen, setUserDialogOpen] = useState(false)
  const [editingUser, setEditingUser] = useState<LinuxUser | null>(null)
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
  const [deletingUser, setDeletingUser] = useState<LinuxUser | null>(null)

  const [groupDialogOpen, setGroupDialogOpen] = useState(false)
  const [editingGroup, setEditingGroup] = useState<LinuxGroup | null>(null)
  const [groupname, setGroupname] = useState("")
  const [gid, setGid] = useState("")
  const [groupState, setGroupState] = useState<"present" | "absent">("present")
  const [groupPriority, setGroupPriority] = useState(100)
  const [deletingGroup, setDeletingGroup] = useState<LinuxGroup | null>(null)

  const { data: linuxUsers = [], isLoading: usersLoading, error: usersError } = useQuery<LinuxUser[]>({
    queryKey: ["linux-users", groupId],
    queryFn: () => apiFetch<LinuxUser[]>(`/api/groups/${groupId}/linux-users`),
    enabled: !!groupId,
  })
  const { data: linuxGroups = [], isLoading: groupsLoading, error: groupsError } = useQuery<LinuxGroup[]>({
    queryKey: ["linux-groups", groupId],
    queryFn: () => apiFetch<LinuxGroup[]>(`/api/groups/${groupId}/linux-groups`),
    enabled: !!groupId,
  })

  const userSaveMutation = useApiMutation({
    mutationFn: ({ userId, payload }: { userId?: number; payload: Record<string, unknown> }) =>
      userId
        ? apiFetch(`/api/groups/${groupId}/linux-users/${userId}`, { method: "PUT", body: JSON.stringify(payload) })
        : apiFetch(`/api/groups/${groupId}/linux-users`, { method: "POST", body: JSON.stringify(payload) }),
    invalidateKeys: [["linux-users", groupId], ...GROUP_KEYS],
    onSuccess: () => setUserDialogOpen(false),
  })
  const userDeleteMutation = useApiMutation({
    mutationFn: (userId: number) => apiFetch(`/api/groups/${groupId}/linux-users/${userId}`, { method: "DELETE" }),
    invalidateKeys: [["linux-users", groupId], ...GROUP_KEYS],
    onSuccess: () => setDeletingUser(null),
  })
  const groupSaveMutation = useApiMutation({
    mutationFn: ({ lgId, payload }: { lgId?: number; payload: Record<string, unknown> }) =>
      lgId
        ? apiFetch(`/api/groups/${groupId}/linux-groups/${lgId}`, { method: "PUT", body: JSON.stringify(payload) })
        : apiFetch(`/api/groups/${groupId}/linux-groups`, { method: "POST", body: JSON.stringify(payload) }),
    invalidateKeys: [["linux-groups", groupId], ...GROUP_KEYS],
    onSuccess: () => setGroupDialogOpen(false),
  })
  const groupDeleteMutation = useApiMutation({
    mutationFn: (lgId: number) => apiFetch(`/api/groups/${groupId}/linux-groups/${lgId}`, { method: "DELETE" }),
    invalidateKeys: [["linux-groups", groupId], ...GROUP_KEYS],
    onSuccess: () => setDeletingGroup(null),
  })

  function openCreateUser() {
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

  function openEditUser(user: LinuxUser) {
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
    userSaveMutation.mutate({
      userId: editingUser?.id,
      payload: {
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
      },
    })
  }

  function openCreateGroup() {
    setEditingGroup(null)
    setGroupname("")
    setGid("")
    setGroupState("present")
    setGroupPriority(100)
    groupSaveMutation.reset()
    setGroupDialogOpen(true)
  }

  function openEditGroup(lg: LinuxGroup) {
    setEditingGroup(lg)
    setGroupname(lg.groupname)
    setGid(lg.gid != null ? String(lg.gid) : "")
    setGroupState(lg.state)
    setGroupPriority(lg.priority)
    groupSaveMutation.reset()
    setGroupDialogOpen(true)
  }

  function handleGroupSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    groupSaveMutation.mutate({ lgId: editingGroup?.id, payload: { groupname, gid: gid ? Number(gid) : null, state: groupState, priority: groupPriority } })
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Toolbar
        actions={
          !gitops && (
            <>
              <button type="button" className="btn btn-sm" onClick={openCreateGroup}>
                Add group
              </button>
              <button type="button" className="btn btn-sm btn-primary" onClick={openCreateUser}>
                Add user
              </button>
            </>
          )
        }
      >
        <span className="tt">
          {plural(linuxUsers.length, "user")} · {plural(linuxGroups.length, "group")} declared here
        </span>
      </Toolbar>
      {gitops && <GitOpsBanner group={group} what="users and groups are" />}
      {usersError && (
        <Banner tone="danger" flush>
          Could not load users: {usersError.message}
        </Banner>
      )}

      <div className="scroll flex min-h-0 flex-1 flex-col">
        <div className="flex shrink-0 flex-col">
        <Table<LinuxUser>
          cols={[
            { k: "username", label: "user", w: "minmax(120px,1fr)", sortable: false, cell: (u) => <span className="mono font-medium text-text">{u.username}</span> },
            { k: "uid", label: "uid", w: "70px", right: true, sortable: false, cell: (u) => <span className="mono num">{u.uid ?? <span className="text-text-faint">auto</span>}</span> },
            { k: "shell", label: "shell", w: "120px", sortable: false, cell: (u) => <span className="mono text-[11px]">{u.shell}</span> },
            { k: "state", label: "state", w: "90px", sortable: false, cell: (u) => <Tag tone={def(ITEM_STATE, u.state).tone}>{u.state}</Tag> },
            { k: "keys", label: "ssh keys", w: "80px", right: true, sortable: false, cell: (u) => <span className={`mono num ${u.authorized_keys.length ? "" : "text-text-faint"}`}>{u.authorized_keys.length}</span> },
            { k: "sudo", label: "sudo", w: "minmax(100px,1fr)", sortable: false, cell: (u) => (u.sudo_rule ? <span className="mono text-[11px]" title={u.sudo_rule}>{u.sudo_rule}</span> : <span className="text-text-faint">—</span>) },
            { k: "groups", label: "groups", w: "minmax(100px,1fr)", sortable: false, cell: (u) => <span className="mono text-[11px]">{u.supplementary_groups.join(", ") || <span className="text-text-faint">—</span>}</span> },
            { k: "priority", label: "priority", w: "70px", right: true, sortable: false, cell: (u) => <span className="mono num">{u.priority}</span> },
            {
              k: "actions",
              label: "",
              w: "118px",
              right: true,
              sortable: false,
              cell: (u) => (
                <span className="flex gap-0.5" onClick={stop}>
                  <button type="button" className="btn btn-sm btn-ghost" disabled={gitops} onClick={() => openEditUser(u)}>
                    edit
                  </button>
                  <button type="button" className="btn btn-sm btn-ghost text-danger" disabled={gitops || userDeleteMutation.isPending} onClick={() => setDeletingUser(u)}>
                    delete
                  </button>
                </span>
              ),
            },
          ]}
          rows={linuxUsers}
          keyOf={(u) => u.id}
          onRowClick={gitops ? undefined : openEditUser}
          loading={usersLoading}
          empty="Nothing declared. Add user declares this module for the group; accounts are created on the next plan."
        />
        </div>

        <div className="tt shrink-0 border-y border-line bg-surface-2 px-[13px] py-[7px]">linux groups · {plural(linuxGroups.length, "group")}</div>
        {groupsError && <Banner tone="danger">Could not load groups: {groupsError.message}</Banner>}
        <div className="flex shrink-0 flex-col">
        <Table<LinuxGroup>
          cols={[
            { k: "groupname", label: "group", w: "minmax(140px,1fr)", sortable: false, cell: (g) => <span className="mono font-medium text-text">{g.groupname}</span> },
            { k: "gid", label: "gid", w: "80px", right: true, sortable: false, cell: (g) => <span className="mono num">{g.gid ?? <span className="text-text-faint">auto</span>}</span> },
            { k: "state", label: "state", w: "90px", sortable: false, cell: (g) => <Tag tone={def(ITEM_STATE, g.state).tone}>{g.state}</Tag> },
            { k: "priority", label: "priority", w: "70px", right: true, sortable: false, cell: (g) => <span className="mono num">{g.priority}</span> },
            { k: "spacer", label: "", w: "1fr", sortable: false, cell: () => null },
            {
              k: "actions",
              label: "",
              w: "118px",
              right: true,
              sortable: false,
              cell: (g) => (
                <span className="flex gap-0.5" onClick={stop}>
                  <button type="button" className="btn btn-sm btn-ghost" disabled={gitops} onClick={() => openEditGroup(g)}>
                    edit
                  </button>
                  <button type="button" className="btn btn-sm btn-ghost text-danger" disabled={gitops || groupDeleteMutation.isPending} onClick={() => setDeletingGroup(g)}>
                    delete
                  </button>
                </span>
              ),
            },
          ]}
          rows={linuxGroups}
          keyOf={(g) => g.id}
          onRowClick={gitops ? undefined : openEditGroup}
          loading={groupsLoading}
          empty="No Linux groups declared — users' supplementary groups must already exist on the host."
        />
        </div>
      </div>

      {userDialogOpen && (
        <Modal
          title={editingUser ? "Edit user" : "Add user"}
          meta={group ? `group: ${group.name}` : undefined}
          w={620}
          onClose={() => setUserDialogOpen(false)}
          onSubmit={handleUserSubmit}
          footer={
            <>
              <span className="tt mr-auto">desired state only — nothing applies until a plan runs</span>
              <button type="button" className="btn" onClick={() => setUserDialogOpen(false)}>
                Cancel
              </button>
              <button type="submit" className="btn btn-primary" disabled={userSaveMutation.isPending || !username.trim()}>
                {userSaveMutation.isPending ? "Saving…" : editingUser ? "Save changes" : "Add user"}
              </button>
            </>
          }
        >
          <div className="grid gap-[11px] grid-cols-1 sm:grid-cols-[1fr_90px_130px_100px]">
            <Field label="username" htmlFor="user-name">
              <input id="user-name" className="inp mono" placeholder="deploy" value={username} onChange={(e) => setUsername(e.target.value)} required />
            </Field>
            <Field label="uid" htmlFor="user-uid" hint="optional">
              <input id="user-uid" type="number" className="inp mono num" value={uid} onChange={(e) => setUid(e.target.value)} />
            </Field>
            <Field label="shell" htmlFor="user-shell">
              <input id="user-shell" className="inp mono" value={shell} onChange={(e) => setShell(e.target.value)} />
            </Field>
            <Field label="state" htmlFor="user-state">
              <select id="user-state" className="inp" value={userState} onChange={(e) => setUserState(e.target.value as "present" | "absent")}>
                <option value="present">present</option>
                <option value="absent">absent</option>
              </select>
            </Field>
          </div>
          <div className="grid gap-[11px] grid-cols-1 sm:grid-cols-[1fr_1fr]">
            <Field label="home directory" htmlFor="user-home" hint="blank = default">
              <input id="user-home" className="inp mono" placeholder="/home/deploy" value={homeDir} onChange={(e) => setHomeDir(e.target.value)} />
            </Field>
            <Field label="supplementary groups" htmlFor="user-groups" hint="comma-separated">
              <input id="user-groups" className="inp mono" placeholder="sudo, docker" value={supplementaryGroups} onChange={(e) => setSupplementaryGroups(e.target.value)} />
            </Field>
          </div>
          <Field label="ssh authorized keys" htmlFor="user-keys" hint="one per line">
            <textarea id="user-keys" className="inp mono" rows={4} spellCheck={false} placeholder="ssh-ed25519 AAAA… deploy@laptop" value={authorizedKeys} onChange={(e) => setAuthorizedKeys(e.target.value)} />
          </Field>
          <div className="grid gap-[11px] grid-cols-1 sm:grid-cols-[1fr_100px]">
            <Field label="sudo rule" htmlFor="user-sudo" hint="optional — a sudoers line without the username">
              <input id="user-sudo" className="inp mono" placeholder="ALL=(ALL) NOPASSWD:ALL" value={sudoRule} onChange={(e) => setSudoRule(e.target.value)} />
            </Field>
            <Field label="priority" htmlFor="user-priority">
              <input id="user-priority" type="number" min={0} className="inp mono num" value={userPriority} onChange={(e) => setUserPriority(Number(e.target.value))} />
            </Field>
          </div>
          <Field label="comment" htmlFor="user-comment" hint="optional — the GECOS field">
            <input id="user-comment" className="inp" value={comment} onChange={(e) => setComment(e.target.value)} />
          </Field>
          {userSaveMutation.error && <Banner tone="danger">{userSaveMutation.error.message}</Banner>}
        </Modal>
      )}

      {groupDialogOpen && (
        <Modal
          title={editingGroup ? "Edit Linux group" : "Add Linux group"}
          meta={group ? `group: ${group.name}` : undefined}
          w={460}
          onClose={() => setGroupDialogOpen(false)}
          onSubmit={handleGroupSubmit}
          footer={
            <>
              <span className="tt mr-auto">desired state only — nothing applies until a plan runs</span>
              <button type="button" className="btn" onClick={() => setGroupDialogOpen(false)}>
                Cancel
              </button>
              <button type="submit" className="btn btn-primary" disabled={groupSaveMutation.isPending || !groupname.trim()}>
                {groupSaveMutation.isPending ? "Saving…" : editingGroup ? "Save changes" : "Add group"}
              </button>
            </>
          }
        >
          <div className="grid gap-[11px] grid-cols-1 sm:grid-cols-[1fr_90px_100px_90px]">
            <Field label="group name" htmlFor="lg-name">
              <input id="lg-name" className="inp mono" placeholder="docker" value={groupname} onChange={(e) => setGroupname(e.target.value)} required />
            </Field>
            <Field label="gid" htmlFor="lg-gid" hint="optional">
              <input id="lg-gid" type="number" className="inp mono num" value={gid} onChange={(e) => setGid(e.target.value)} />
            </Field>
            <Field label="state" htmlFor="lg-state">
              <select id="lg-state" className="inp" value={groupState} onChange={(e) => setGroupState(e.target.value as "present" | "absent")}>
                <option value="present">present</option>
                <option value="absent">absent</option>
              </select>
            </Field>
            <Field label="priority" htmlFor="lg-priority">
              <input id="lg-priority" type="number" min={0} className="inp mono num" value={groupPriority} onChange={(e) => setGroupPriority(Number(e.target.value))} />
            </Field>
          </div>
          {groupSaveMutation.error && <Banner tone="danger">{groupSaveMutation.error.message}</Banner>}
        </Modal>
      )}

      {deletingUser && (
        <Confirm
          open
          onOpenChange={(open) => !open && setDeletingUser(null)}
          title="Delete Linux user"
          description={`${deletingUser.username} is no longer declared by this group; the account stays on the hosts until a plan runs with it absent. This cannot be undone.`}
          confirmLabel="Delete"
          variant="destructive"
          loading={userDeleteMutation.isPending}
          onConfirm={() => userDeleteMutation.mutate(deletingUser.id)}
        />
      )}
      {deletingGroup && (
        <Confirm
          open
          onOpenChange={(open) => !open && setDeletingGroup(null)}
          title="Delete Linux group"
          description={`${deletingGroup.groupname} is no longer declared by this group. This cannot be undone.`}
          confirmLabel="Delete"
          variant="destructive"
          loading={groupDeleteMutation.isPending}
          onConfirm={() => groupDeleteMutation.mutate(deletingGroup.id)}
        />
      )}
    </div>
  )
}
