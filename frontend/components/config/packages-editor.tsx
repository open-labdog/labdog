"use client"

import { useState, type FormEvent } from "react"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { plural } from "@/lib/fleet"
import { ITEM_STATE, PACKAGE_STATE, def } from "@/lib/status"
import type { PackageRepository, PackageRule } from "@/lib/types"
import { Banner, Confirm, Field, Modal, Table, Tag, Toolbar } from "@/components/ld"
import { GROUP_KEYS, GitOpsBanner, stop, useEditorGroup } from "./shared"

type PkgState = PackageRule["state"]
type PkgManager = PackageRule["package_manager"]
type RepoType = PackageRepository["repo_type"]

/**
 * Packages a group declares — present, absent or latest, optionally
 * held — and the repositories they come from. Two tables under one
 * toolbar; the repositories go below the packages, which is the order
 * a sync applies them in reverse. Embedded in the group page's Config tab.
 */
export function PackagesEditor({ groupId }: { groupId: number }) {
  const { group, gitops } = useEditorGroup(groupId)

  const [pkgDialogOpen, setPkgDialogOpen] = useState(false)
  const [pkgEditing, setPkgEditing] = useState<PackageRule | null>(null)
  const [pkgName, setPkgName] = useState("")
  const [pkgVersion, setPkgVersion] = useState("")
  const [pkgState, setPkgState] = useState<PkgState>("present")
  const [pkgManager, setPkgManager] = useState<PkgManager>("auto")
  const [pkgComment, setPkgComment] = useState("")
  const [pkgHold, setPkgHold] = useState(false)

  const [repoDialogOpen, setRepoDialogOpen] = useState(false)
  const [repoEditing, setRepoEditing] = useState<PackageRepository | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<PackageRule | null>(null)
  const [uninstallChecked, setUninstallChecked] = useState(false)
  const [repoDeleting, setRepoDeleting] = useState<PackageRepository | null>(null)

  const [repoName, setRepoName] = useState("")
  const [repoUrl, setRepoUrl] = useState("")
  const [repoType, setRepoType] = useState<RepoType>("apt")
  const [repoDistribution, setRepoDistribution] = useState("")
  const [repoComponents, setRepoComponents] = useState("")
  const [repoKeyUrl, setRepoKeyUrl] = useState("")
  const [repoState, setRepoState] = useState<"present" | "absent">("present")

  const { data: packages = [], isLoading: pkgLoading, error: pkgError } = useQuery<PackageRule[]>({
    queryKey: ["group-packages", groupId],
    queryFn: () => apiFetch<PackageRule[]>(`/api/groups/${groupId}/packages`),
    enabled: !!groupId,
  })
  const { data: repos = [], isLoading: repoLoading, error: repoError } = useQuery<PackageRepository[]>({
    queryKey: ["group-package-repos", groupId],
    queryFn: () => apiFetch<PackageRepository[]>(`/api/groups/${groupId}/package-repos`),
    enabled: !!groupId,
  })
  const { data: hostCountData } = useQuery<{ count: number }>({
    queryKey: ["group-host-count", groupId],
    queryFn: () => apiFetch<{ count: number }>(`/api/groups/${groupId}/host-count`),
    enabled: !!groupId,
  })
  const hostCount = hostCountData?.count ?? 0

  const pkgSaveMutation = useApiMutation({
    mutationFn: ({ pkgId, payload }: { pkgId?: number; payload: Record<string, unknown> }) =>
      pkgId
        ? apiFetch(`/api/groups/${groupId}/packages/${pkgId}`, { method: "PUT", body: JSON.stringify(payload) })
        : apiFetch(`/api/groups/${groupId}/packages`, { method: "POST", body: JSON.stringify(payload) }),
    invalidateKeys: [["group-packages", groupId], ...GROUP_KEYS],
    onSuccess: () => setPkgDialogOpen(false),
  })
  const pkgDeleteMutation = useApiMutation({
    mutationFn: ({ pkgId, uninstall }: { pkgId: number; uninstall: boolean }) =>
      apiFetch(`/api/groups/${groupId}/packages/${pkgId}${uninstall ? "?uninstall=true" : ""}`, { method: "DELETE" }),
    invalidateKeys: [["group-packages", groupId], ...GROUP_KEYS],
  })
  const repoSaveMutation = useApiMutation({
    mutationFn: ({ repoId, payload }: { repoId?: number; payload: Record<string, unknown> }) =>
      repoId
        ? apiFetch(`/api/groups/${groupId}/package-repos/${repoId}`, { method: "PUT", body: JSON.stringify(payload) })
        : apiFetch(`/api/groups/${groupId}/package-repos`, { method: "POST", body: JSON.stringify(payload) }),
    invalidateKeys: [["group-package-repos", groupId], ...GROUP_KEYS],
    onSuccess: () => setRepoDialogOpen(false),
  })
  const repoDeleteMutation = useApiMutation({
    mutationFn: (repoId: number) => apiFetch(`/api/groups/${groupId}/package-repos/${repoId}`, { method: "DELETE" }),
    invalidateKeys: [["group-package-repos", groupId], ...GROUP_KEYS],
    onSuccess: () => setRepoDeleting(null),
  })

  function openPkgCreate() {
    setPkgEditing(null)
    setPkgName("")
    setPkgVersion("")
    setPkgState("present")
    setPkgManager("auto")
    setPkgComment("")
    setPkgHold(false)
    pkgSaveMutation.reset()
    setPkgDialogOpen(true)
  }

  function openPkgEdit(pkg: PackageRule) {
    setPkgEditing(pkg)
    setPkgName(pkg.package_name)
    setPkgVersion(pkg.version ?? "")
    setPkgState(pkg.state)
    setPkgManager(pkg.package_manager)
    setPkgComment(pkg.comment ?? "")
    setPkgHold(pkg.hold)
    pkgSaveMutation.reset()
    setPkgDialogOpen(true)
  }

  function handlePkgSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    pkgSaveMutation.mutate({
      pkgId: pkgEditing?.id,
      payload: { package_name: pkgName, version: pkgVersion || null, state: pkgState, package_manager: pkgManager, comment: pkgComment || null, hold: pkgHold },
    })
  }

  async function handleConfirmPkgDelete() {
    if (!deleteTarget) return
    try {
      await pkgDeleteMutation.mutateAsync({ pkgId: deleteTarget.id, uninstall: uninstallChecked })
    } finally {
      setDeleteTarget(null)
      setUninstallChecked(false)
    }
  }

  function openRepoCreate() {
    setRepoEditing(null)
    setRepoName("")
    setRepoUrl("")
    setRepoType("apt")
    setRepoDistribution("")
    setRepoComponents("")
    setRepoKeyUrl("")
    setRepoState("present")
    repoSaveMutation.reset()
    setRepoDialogOpen(true)
  }

  function openRepoEdit(repo: PackageRepository) {
    setRepoEditing(repo)
    setRepoName(repo.name)
    setRepoUrl(repo.url)
    setRepoType(repo.repo_type)
    setRepoDistribution(repo.distribution ?? "")
    setRepoComponents(repo.components ?? "")
    setRepoKeyUrl(repo.key_url ?? "")
    setRepoState(repo.state)
    repoSaveMutation.reset()
    setRepoDialogOpen(true)
  }

  function handleRepoSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    repoSaveMutation.mutate({
      repoId: repoEditing?.id,
      payload: {
        name: repoName,
        url: repoUrl,
        repo_type: repoType,
        distribution: repoType === "apt" ? repoDistribution || null : null,
        components: repoType === "apt" ? repoComponents || null : null,
        key_url: repoKeyUrl || null,
        state: repoState,
      },
    })
  }

  const closePkgDelete = () => {
    setDeleteTarget(null)
    setUninstallChecked(false)
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Toolbar
        actions={
          !gitops && (
            <>
              <button type="button" className="btn btn-sm" onClick={openRepoCreate}>
                Add repository
              </button>
              <button type="button" className="btn btn-sm btn-primary" onClick={openPkgCreate}>
                Add package
              </button>
            </>
          )
        }
      >
        <span className="tt">
          {plural(packages.length, "package")} · {repos.length} {repos.length === 1 ? "repository" : "repositories"} declared here
        </span>
      </Toolbar>
      {gitops && <GitOpsBanner group={group} what="packages and repositories are" />}
      {pkgError && (
        <Banner tone="danger" flush>
          Could not load packages: {pkgError.message}
        </Banner>
      )}

      <div className="scroll flex min-h-0 flex-1 flex-col">
        <div className="flex shrink-0 flex-col">
        <Table<PackageRule>
          cols={[
            { k: "name", label: "package", w: "minmax(160px,1.2fr)", sortable: false, cell: (p) => <span className="mono font-medium text-text">{p.package_name}</span> },
            { k: "version", label: "version", w: "120px", sortable: false, cell: (p) => <span className="mono text-[11px]">{p.version ?? <span className="text-text-faint">any</span>}</span> },
            { k: "state", label: "state", w: "90px", sortable: false, cell: (p) => <Tag tone={def(PACKAGE_STATE, p.state).tone}>{p.state}</Tag> },
            { k: "manager", label: "manager", w: "90px", sortable: false, cell: (p) => <Tag>{p.package_manager}</Tag> },
            { k: "hold", label: "hold", w: "70px", sortable: false, cell: (p) => (p.hold ? <Tag tone="hold">held</Tag> : <span className="text-text-faint">—</span>) },
            { k: "comment", label: "comment", w: "minmax(120px,1fr)", sortable: false, cell: (p) => <span className="text-[11.5px] text-text-3">{p.comment ?? ""}</span> },
            {
              k: "actions",
              label: "",
              w: "118px",
              right: true,
              sortable: false,
              cell: (p) => (
                <span className="flex gap-0.5" onClick={stop}>
                  <button type="button" className="btn btn-sm btn-ghost" disabled={gitops} onClick={() => openPkgEdit(p)}>
                    edit
                  </button>
                  <button
                    type="button"
                    className="btn btn-sm btn-ghost text-danger"
                    disabled={gitops || pkgDeleteMutation.isPending}
                    onClick={() => {
                      setDeleteTarget(p)
                      setUninstallChecked(false)
                    }}
                  >
                    delete
                  </button>
                </span>
              ),
            },
          ]}
          rows={packages}
          keyOf={(p) => p.id}
          onRowClick={gitops ? undefined : openPkgEdit}
          loading={pkgLoading}
          empty="Nothing declared. Add package declares this module for the group; nothing is installed until a plan runs."
        />
        </div>

        <div className="tt shrink-0 border-y border-line bg-surface-2 px-[13px] py-[7px]">repositories · {plural(repos.length, "source")}</div>
        {repoError && <Banner tone="danger">Could not load repositories: {repoError.message}</Banner>}
        <div className="flex shrink-0 flex-col">
        <Table<PackageRepository>
          cols={[
            { k: "name", label: "repository", w: "minmax(140px,1fr)", sortable: false, cell: (r) => <span className="mono font-medium text-text">{r.name}</span> },
            { k: "url", label: "url", w: "minmax(200px,1.8fr)", sortable: false, cell: (r) => <span className="mono text-[11px]" title={r.url}>{r.url}</span> },
            { k: "type", label: "type", w: "70px", sortable: false, cell: (r) => <Tag>{r.repo_type}</Tag> },
            { k: "dist", label: "distribution", w: "150px", sortable: false, cell: (r) => <span className="mono text-[11px]">{r.repo_type === "apt" ? [r.distribution, r.components].filter(Boolean).join(" · ") || <span className="text-text-faint">—</span> : <span className="text-text-faint">—</span>}</span> },
            { k: "state", label: "state", w: "90px", sortable: false, cell: (r) => <Tag tone={def(ITEM_STATE, r.state).tone}>{r.state}</Tag> },
            {
              k: "actions",
              label: "",
              w: "118px",
              right: true,
              sortable: false,
              cell: (r) => (
                <span className="flex gap-0.5" onClick={stop}>
                  <button type="button" className="btn btn-sm btn-ghost" disabled={gitops} onClick={() => openRepoEdit(r)}>
                    edit
                  </button>
                  <button type="button" className="btn btn-sm btn-ghost text-danger" disabled={gitops || repoDeleteMutation.isPending} onClick={() => setRepoDeleting(r)}>
                    delete
                  </button>
                </span>
              ),
            },
          ]}
          rows={repos}
          keyOf={(r) => r.id}
          onRowClick={gitops ? undefined : openRepoEdit}
          loading={repoLoading}
          empty="No repositories declared — packages come from the hosts' own sources."
        />
        </div>
      </div>

      {pkgDialogOpen && (
        <Modal
          title={pkgEditing ? "Edit package rule" : "Add package rule"}
          meta={group ? `group: ${group.name}` : undefined}
          w={520}
          onClose={() => setPkgDialogOpen(false)}
          onSubmit={handlePkgSubmit}
          footer={
            <>
              <span className="tt mr-auto">desired state only — nothing applies until a plan runs</span>
              <button type="button" className="btn" onClick={() => setPkgDialogOpen(false)}>
                Cancel
              </button>
              <button type="submit" className="btn btn-primary" disabled={pkgSaveMutation.isPending || !pkgName.trim()}>
                {pkgSaveMutation.isPending ? "Saving…" : pkgEditing ? "Save changes" : "Add package"}
              </button>
            </>
          }
        >
          <div className="grid gap-[11px] grid-cols-1 sm:grid-cols-[1fr_140px]">
            <Field label="package" htmlFor="pkg-name" hint={pkgEditing ? "the name cannot change — add a new rule instead" : undefined}>
              <input id="pkg-name" className="inp mono" placeholder="nginx, curl, htop" value={pkgName} onChange={(e) => setPkgName(e.target.value)} readOnly={!!pkgEditing} required />
            </Field>
            <Field label="version" htmlFor="pkg-version" hint="blank = any">
              <input id="pkg-version" className="inp mono" value={pkgVersion} onChange={(e) => setPkgVersion(e.target.value)} />
            </Field>
          </div>
          <div className="grid gap-[11px] grid-cols-1 sm:grid-cols-[1fr_1fr]">
            <Field label="state" htmlFor="pkg-state">
              <select id="pkg-state" className="inp" value={pkgState} onChange={(e) => setPkgState(e.target.value as PkgState)}>
                <option value="present">present</option>
                <option value="latest">latest</option>
                <option value="absent">absent</option>
              </select>
            </Field>
            <Field label="package manager" htmlFor="pkg-manager">
              <select id="pkg-manager" className="inp" value={pkgManager} onChange={(e) => setPkgManager(e.target.value as PkgManager)}>
                <option value="auto">auto — by OS family</option>
                <option value="apt">apt</option>
                <option value="dnf">dnf</option>
                <option value="yum">yum</option>
              </select>
            </Field>
          </div>
          <Field label="comment" htmlFor="pkg-comment" hint="optional">
            <textarea id="pkg-comment" className="inp" rows={2} value={pkgComment} onChange={(e) => setPkgComment(e.target.value)} />
          </Field>
          <label className="flex items-center gap-2 text-xs text-text">
            <input id="pkg-hold" type="checkbox" checked={pkgHold} onChange={(e) => setPkgHold(e.target.checked)} />
            hold the package — pin it against upgrades
          </label>
          {pkgSaveMutation.error && <Banner tone="danger">{pkgSaveMutation.error.message}</Banner>}
        </Modal>
      )}

      {deleteTarget && (
        <Modal
          title="Delete package rule"
          meta={deleteTarget.package_name}
          w={460}
          onClose={closePkgDelete}
          footer={
            <>
              <button type="button" className="btn ml-auto" onClick={closePkgDelete} disabled={pkgDeleteMutation.isPending}>
                Cancel
              </button>
              <button type="button" className="btn btn-danger" onClick={() => void handleConfirmPkgDelete()} disabled={pkgDeleteMutation.isPending}>
                {pkgDeleteMutation.isPending ? "Deleting…" : uninstallChecked ? "Uninstall + sync" : "Delete rule"}
              </button>
            </>
          }
        >
          <div className="text-[12.5px] leading-[1.55] text-text-2">
            {uninstallChecked
              ? `${deleteTarget.package_name} is set to absent and a sync uninstalls it from ${plural(hostCount, "host")}. The rule stays until you remove it after the sync completes.`
              : `${deleteTarget.package_name} is no longer declared by this group.${hostCount > 0 ? ` It stays installed on ${plural(hostCount, "host")}.` : ""}`}
          </div>
          {hostCount > 0 && (
            <label className="flex items-center gap-2 text-xs text-text">
              <input type="checkbox" checked={uninstallChecked} onChange={(e) => setUninstallChecked(e.target.checked)} />
              also uninstall {deleteTarget.package_name} from {plural(hostCount, "host")}
            </label>
          )}
        </Modal>
      )}

      {repoDialogOpen && (
        <Modal
          title={repoEditing ? "Edit repository" : "Add repository"}
          meta={group ? `group: ${group.name}` : undefined}
          w={560}
          onClose={() => setRepoDialogOpen(false)}
          onSubmit={handleRepoSubmit}
          footer={
            <>
              <span className="tt mr-auto">added to the hosts before packages are installed</span>
              <button type="button" className="btn" onClick={() => setRepoDialogOpen(false)}>
                Cancel
              </button>
              <button type="submit" className="btn btn-primary" disabled={repoSaveMutation.isPending || !repoName.trim() || !repoUrl.trim()}>
                {repoSaveMutation.isPending ? "Saving…" : repoEditing ? "Save changes" : "Add repository"}
              </button>
            </>
          }
        >
          <div className="grid gap-[11px] grid-cols-1 sm:grid-cols-[160px_1fr_90px]">
            <Field label="name" htmlFor="repo-name">
              <input id="repo-name" className="inp mono" placeholder="docker-ce" value={repoName} onChange={(e) => setRepoName(e.target.value)} required />
            </Field>
            <Field label="url" htmlFor="repo-url">
              <input id="repo-url" className="inp mono" placeholder="https://download.docker.com/linux/ubuntu" value={repoUrl} onChange={(e) => setRepoUrl(e.target.value)} required />
            </Field>
            <Field label="type" htmlFor="repo-type">
              <select id="repo-type" className="inp" value={repoType} onChange={(e) => setRepoType(e.target.value as RepoType)}>
                <option value="apt">apt</option>
                <option value="yum">yum</option>
              </select>
            </Field>
          </div>
          {repoType === "apt" && (
            <div className="grid gap-[11px] grid-cols-1 sm:grid-cols-[1fr_1fr]">
              <Field label="distribution" htmlFor="repo-dist" hint="e.g. jammy">
                <input id="repo-dist" className="inp mono" value={repoDistribution} onChange={(e) => setRepoDistribution(e.target.value)} />
              </Field>
              <Field label="components" htmlFor="repo-components" hint="e.g. main">
                <input id="repo-components" className="inp mono" value={repoComponents} onChange={(e) => setRepoComponents(e.target.value)} />
              </Field>
            </div>
          )}
          <div className="grid gap-[11px] grid-cols-1 sm:grid-cols-[1fr_120px]">
            <Field label="gpg key url" htmlFor="repo-key" hint="optional">
              <input id="repo-key" className="inp mono" placeholder="https://download.docker.com/linux/ubuntu/gpg" value={repoKeyUrl} onChange={(e) => setRepoKeyUrl(e.target.value)} />
            </Field>
            <Field label="state" htmlFor="repo-state">
              <select id="repo-state" className="inp" value={repoState} onChange={(e) => setRepoState(e.target.value as "present" | "absent")}>
                <option value="present">present</option>
                <option value="absent">absent</option>
              </select>
            </Field>
          </div>
          {repoSaveMutation.error && <Banner tone="danger">{repoSaveMutation.error.message}</Banner>}
        </Modal>
      )}

      {repoDeleting && (
        <Confirm
          open
          onOpenChange={(open) => !open && setRepoDeleting(null)}
          title="Delete repository"
          description={`${repoDeleting.name} is no longer declared by this group; hosts keep the source until a plan runs. This cannot be undone.`}
          confirmLabel="Delete"
          variant="destructive"
          loading={repoDeleteMutation.isPending}
          onConfirm={() => repoDeleteMutation.mutate(repoDeleting.id)}
        />
      )}
    </div>
  )
}
