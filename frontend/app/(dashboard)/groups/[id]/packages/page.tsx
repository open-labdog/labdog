"use client"

import { useState, type FormEvent } from "react"
import { useParams } from "next/navigation"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
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
import { apiFetch } from "@/lib/api"
import type { PackageRule, PackageRepository } from "@/lib/types"

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

export default function GroupPackagesPage() {
  const params = useParams()
  const id = Number(params.id)
  const queryClient = useQueryClient()

  const [pkgDialogOpen, setPkgDialogOpen] = useState(false)
  const [pkgEditing, setPkgEditing] = useState<PackageRule | null>(null)
  const [pkgDeletingId, setPkgDeletingId] = useState<number | null>(null)
  const [pkgDeleteError, setPkgDeleteError] = useState<string | null>(null)
  const [pkgFormError, setPkgFormError] = useState<string | null>(null)
  const [pkgFormLoading, setPkgFormLoading] = useState(false)

  const [pkgName, setPkgName] = useState("")
  const [pkgVersion, setPkgVersion] = useState("")
  const [pkgState, setPkgState] = useState<"present" | "absent" | "latest">("present")
  const [pkgManager, setPkgManager] = useState<"auto" | "apt" | "dnf" | "yum">("auto")
  const [pkgPriority, setPkgPriority] = useState(0)
  const [pkgComment, setPkgComment] = useState("")

  const [repoDialogOpen, setRepoDialogOpen] = useState(false)
  const [repoEditing, setRepoEditing] = useState<PackageRepository | null>(null)
  const [repoDeletingId, setRepoDeletingId] = useState<number | null>(null)
  const [repoDeleteError, setRepoDeleteError] = useState<string | null>(null)
  const [repoFormError, setRepoFormError] = useState<string | null>(null)
  const [repoFormLoading, setRepoFormLoading] = useState(false)

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

  const { data: repos = [], isLoading: repoLoading, error: repoError } = useQuery<PackageRepository[]>({
    queryKey: ["group-package-repos", id],
    queryFn: () => apiFetch<PackageRepository[]>(`/api/groups/${id}/package-repos`),
    enabled: !!id,
  })

  function openPkgCreateDialog() {
    setPkgEditing(null)
    setPkgName("")
    setPkgVersion("")
    setPkgState("present")
    setPkgManager("auto")
    setPkgPriority(0)
    setPkgComment("")
    setPkgFormError(null)
    setPkgDialogOpen(true)
  }

  function openPkgEditDialog(pkg: PackageRule) {
    setPkgEditing(pkg)
    setPkgName(pkg.package_name)
    setPkgVersion(pkg.version ?? "")
    setPkgState(pkg.state)
    setPkgManager(pkg.package_manager)
    setPkgPriority(pkg.priority)
    setPkgComment(pkg.comment ?? "")
    setPkgFormError(null)
    setPkgDialogOpen(true)
  }

  async function handlePkgSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setPkgFormError(null)
    setPkgFormLoading(true)

    const payload = {
      package_name: pkgName,
      version: pkgVersion || null,
      state: pkgState,
      package_manager: pkgManager,
      priority: pkgPriority,
      comment: pkgComment || null,
    }

    try {
      if (pkgEditing) {
        await apiFetch(`/api/groups/${id}/packages/${pkgEditing.id}`, {
          method: "PUT",
          body: JSON.stringify(payload),
        })
      } else {
        await apiFetch(`/api/groups/${id}/packages`, {
          method: "POST",
          body: JSON.stringify(payload),
        })
      }
      await queryClient.invalidateQueries({ queryKey: ["group-packages", id] })
      setPkgDialogOpen(false)
    } catch (err) {
      setPkgFormError(err instanceof Error ? err.message : "Failed to save package rule")
    } finally {
      setPkgFormLoading(false)
    }
  }

  async function handlePkgDelete(pkg: PackageRule) {
    if (!confirm(`Delete package rule "${pkg.package_name}"?`)) return
    setPkgDeletingId(pkg.id)
    setPkgDeleteError(null)
    try {
      await apiFetch(`/api/groups/${id}/packages/${pkg.id}`, { method: "DELETE" })
      await queryClient.invalidateQueries({ queryKey: ["group-packages", id] })
    } catch (err) {
      setPkgDeleteError(err instanceof Error ? err.message : "Delete failed")
    } finally {
      setPkgDeletingId(null)
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
    setRepoFormError(null)
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
    setRepoFormError(null)
    setRepoDialogOpen(true)
  }

  async function handleRepoSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setRepoFormError(null)
    setRepoFormLoading(true)

    const payload = {
      name: repoName,
      url: repoUrl,
      repo_type: repoType,
      distribution: repoType === "apt" ? (repoDistribution || null) : null,
      components: repoType === "apt" ? (repoComponents || null) : null,
      key_url: repoKeyUrl || null,
      state: repoState,
    }

    try {
      if (repoEditing) {
        await apiFetch(`/api/groups/${id}/package-repos/${repoEditing.id}`, {
          method: "PUT",
          body: JSON.stringify(payload),
        })
      } else {
        await apiFetch(`/api/groups/${id}/package-repos`, {
          method: "POST",
          body: JSON.stringify(payload),
        })
      }
      await queryClient.invalidateQueries({ queryKey: ["group-package-repos", id] })
      setRepoDialogOpen(false)
    } catch (err) {
      setRepoFormError(err instanceof Error ? err.message : "Failed to save repository")
    } finally {
      setRepoFormLoading(false)
    }
  }

  async function handleRepoDelete(repo: PackageRepository) {
    if (!confirm(`Delete repository "${repo.name}"?`)) return
    setRepoDeletingId(repo.id)
    setRepoDeleteError(null)
    try {
      await apiFetch(`/api/groups/${id}/package-repos/${repo.id}`, { method: "DELETE" })
      await queryClient.invalidateQueries({ queryKey: ["group-package-repos", id] })
    } catch (err) {
      setRepoDeleteError(err instanceof Error ? err.message : "Delete failed")
    } finally {
      setRepoDeletingId(null)
    }
  }

  function truncateUrl(url: string, max = 50): string {
    return url.length > max ? url.slice(0, max) + "..." : url
  }

  const selectClass = "w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30"

  return (
    <div className="space-y-8">
      {/* Package Rules Section */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-white">Package Rules</h1>
            <p className="text-slate-400 text-sm mt-1">Group ID: {id}</p>
          </div>
          <Button onClick={openPkgCreateDialog}>Add Package</Button>
        </div>

        {pkgLoading && (
          <div className="text-slate-400 py-8 text-center">Loading packages...</div>
        )}

        {pkgError && (
          <div className="text-red-400 py-8 text-center">Failed to load packages</div>
        )}

        {pkgDeleteError && (
          <div className="text-red-400 text-sm">{pkgDeleteError}</div>
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
                  <TableHead>Priority</TableHead>
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
                    <TableCell className="font-mono text-slate-300 text-xs">{pkg.priority}</TableCell>
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
                          disabled={pkgDeletingId === pkg.id}
                          onClick={() => handlePkgDelete(pkg)}
                          className="text-red-400 hover:text-red-300 hover:bg-red-950"
                        >
                          {pkgDeletingId === pkg.id ? "..." : "Delete"}
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

        {repoLoading && (
          <div className="text-slate-400 py-8 text-center">Loading repositories...</div>
        )}

        {repoError && (
          <div className="text-red-400 py-8 text-center">Failed to load repositories</div>
        )}

        {repoDeleteError && (
          <div className="text-red-400 text-sm">{repoDeleteError}</div>
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
                          disabled={repoDeletingId === repo.id}
                          onClick={() => handleRepoDelete(repo)}
                          className="text-red-400 hover:text-red-300 hover:bg-red-950"
                        >
                          {repoDeletingId === repo.id ? "..." : "Delete"}
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
              <Label htmlFor="pkg-priority">Priority</Label>
              <Input
                id="pkg-priority"
                type="number"
                value={pkgPriority}
                onChange={(e) => setPkgPriority(Number(e.target.value))}
                required
                min={0}
              />
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

            {pkgFormError && (
              <p className="text-sm text-red-400">{pkgFormError}</p>
            )}

            <div className="flex gap-3 pt-2">
              <Button type="submit" disabled={pkgFormLoading}>
                {pkgFormLoading ? "Saving..." : pkgEditing ? "Save Changes" : "Create"}
              </Button>
              <Button
                type="button"
                variant="outline"
                onClick={() => setPkgDialogOpen(false)}
              >
                Cancel
              </Button>
            </div>
          </form>
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

            {repoFormError && (
              <p className="text-sm text-red-400">{repoFormError}</p>
            )}

            <div className="flex gap-3 pt-2">
              <Button type="submit" disabled={repoFormLoading}>
                {repoFormLoading ? "Saving..." : repoEditing ? "Save Changes" : "Create"}
              </Button>
              <Button
                type="button"
                variant="outline"
                onClick={() => setRepoDialogOpen(false)}
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
