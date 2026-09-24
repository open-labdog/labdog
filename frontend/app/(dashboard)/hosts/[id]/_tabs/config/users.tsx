"use client"

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { plural } from "@/lib/fleet"
import { def, ITEM_STATE } from "@/lib/status"
import { Banner, Confirm, Field, Modal, Provenance, Tag, Table, Toolbar } from "@/components/ld"
import type { EffectiveLinuxGroup, EffectiveLinuxUser, LinuxGroup, LinuxUser, ModuleCurrentState } from "@/lib/types"
import { CurrentStateSection, type CollectedGroup, type CollectedUser } from "./shared"

const userDefaults = { username: "", uid: "", shell: "/bin/bash", homeDir: "", state: "present" as "present" | "absent", comment: "", sudoRule: "", authorizedKeys: "", supplementaryGroups: "", priority: 100 }
const groupDefaults = { groupname: "", gid: "", state: "present" as "present" | "absent", priority: 100 }

export function UsersTab({
  hostId,
  currentState,
  syncBusy,
  onSync,
}: {
  hostId: number
  currentState: ModuleCurrentState[] | undefined
  syncBusy: boolean
  onSync: () => void
}) {
  const [luOpen, setLuOpen] = useState(false)
  const [luEditingId, setLuEditingId] = useState<number | null>(null)
  const [luForm, setLuForm] = useState(userDefaults)
  const [luDeleting, setLuDeleting] = useState<string | null>(null)

  const [lgOpen, setLgOpen] = useState(false)
  const [lgEditingId, setLgEditingId] = useState<number | null>(null)
  const [lgForm, setLgForm] = useState(groupDefaults)
  const [lgDeleting, setLgDeleting] = useState<string | null>(null)

  const { data: users, isLoading: usersLoading, error: usersError } = useQuery<EffectiveLinuxUser[]>({
    queryKey: ["host-effective-linux-users", hostId],
    queryFn: () => apiFetch<EffectiveLinuxUser[]>(`/api/hosts/${hostId}/effective-users`),
  })
  const { data: groups, isLoading: groupsLoading, error: groupsError } = useQuery<EffectiveLinuxGroup[]>({
    queryKey: ["host-effective-linux-groups", hostId],
    queryFn: () => apiFetch<EffectiveLinuxGroup[]>(`/api/hosts/${hostId}/effective-groups`),
  })
  const { data: userOverrides } = useQuery<LinuxUser[]>({
    queryKey: ["host-linux-user-overrides", hostId],
    queryFn: () => apiFetch<LinuxUser[]>(`/api/hosts/${hostId}/linux-users`),
  })
  const { data: groupOverrides } = useQuery<LinuxGroup[]>({
    queryKey: ["host-linux-group-overrides", hostId],
    queryFn: () => apiFetch<LinuxGroup[]>(`/api/hosts/${hostId}/linux-groups`),
  })

  const luSaveMutation = useApiMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      luEditingId != null
        ? apiFetch(`/api/hosts/${hostId}/linux-users/${luEditingId}`, { method: "PUT", body: JSON.stringify(payload) })
        : apiFetch(`/api/hosts/${hostId}/linux-users`, { method: "POST", body: JSON.stringify(payload) }),
    invalidateKeys: [["host-effective-linux-users", hostId], ["host-linux-user-overrides", hostId]],
    onSuccess: () => setLuOpen(false),
  })
  const luDeleteMutation = useApiMutation({
    mutationFn: (overrideId: number) => apiFetch(`/api/hosts/${hostId}/linux-users/${overrideId}`, { method: "DELETE" }),
    invalidateKeys: [["host-effective-linux-users", hostId], ["host-linux-user-overrides", hostId]],
    onSuccess: () => setLuDeleting(null),
  })
  const lgSaveMutation = useApiMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      lgEditingId != null
        ? apiFetch(`/api/hosts/${hostId}/linux-groups/${lgEditingId}`, { method: "PUT", body: JSON.stringify(payload) })
        : apiFetch(`/api/hosts/${hostId}/linux-groups`, { method: "POST", body: JSON.stringify(payload) }),
    invalidateKeys: [["host-effective-linux-groups", hostId], ["host-linux-group-overrides", hostId]],
    onSuccess: () => setLgOpen(false),
  })
  const lgDeleteMutation = useApiMutation({
    mutationFn: (overrideId: number) => apiFetch(`/api/hosts/${hostId}/linux-groups/${overrideId}`, { method: "DELETE" }),
    invalidateKeys: [["host-effective-linux-groups", hostId], ["host-linux-group-overrides", hostId]],
    onSuccess: () => setLgDeleting(null),
  })

  function openLuCreate() {
    setLuEditingId(null)
    setLuForm(userDefaults)
    luSaveMutation.reset()
    setLuOpen(true)
  }
  function openLuEditOverride(o: LinuxUser) {
    setLuEditingId(o.id)
    setLuForm({ username: o.username, uid: o.uid != null ? String(o.uid) : "", shell: o.shell, homeDir: o.home_dir ?? "", state: o.state, comment: o.comment ?? "", sudoRule: o.sudo_rule ?? "", authorizedKeys: o.authorized_keys.join("\n"), supplementaryGroups: o.supplementary_groups.join(", "), priority: o.priority })
    luSaveMutation.reset()
    setLuOpen(true)
  }
  function handleLuEdit(username: string) {
    const o = userOverrides?.find((x) => x.username === username)
    if (o) openLuEditOverride(o)
  }
  function manageCollectedUser(u: CollectedUser) {
    const existing = userOverrides?.find((o) => o.username === u.username)
    if (existing) { openLuEditOverride(existing); return }
    setLuEditingId(null)
    setLuForm({ ...userDefaults, username: u.username, uid: u.uid != null ? String(u.uid) : "", shell: u.shell || "/bin/bash", homeDir: u.home ?? "" })
    luSaveMutation.reset()
    setLuOpen(true)
  }
  function luSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    luSaveMutation.mutate({
      username: luForm.username, uid: luForm.uid ? Number(luForm.uid) : null, shell: luForm.shell,
      home_dir: luForm.homeDir || null, state: luForm.state, comment: luForm.comment || null,
      sudo_rule: luForm.sudoRule || null,
      authorized_keys: luForm.authorizedKeys.split("\n").map((k) => k.trim()).filter(Boolean),
      supplementary_groups: luForm.supplementaryGroups.split(",").map((g) => g.trim()).filter(Boolean),
      priority: luForm.priority,
    })
  }

  function openLgCreate() {
    setLgEditingId(null)
    setLgForm(groupDefaults)
    lgSaveMutation.reset()
    setLgOpen(true)
  }
  function openLgEditOverride(o: LinuxGroup) {
    setLgEditingId(o.id)
    setLgForm({ groupname: o.groupname, gid: o.gid != null ? String(o.gid) : "", state: o.state, priority: o.priority })
    lgSaveMutation.reset()
    setLgOpen(true)
  }
  function handleLgEdit(groupname: string) {
    const o = groupOverrides?.find((x) => x.groupname === groupname)
    if (o) openLgEditOverride(o)
  }
  function manageCollectedGroup(g: CollectedGroup) {
    const existing = groupOverrides?.find((o) => o.groupname === g.groupname)
    if (existing) { openLgEditOverride(existing); return }
    setLgEditingId(null)
    setLgForm({ ...groupDefaults, groupname: g.groupname, gid: g.gid != null ? String(g.gid) : "" })
    lgSaveMutation.reset()
    setLgOpen(true)
  }
  function lgSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    lgSaveMutation.mutate({ groupname: lgForm.groupname, gid: lgForm.gid ? Number(lgForm.gid) : null, state: lgForm.state, priority: lgForm.priority })
  }

  const userRows = users ?? []
  const groupRows = groups ?? []

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Toolbar
        actions={
          <>
            <button type="button" className="btn btn-sm btn-ghost" disabled={syncBusy} onClick={onSync}>sync</button>
            <button type="button" className="btn btn-sm" onClick={openLuCreate}>add user override</button>
            <button type="button" className="btn btn-sm btn-primary" onClick={openLgCreate}>add group override</button>
          </>
        }
      >
        <span className="tt">{plural(userRows.length, "user")} · {plural(groupRows.length, "group")} effective</span>
      </Toolbar>

      {usersError && <Banner tone="danger" flush>Could not load users: {usersError.message}</Banner>}
      {luDeleteMutation.error && <Banner tone="danger" flush>{luDeleteMutation.error.message}</Banner>}

      <div className="border-b border-line">
        <div className="tt bg-surface-2 px-[13px] py-1.5">users · {userRows.length}</div>
        <Table<EffectiveLinuxUser>
          cols={[
            { k: "username", label: "username", w: "minmax(130px,1fr)", sortable: false, cell: (u) => <span className="mono font-medium text-text">{u.username}</span> },
            { k: "uid", label: "uid", w: "70px", sortable: false, cell: (u) => <span className="mono text-[11px] text-text-3">{u.uid ?? "auto"}</span> },
            { k: "shell", label: "shell", w: "120px", sortable: false, cell: (u) => <span className="mono text-[11px] text-text-3">{u.shell}</span> },
            { k: "state", label: "state", w: "90px", sortable: false, cell: (u) => <Tag tone={def(ITEM_STATE, u.state).tone}>{u.state}</Tag> },
            { k: "keys", label: "keys", w: "70px", sortable: false, cell: (u) => <Tag>{u.authorized_keys.length}</Tag> },
            { k: "sudo", label: "sudo", w: "60px", sortable: false, cell: (u) => (u.sudo_rule ? <Tag tone="warn">yes</Tag> : <span className="text-text-faint">no</span>) },
            { k: "source", label: "source", w: "minmax(110px,1fr)", sortable: false, cell: (u) => <Provenance origin={u.source} label={u.source === "host" ? "this host" : u.source_name} /> },
            {
              k: "actions", label: "", w: "108px", right: true, sortable: false,
              cell: (u) =>
                u.source === "host" ? (
                  <span className="flex gap-0.5">
                    <button type="button" className="btn btn-sm btn-ghost" onClick={() => handleLuEdit(u.username)}>edit</button>
                    <button type="button" className="btn btn-sm btn-ghost text-danger" disabled={luDeleteMutation.isPending} onClick={() => setLuDeleting(u.username)}>delete</button>
                  </span>
                ) : (
                  <span className="text-[11px] text-text-faint">read-only</span>
                ),
            },
          ]}
          rows={userRows}
          keyOf={(u) => `${u.source}-${u.source_id}-${u.username}`}
          loading={usersLoading}
          empty="No users configured."
        />
      </div>

      {groupsError && <Banner tone="danger" flush>Could not load groups: {groupsError.message}</Banner>}
      {lgDeleteMutation.error && <Banner tone="danger" flush>{lgDeleteMutation.error.message}</Banner>}

      <div>
        <div className="tt bg-surface-2 px-[13px] py-1.5">linux groups · {groupRows.length}</div>
        <Table<EffectiveLinuxGroup>
          cols={[
            { k: "groupname", label: "group name", w: "minmax(150px,1fr)", sortable: false, cell: (g) => <span className="mono font-medium text-text">{g.groupname}</span> },
            { k: "gid", label: "gid", w: "70px", sortable: false, cell: (g) => <span className="mono text-[11px] text-text-3">{g.gid ?? "auto"}</span> },
            { k: "state", label: "state", w: "90px", sortable: false, cell: (g) => <Tag tone={def(ITEM_STATE, g.state).tone}>{g.state}</Tag> },
            { k: "source", label: "source", w: "minmax(110px,1fr)", sortable: false, cell: (g) => <Provenance origin={g.source} label={g.source === "host" ? "this host" : g.source_name} /> },
            {
              k: "actions", label: "", w: "108px", right: true, sortable: false,
              cell: (g) =>
                g.source === "host" ? (
                  <span className="flex gap-0.5">
                    <button type="button" className="btn btn-sm btn-ghost" onClick={() => handleLgEdit(g.groupname)}>edit</button>
                    <button type="button" className="btn btn-sm btn-ghost text-danger" disabled={lgDeleteMutation.isPending} onClick={() => setLgDeleting(g.groupname)}>delete</button>
                  </span>
                ) : (
                  <span className="text-[11px] text-text-faint">read-only</span>
                ),
            },
          ]}
          rows={groupRows}
          keyOf={(g) => `${g.source}-${g.source_id}-${g.groupname}`}
          loading={groupsLoading}
          empty="No groups configured."
        />
      </div>

      {luOpen && (
        <Modal
          title={luEditingId != null ? "Edit user override" : "Add user override"}
          w={560}
          onClose={() => setLuOpen(false)}
          onSubmit={luSubmit}
          footer={
            <>
              <button type="button" className="btn ml-auto" onClick={() => setLuOpen(false)}>Cancel</button>
              <button type="submit" className="btn btn-primary" disabled={luSaveMutation.isPending}>{luSaveMutation.isPending ? "Saving…" : luEditingId != null ? "Save changes" : "Create override"}</button>
            </>
          }
        >
          <div className="grid gap-[11px] grid-cols-1 sm:grid-cols-[1fr_100px]">
            <Field label="username" htmlFor="lu-username">
              <input id="lu-username" className="inp mono" placeholder="e.g. deploy, appuser" value={luForm.username} onChange={(e) => setLuForm((f) => ({ ...f, username: e.target.value }))} required disabled={luEditingId != null} />
            </Field>
            <Field label="uid" htmlFor="lu-uid" hint="auto if empty">
              <input id="lu-uid" type="number" min={1000} className="inp mono num" value={luForm.uid} onChange={(e) => setLuForm((f) => ({ ...f, uid: e.target.value }))} />
            </Field>
          </div>
          <div className="grid gap-[11px] grid-cols-1 sm:grid-cols-[1fr_1fr]">
            <Field label="shell" htmlFor="lu-shell">
              <input id="lu-shell" className="inp mono" value={luForm.shell} onChange={(e) => setLuForm((f) => ({ ...f, shell: e.target.value }))} required />
            </Field>
            <Field label="home directory" htmlFor="lu-home" hint="optional">
              <input id="lu-home" className="inp mono" placeholder="e.g. /home/deploy" value={luForm.homeDir} onChange={(e) => setLuForm((f) => ({ ...f, homeDir: e.target.value }))} />
            </Field>
          </div>
          <Field as="div" label="state">
            <select className="inp" value={luForm.state} onChange={(e) => setLuForm((f) => ({ ...f, state: e.target.value as "present" | "absent" }))}>
              <option value="present">present</option>
              <option value="absent">absent</option>
            </select>
          </Field>
          <Field label="ssh authorized keys" htmlFor="lu-keys" hint="one per line">
            <textarea id="lu-keys" className="inp mono" rows={3} value={luForm.authorizedKeys} onChange={(e) => setLuForm((f) => ({ ...f, authorizedKeys: e.target.value }))} />
          </Field>
          <Field label="supplementary groups" htmlFor="lu-groups" hint="comma-separated">
            <input id="lu-groups" className="inp" placeholder="e.g. docker, wheel, sudo" value={luForm.supplementaryGroups} onChange={(e) => setLuForm((f) => ({ ...f, supplementaryGroups: e.target.value }))} />
          </Field>
          <div className="grid gap-[11px] grid-cols-1 sm:grid-cols-[1fr_100px]">
            <Field label="sudo rule" htmlFor="lu-sudo" hint="optional">
              <input id="lu-sudo" className="inp mono" placeholder="e.g. ALL=(ALL) NOPASSWD: ALL" value={luForm.sudoRule} onChange={(e) => setLuForm((f) => ({ ...f, sudoRule: e.target.value }))} />
            </Field>
            <Field label="priority" htmlFor="lu-priority">
              <input id="lu-priority" type="number" min={0} className="inp mono num" value={luForm.priority} onChange={(e) => setLuForm((f) => ({ ...f, priority: Number(e.target.value) }))} required />
            </Field>
          </div>
          <Field label="comment" htmlFor="lu-comment" hint="GECOS / description">
            <input id="lu-comment" className="inp" value={luForm.comment} onChange={(e) => setLuForm((f) => ({ ...f, comment: e.target.value }))} />
          </Field>

          {luSaveMutation.error && <Banner tone="danger">{luSaveMutation.error.message}</Banner>}
        </Modal>
      )}

      {luDeleting && (
        <Confirm
          open
          onOpenChange={(open) => !open && setLuDeleting(null)}
          title="Delete user override"
          description={`Delete the override for "${luDeleting}"? This cannot be undone.`}
          confirmLabel="Delete"
          variant="destructive"
          loading={luDeleteMutation.isPending}
          onConfirm={() => { const o = userOverrides?.find((x) => x.username === luDeleting); if (o) luDeleteMutation.mutate(o.id) }}
        />
      )}

      {lgOpen && (
        <Modal
          title={lgEditingId != null ? "Edit group override" : "Add group override"}
          w={480}
          onClose={() => setLgOpen(false)}
          onSubmit={lgSubmit}
          footer={
            <>
              <button type="button" className="btn ml-auto" onClick={() => setLgOpen(false)}>Cancel</button>
              <button type="submit" className="btn btn-primary" disabled={lgSaveMutation.isPending}>{lgSaveMutation.isPending ? "Saving…" : lgEditingId != null ? "Save changes" : "Create override"}</button>
            </>
          }
        >
          <div className="grid gap-[11px] grid-cols-1 sm:grid-cols-[1fr_100px]">
            <Field label="group name" htmlFor="lg-name">
              <input id="lg-name" className="inp mono" placeholder="e.g. docker, developers" value={lgForm.groupname} onChange={(e) => setLgForm((f) => ({ ...f, groupname: e.target.value }))} required disabled={lgEditingId != null} />
            </Field>
            <Field label="gid" htmlFor="lg-gid" hint="auto if empty">
              <input id="lg-gid" type="number" min={1000} className="inp mono num" value={lgForm.gid} onChange={(e) => setLgForm((f) => ({ ...f, gid: e.target.value }))} />
            </Field>
          </div>
          <div className="grid gap-[11px] grid-cols-1 sm:grid-cols-[1fr_100px]">
            <Field as="div" label="state">
              <select className="inp" value={lgForm.state} onChange={(e) => setLgForm((f) => ({ ...f, state: e.target.value as "present" | "absent" }))}>
                <option value="present">present</option>
                <option value="absent">absent</option>
              </select>
            </Field>
            <Field label="priority" htmlFor="lg-priority">
              <input id="lg-priority" type="number" min={0} className="inp mono num" value={lgForm.priority} onChange={(e) => setLgForm((f) => ({ ...f, priority: Number(e.target.value) }))} required />
            </Field>
          </div>

          {lgSaveMutation.error && <Banner tone="danger">{lgSaveMutation.error.message}</Banner>}
        </Modal>
      )}

      {lgDeleting && (
        <Confirm
          open
          onOpenChange={(open) => !open && setLgDeleting(null)}
          title="Delete group override"
          description={`Delete the override for "${lgDeleting}"? This cannot be undone.`}
          confirmLabel="Delete"
          variant="destructive"
          loading={lgDeleteMutation.isPending}
          onConfirm={() => { const o = groupOverrides?.find((x) => x.groupname === lgDeleting); if (o) lgDeleteMutation.mutate(o.id) }}
        />
      )}

      <CurrentStateSection
        moduleType="linux_user"
        modules={currentState}
        hostId={hostId}
        onManageUser={manageCollectedUser}
        onManageGroup={manageCollectedGroup}
        userHasOverride={(username) => !!userOverrides?.find((o) => o.username === username)}
        groupHasOverride={(groupname) => !!groupOverrides?.find((o) => o.groupname === groupname)}
      />
    </div>
  )
}
