"use client"

import { useEffect, useRef, useState } from "react"
import Link from "next/link"
import { useParams } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import {
  AlertCircleIcon,
  GitBranchIcon,
  Loader2Icon,
  RefreshCwIcon,
} from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { CardSkeleton } from "@/components/ui/skeleton"
import { ReviewStep } from "@/components/git-repos/review-step"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { formatRelativeTime } from "@/lib/utils"
import type {
  ActionPack,
  GitRepository,
  HostGroup,
  RepoScanResponse,
} from "@/lib/types"

function authBadge(repo: GitRepository) {
  if (repo.auth_type === "ssh_key") return <Badge className="bg-blue-600 text-white">SSH</Badge>
  if (repo.auth_type === "https_token")
    return <Badge className="bg-amber-600 text-white">HTTPS</Badge>
  return <Badge className="bg-slate-600 text-white">Public</Badge>
}

function InfoRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[140px_1fr] gap-3 py-1 text-sm">
      <span className="text-slate-500">{label}</span>
      <span className="text-slate-200">{children}</span>
    </div>
  )
}

function RescanModal({
  repoId,
  open,
  onOpenChange,
}: {
  repoId: number
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const scanMutation = useApiMutation<RepoScanResponse, void>({
    mutationFn: () =>
      apiFetch<RepoScanResponse>(`/api/git-repos/${repoId}/scan`, { method: "POST" }),
  })

  const startedRef = useRef(false)
  useEffect(() => {
    if (!open) {
      startedRef.current = false
      scanMutation.reset()
      return
    }
    if (startedRef.current) return
    startedRef.current = true
    scanMutation.mutate()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>Re-scan repository</DialogTitle>
        </DialogHeader>
        {scanMutation.isPending || (!scanMutation.data && !scanMutation.error) ? (
          <div className="flex flex-col items-center gap-3 py-10">
            <Loader2Icon className="h-7 w-7 animate-spin text-blue-500" />
            <p className="text-sm text-slate-300">Cloning and scanning the repository…</p>
          </div>
        ) : scanMutation.error ? (
          <div className="flex flex-col items-start gap-4 py-4">
            <div className="flex items-start gap-2 text-red-400">
              <AlertCircleIcon className="h-5 w-5 shrink-0 mt-0.5" />
              <div>
                <p className="text-sm font-medium">Scan failed</p>
                <p className="text-sm text-slate-400 mt-1 break-words">
                  {scanMutation.error.message}
                </p>
              </div>
            </div>
            <div className="flex gap-3">
              <Button
                type="button"
                onClick={() => {
                  scanMutation.reset()
                  scanMutation.mutate()
                }}
                disabled={scanMutation.isPending}
              >
                Retry
              </Button>
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                Close
              </Button>
            </div>
          </div>
        ) : scanMutation.data ? (
          <ReviewStep
            repoId={repoId}
            scanResult={scanMutation.data}
            onActivated={() => onOpenChange(false)}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  )
}

export default function GitRepoDetailPage() {
  const params = useParams()
  const id = Number(params.id)
  const [rescanOpen, setRescanOpen] = useState(false)

  const repoQuery = useQuery<GitRepository>({
    queryKey: ["git-repo", id],
    queryFn: () => apiFetch<GitRepository>(`/api/git-repos/${id}`),
    enabled: Number.isFinite(id),
  })

  const packsQuery = useQuery<ActionPack[]>({
    queryKey: ["action-packs"],
    queryFn: () => apiFetch<ActionPack[]>("/api/action-packs"),
  })

  const groupsQuery = useQuery<HostGroup[]>({
    queryKey: ["groups"],
    queryFn: () => apiFetch<HostGroup[]>("/api/groups"),
  })

  if (repoQuery.isLoading) {
    return (
      <div className="space-y-6">
        <Breadcrumb items={[{ label: "Git Repos", href: "/git-repos" }, { label: "…" }]} />
        <CardSkeleton />
      </div>
    )
  }
  if (repoQuery.error || !repoQuery.data) {
    return (
      <div className="space-y-6">
        <Breadcrumb items={[{ label: "Git Repos", href: "/git-repos" }, { label: "Not found" }]} />
        <p className="text-red-400">
          Failed to load repository: {repoQuery.error?.message ?? "Not found"}
        </p>
      </div>
    )
  }

  const repo = repoQuery.data
  const linkedPacks = (packsQuery.data ?? []).filter((p) => p.git_repository_id === repo.id)
  const linkedGroups = (groupsQuery.data ?? []).filter((g) => g.git_repository_id === repo.id)

  return (
    <div className="max-w-4xl space-y-6">
      <Breadcrumb items={[{ label: "Git Repos", href: "/git-repos" }, { label: repo.name }]} />

      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="flex items-center gap-2 text-2xl font-bold text-white">
            <GitBranchIcon className="h-5 w-5 text-slate-500" />
            <span className="truncate">{repo.name}</span>
          </h1>
          <p className="mt-1 font-mono text-sm text-slate-400 truncate">{repo.url}</p>
        </div>
        <Button type="button" onClick={() => setRescanOpen(true)} data-testid="rescan-button">
          <RefreshCwIcon className="mr-2 h-4 w-4" />
          Re-scan
        </Button>
      </div>

      <section className="rounded-lg border border-slate-700 bg-slate-900 p-5">
        <h2 className="mb-3 text-sm font-medium text-slate-200">Connection</h2>
        <InfoRow label="Branch">
          <Badge variant="outline" className="border-slate-600 text-slate-300">
            {repo.branch}
          </Badge>
        </InfoRow>
        <InfoRow label="Auth">{authBadge(repo)}</InfoRow>
        <InfoRow label="Last commit">
          {repo.last_commit_sha ? (
            <span className="font-mono text-xs text-slate-300">
              {repo.last_commit_sha.slice(0, 12)}
            </span>
          ) : (
            <span className="text-slate-600">never synced</span>
          )}
        </InfoRow>
        <InfoRow label="Last sync">
          {repo.last_sync_at ? (
            <span title={new Date(repo.last_sync_at).toLocaleString()}>
              {formatRelativeTime(repo.last_sync_at)}
            </span>
          ) : (
            <span className="text-slate-600">never</span>
          )}
        </InfoRow>
      </section>

      <section className="rounded-lg border border-slate-700 bg-slate-900 p-5">
        <h2 className="mb-3 text-sm font-medium text-slate-200">
          Action packs
          <span className="ml-2 text-slate-500 tabular-nums">({linkedPacks.length})</span>
        </h2>
        {linkedPacks.length === 0 ? (
          <p className="text-sm text-slate-500">No action packs linked to this repository yet.</p>
        ) : (
          <ul className="space-y-2">
            {linkedPacks.map((p) => (
              <li
                key={p.id}
                className="flex items-center justify-between gap-3 rounded border border-slate-700 bg-slate-800/40 px-3 py-2 text-sm"
              >
                <div className="min-w-0">
                  <span className="font-medium text-slate-100">{p.name}</span>
                  <span className="ml-2 font-mono text-xs text-slate-500">
                    {p.path || "(repo root)"}
                  </span>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Badge variant="outline" className="border-slate-600 text-slate-300">
                    {p.role}
                  </Badge>
                  {!p.enabled && <Badge className="bg-slate-700 text-slate-300">disabled</Badge>}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="rounded-lg border border-slate-700 bg-slate-900 p-5">
        <h2 className="mb-3 text-sm font-medium text-slate-200">
          GitOps-bound host groups
          <span className="ml-2 text-slate-500 tabular-nums">({linkedGroups.length})</span>
        </h2>
        {linkedGroups.length === 0 ? (
          <p className="text-sm text-slate-500">No host groups draw config from this repository.</p>
        ) : (
          <ul className="space-y-2">
            {linkedGroups.map((g) => (
              <li
                key={g.id}
                className="flex items-center justify-between gap-3 rounded border border-slate-700 bg-slate-800/40 px-3 py-2 text-sm"
              >
                <div className="min-w-0">
                  <Link
                    href={`/groups/${g.id}`}
                    className="font-medium text-slate-100 hover:text-white"
                  >
                    {g.name}
                  </Link>
                  {g.gitops_file_path && (
                    <span className="ml-2 font-mono text-xs text-slate-500">
                      {g.gitops_file_path}
                    </span>
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  {g.gitops_status && (
                    <Badge variant="outline" className="border-slate-600 text-slate-300">
                      {g.gitops_status}
                    </Badge>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <RescanModal repoId={repo.id} open={rescanOpen} onOpenChange={setRescanOpen} />
    </div>
  )
}
