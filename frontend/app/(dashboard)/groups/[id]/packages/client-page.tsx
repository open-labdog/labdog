"use client"

import { useState, type FormEvent } from "react"
import { useParams } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { Loader2Icon } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
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
import { useApiMutation } from "@/lib/mutations"
import { cn, useDelayedLoading } from "@/lib/utils"
import { TableSkeleton } from "@/components/ui/skeleton"
import type { PackageRule, PackageRepository, HostGroup } from "@/lib/types"

function StateBadge({ state }: { state: string }) {
  const colors: Record<string, string> = {
    present: "bg-green-600 text-white",
    absent: "bg-red-600 text-white",
    latest: "bg-blue-600 text-white",
  }
  return (
    <Badge className={colors[state] ?? ""}>
      {state.charAt(0).toUpperCase() + state.slice(1)}
    </Badge>
  )
}

function RepoTypeBadge({ type }: { type: string }) {
  return (
    <Badge variant="outline" className="text-xs font-mono">
      {type}
    </Badge>
  )
}

export default function GroupPackagesPage({ embedded = false }: { embedded?: boolean } = {}) {
  const params = useParams()
  const id = Number(params.id)

  const [pkgDialogOpen, setPkgDialogOpen] = useState(false)
  const [pkgEditing, setPkgEditing] = useState<PackageRule | null>(null)

  const [pkgName, setPkgName] = useState("")
  const [pkgVersion, setPkgVersion] = useState("")
  const [pkgState, setPkgState] = useState<"present" | "absent" | "latest">("present")
  const [pkgManager, setPkgManager] = useState<"auto" | "apt" | "dnf" | "yum">("auto")
  const [pkgComment, setPkgComment] = useState("")
  const [pkgHold, setPkgHold] = useState(false)

  const [repoDialogOpen, setRepoDialogOpen] = useState(false)
  const [repoEditing, setRepoEditing] = useState<PackageRepository | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<PackageRule | null>(null)
  const [uninstallChecked, setUninstallChecked] = useState(false)
  const [confirmState, setConfirmState] = useState<{
    open: boolean; title: string; description: string; action: () => void | Promise<void>; loading?: boolean
  } | null>(null)

  const [repoName, setRepoName] = useState("")
  const [repoUrl, setRepoUrl] = useState("")
  const [repoType, setRepoType] = useState<"apt" | "yum">("apt")
  const [repoDistribution, setRepoDistribution] = useState("")
  const [repoComponents, setRepoComponents] = useState("")
  const [repoKeyUrl, setRepoKeyUrl] = useState("")
  const [repoState, setRepoState] = useState<"present" | "absent">("present")

  const { data: packages = [], isLoading: pkgLoading, error: pkgError } = useQuery<PackageRule[]>({
    queryKey: ["group-packages", id],
    queryFn: () => apiFetch<PackageRule[]>(`/api/groups/${id}/packages`),
    enabled: !!id,
  })
  const showPkgLoading = useDelayedLoading(pkgLoading)

  const { data: repos = [], isLoading: repoLoading, error: repoError } = useQuery<PackageRepository[]>({
    queryKey: ["group-package-repos", id],
    queryFn: () => apiFetch<PackageRepository[]>(`/api/groups/${id}/package-repos`),
    enabled: !!id,
  })
  const showRepoLoading = useDelayedLoading(repoLoading)

  const { data: hostCountData } = useQuery<{ count: number }>({
    queryKey: ["group-host-count", id],
    queryFn: () => apiFetch<{ count: number }>(`/api/groups/${id}/host-count`),
    enabled: !!id,
  })
  const hostCount = hostCountData?.count ?? 0

  const pkgSaveMutation = useApiMutation({
    mutationFn: ({ pkgId, payload }: { pkgId?: number; payload: Record<string, unknown> }) => {
      if (pkgId) return apiFetch(`/api/groups/${id}/packages/${pkgId}`, { method: "PUT", body: JSON.stringify(payload) })
      return apiFetch(`/api/groups/${id}/packages`, { method: "POST", body: JSON.stringify(payload) })
    },
    invalidateKeys: [["group-packages", id]],
    onSuccess: () => setPkgDialogOpen(false),
  })

  const pkgDeleteMutation = useApiMutation({
    mutationFn: ({ pkgId, uninstall }: { pkgId: number; uninstall: boolean }) =>
      apiFetch(`/api/groups/${id}/packages/${pkgId}${uninstall ? "?uninstall=true" : ""}`, { method: "DELETE" }),
    invalidateKeys: [["group-packages", id]],
  })

  const repoSaveMutation = useApiMutation({
    mutationFn: ({ repoId, payload }: { repoId?: number; payload: Record<string, unknown> }) => {
      if (repoId) return apiFetch(`/api/groups/${id}/package-repos/${repoId}`, { method: "PUT", body: JSON.stringify(payload) })
      return apiFetch(`/api/groups/${id}/package-repos`, { method: "POST", body: JSON.stringify(payload) })
    },
    invalidateKeys: [["group-package-repos", id]],
    onSuccess: () => setRepoDialogOpen(false),
  })

  const repoDeleteMutation = useApiMutation({
    mutationFn: (repoId: number) => apiFetch(`/api/groups/${id}/package-repos/${repoId}`, { method: "DELETE" }),
    invalidateKeys: [["group-package-repos", id]],
  })

  function openPkgCreateDialog() {
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

  function openPkgEditDialog(pkg: PackageRule) {
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
    const payload = {
      package_name: pkgName, version: pkgVersion || null, state: pkgState,
      package_manager: pkgManager, comment: pkgComment || null, hold: pkgHold,
    }
    pkgSaveMutation.mutate({ pkgId: pkgEditing?.id, payload })
  }

  function handlePkgDelete(pkg: PackageRule) {
    setDeleteTarget(pkg)
    setUninstallChecked(false)
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

  function openRepoCreateDialog() {
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

  function openRepoEditDialog(repo: PackageRepository) {
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
    const payload = {
      name: repoName, url: repoUrl, repo_type: repoType,
      distribution: repoType === "apt" ? (repoDistribution || null) : null,
      components: repoType === "apt" ? (repoComponents || null) : null,
      key_url: repoKeyUrl || null, state: repoState,
    }
    repoSaveMutation.mutate({ repoId: repoEditing?.id, payload })
  }

  function handleRepoDelete(repo: PackageRepository) {
    setConfirmState({
      open: true,
      title: "Delete Repository",
      description: `Delete repository "${repo.name}"? This action cannot be undone.`,
      action: async () => {
        setConfirmState((prev) => prev ? { ...prev, loading: true } : null)
        try { await repoDeleteMutation.mutateAsync(repo.id) } finally { setConfirmState(null) }
      },
    })
  }

  function truncateUrl(url: string, max = 50): string {
    return url.length > max ? url.slice(0, max) + "..." : url
  }

  const selectClass = "w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30"

  const { data: group } = useQuery<HostGroup>({
    queryKey: ["group", id],
    queryFn: () => apiFetch<HostGroup>(`/api/groups/${id}`),
    enabled: !!id,
  })

  return (
    <div className="space-y-8">
      {!embedded && <Breadcrumb items={[{ label: "Groups", href: "/groups" }, { label: group?.name ?? "Group", href: `/groups/${id}` }, { label: "Packages" }]} />}
      {/* Package Rules Section */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-white">Package Rules</h1>
            <p className="text-slate-400 text-sm mt-1">Group ID: {id}</p>
          </div>
          <Button onClick={openPkgCreateDialog}>Add Package</Button>
        </div>

        {showPkgLoading && <TableSkeleton rows={5} columns={4} />}

        {pkgError && (
          <div className="text-red-400 py-8 text-center">Failed to load packages</div>
        )}

        {!pkgLoading && !pkgError && packages.length === 0 && (
          <div className="text-slate-400 py-8 text-center">
            No package rules yet. Click <strong>Add Package</strong> to create one.
          </div>
        )}

        {!pkgLoading && !pkgError && packages.length > 0 && (
          <div className="rounded-lg border border-slate-700 bg-slate-900">
            <Table>
              <TableHeader>
                <TableRow className="border-slate-700">
                  <TableHead>Package Name</TableHead>
                  <TableHead>Version</TableHead>
                  <TableHead>State</TableHead>
                  <TableHead>Package Manager</TableHead>
                   <TableHead>Hold</TableHead>
                   <TableHead className="w-40">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {packages.map((pkg) => (
                  <TableRow key={pkg.id} className="border-slate-700">
                    <TableCell className="font-mono text-white text-sm">{pkg.package_name}</TableCell>
                    <TableCell className="font-mono text-slate-300 text-xs">{pkg.version ?? "any"}</TableCell>
                    <TableCell>
                      <StateBadge state={pkg.state} />
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline" className="text-xs font-mono">{pkg.package_manager}</Badge>
                    </TableCell>
                     <TableCell>
                       {pkg.hold ? (
                         <span className="text-xs px-1.5 py-0.5 rounded bg-amber-900/50 text-amber-400">held</span>
                       ) : (
                         <span className="text-slate-600">—</span>
                       )}
                     </TableCell>
                     <TableCell>
                       <div className="flex gap-1">
                         <Button
                           size="sm"
                           variant="ghost"
                           onClick={() => openPkgEditDialog(pkg)}
                        >
                          Edit
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={pkgDeleteMutation.isPending}
                          onClick={() => handlePkgDelete(pkg)}
                          className="text-red-400 hover:text-red-300 hover:bg-red-950"
                        >
                          {pkgDeleteMutation.isPending ? "..." : "Delete"}
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

      <hr className="border-slate-700" />

      {/* Package Repositories Section */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-xl font-bold text-white">Package Repositories</h2>
            <p className="text-slate-400 text-sm mt-1">Custom package sources for this group</p>
          </div>
          <Button onClick={openRepoCreateDialog}>Add Repository</Button>
        </div>

        {showRepoLoading && <TableSkeleton rows={5} columns={4} />}

        {repoError && (
          <div className="text-red-400 py-8 text-center">Failed to load repositories</div>
        )}

        {!repoLoading && !repoError && repos.length === 0 && (
          <div className="text-slate-400 py-8 text-center">
            No repositories yet. Click <strong>Add Repository</strong> to create one.
          </div>
        )}

        {!repoLoading && !repoError && repos.length > 0 && (
          <div className="rounded-lg border border-slate-700 bg-slate-900">
            <Table>
              <TableHeader>
                <TableRow className="border-slate-700">
                  <TableHead>Name</TableHead>
                  <TableHead>URL</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Distribution</TableHead>
                  <TableHead>State</TableHead>
                  <TableHead className="w-40">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {repos.map((repo) => (
                  <TableRow key={repo.id} className="border-slate-700">
                    <TableCell className="font-mono text-white text-sm">{repo.name}</TableCell>
                    <TableCell className="font-mono text-slate-300 text-xs max-w-[240px]">
                      <span title={repo.url}>{truncateUrl(repo.url)}</span>
                    </TableCell>
                    <TableCell>
                      <RepoTypeBadge type={repo.repo_type} />
                    </TableCell>
                    <TableCell className="text-slate-300 text-xs">
                      {repo.distribution ?? "—"}
                    </TableCell>
                    <TableCell>
                      <StateBadge state={repo.state} />
                    </TableCell>
                    <TableCell>
                      <div className="flex gap-1">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => openRepoEditDialog(repo)}
                        >
                          Edit
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={repoDeleteMutation.isPending}
                          onClick={() => handleRepoDelete(repo)}
                          className="text-red-400 hover:text-red-300 hover:bg-red-950"
                        >
                          {repoDeleteMutation.isPending ? "..." : "Delete"}
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

      {/* Package Rule Dialog */}
      <Dialog open={pkgDialogOpen} onOpenChange={setPkgDialogOpen}>
        <DialogContent className="sm:max-w-lg max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{pkgEditing ? "Edit Package Rule" : "Add Package Rule"}</DialogTitle>
          </DialogHeader>
          <form onSubmit={handlePkgSubmit} className="space-y-4 mt-2">
            <div className="space-y-2">
              <Label htmlFor="pkg-name">Package Name</Label>
              <Input
                id="pkg-name"
                type="text"
                placeholder="e.g. nginx, curl, htop"
                value={pkgName}
                onChange={(e) => setPkgName(e.target.value)}
                required
                readOnly={!!pkgEditing}
                className={pkgEditing ? "opacity-60" : ""}
              />
              {pkgEditing && (
                <p className="text-xs text-slate-500">Package name cannot be changed after creation</p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="pkg-version">Version</Label>
              <Input
                id="pkg-version"
                type="text"
                placeholder="any version"
                value={pkgVersion}
                onChange={(e) => setPkgVersion(e.target.value)}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="pkg-state">State</Label>
              <select
                id="pkg-state"
                value={pkgState}
                onChange={(e) => setPkgState(e.target.value as "present" | "absent" | "latest")}
                className={selectClass}
              >
                <option value="present">Present</option>
                <option value="absent">Absent</option>
                <option value="latest">Latest</option>
              </select>
            </div>

            <div className="space-y-2">
              <Label htmlFor="pkg-manager">Package Manager</Label>
              <select
                id="pkg-manager"
                value={pkgManager}
                onChange={(e) => setPkgManager(e.target.value as "auto" | "apt" | "dnf" | "yum")}
                className={selectClass}
              >
                <option value="auto">Auto-detect</option>
                <option value="apt">apt</option>
                <option value="dnf">dnf</option>
                <option value="yum">yum</option>
              </select>
            </div>

            <div className="space-y-2">
              <Label htmlFor="pkg-comment">Comment (optional)</Label>
              <textarea
                id="pkg-comment"
                placeholder="Optional description"
                value={pkgComment}
                onChange={(e) => setPkgComment(e.target.value)}
                rows={2}
                className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30 resize-y"
              />
            </div>

            <div className="flex items-center gap-2">
              <input
                id="pkg-hold"
                type="checkbox"
                checked={pkgHold}
                onChange={(e) => setPkgHold(e.target.checked)}
                className="rounded border-input"
              />
              <Label htmlFor="pkg-hold">Hold package</Label>
              <span className="text-xs text-slate-500">Prevent automatic upgrades</span>
            </div>

            {pkgSaveMutation.error && (
              <p className="text-sm text-red-400">{pkgSaveMutation.error.message}</p>
            )}

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setPkgDialogOpen(false)}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={pkgSaveMutation.isPending}>
                {pkgSaveMutation.isPending ? "Saving..." : pkgEditing ? "Save Changes" : "Create"}
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

      <Dialog open={!!deleteTarget} onOpenChange={(open) => { if (!open) { setDeleteTarget(null); setUninstallChecked(false) } }}>
        <DialogContent showCloseButton={false}>
          <DialogHeader>
            <DialogTitle>Delete Package Rule</DialogTitle>
            <DialogDescription>
              {uninstallChecked
                ? `This will set "${deleteTarget?.package_name}" to absent and trigger a sync to uninstall it from ${hostCount} host(s). The rule stays until you remove it after sync completes.`
                : `Remove "${deleteTarget?.package_name}" from this group?${hostCount > 0 ? ` The package will remain installed on ${hostCount} host(s).` : ""}`}
            </DialogDescription>
          </DialogHeader>
          {hostCount > 0 && (
            <div className={cn(
              "rounded-lg border p-3",
              uninstallChecked ? "border-amber-600 bg-amber-950/30" : "border-slate-700 bg-slate-800/50"
            )}>
              <label className="flex items-start gap-2.5 cursor-pointer">
                <input
                  type="checkbox"
                  checked={uninstallChecked}
                  onChange={(e) => setUninstallChecked(e.target.checked)}
                  className="mt-1 h-4 w-4 rounded border-slate-600 bg-slate-800 accent-amber-500"
                />
                <div>
                  <div className="text-sm font-medium text-slate-200">
                    Also uninstall {deleteTarget?.package_name} from {hostCount} host(s)
                  </div>
                  <div className="text-xs text-slate-400 mt-0.5">
                    Sets state to absent and triggers a package sync. If the sync fails, drift detection will flag affected hosts so you can retry.
                  </div>
                </div>
              </label>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => { setDeleteTarget(null); setUninstallChecked(false) }} disabled={pkgDeleteMutation.isPending}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={handleConfirmPkgDelete} disabled={pkgDeleteMutation.isPending}>
              {pkgDeleteMutation.isPending && <Loader2Icon className="animate-spin mr-1 h-4 w-4" />}
              {uninstallChecked ? "Uninstall + Sync" : "Delete Rule"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Repository Dialog */}
      <Dialog open={repoDialogOpen} onOpenChange={setRepoDialogOpen}>
        <DialogContent className="sm:max-w-lg max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{repoEditing ? "Edit Repository" : "Add Repository"}</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleRepoSubmit} className="space-y-4 mt-2">
            <div className="space-y-2">
              <Label htmlFor="repo-name">Name</Label>
              <Input
                id="repo-name"
                type="text"
                placeholder="e.g. docker-ce, grafana"
                value={repoName}
                onChange={(e) => setRepoName(e.target.value)}
                required
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="repo-url">URL</Label>
              <Input
                id="repo-url"
                type="text"
                placeholder="https://download.docker.com/linux/ubuntu"
                value={repoUrl}
                onChange={(e) => setRepoUrl(e.target.value)}
                required
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="repo-type">Repository Type</Label>
              <select
                id="repo-type"
                value={repoType}
                onChange={(e) => setRepoType(e.target.value as "apt" | "yum")}
                className={selectClass}
              >
                <option value="apt">apt</option>
                <option value="yum">yum</option>
              </select>
            </div>

            {repoType === "apt" && (
              <>
                <div className="space-y-2">
                  <Label htmlFor="repo-dist">Distribution</Label>
                  <Input
                    id="repo-dist"
                    type="text"
                    placeholder="e.g. jammy"
                    value={repoDistribution}
                    onChange={(e) => setRepoDistribution(e.target.value)}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="repo-components">Components</Label>
                  <Input
                    id="repo-components"
                    type="text"
                    placeholder="e.g. main"
                    value={repoComponents}
                    onChange={(e) => setRepoComponents(e.target.value)}
                  />
                </div>
              </>
            )}

            <div className="space-y-2">
              <Label htmlFor="repo-key">GPG Key URL (optional)</Label>
              <Input
                id="repo-key"
                type="text"
                placeholder="https://download.docker.com/linux/ubuntu/gpg"
                value={repoKeyUrl}
                onChange={(e) => setRepoKeyUrl(e.target.value)}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="repo-state">State</Label>
              <select
                id="repo-state"
                value={repoState}
                onChange={(e) => setRepoState(e.target.value as "present" | "absent")}
                className={selectClass}
              >
                <option value="present">Present</option>
                <option value="absent">Absent</option>
              </select>
            </div>

            {repoSaveMutation.error && (
              <p className="text-sm text-red-400">{repoSaveMutation.error.message}</p>
            )}

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setRepoDialogOpen(false)}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={repoSaveMutation.isPending}>
                {repoSaveMutation.isPending ? "Saving..." : repoEditing ? "Save Changes" : "Create"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  )
}
