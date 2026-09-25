"use client"

import { useEffect, useRef, useState } from "react"
import Link from "next/link"
import { useParams, useRouter } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { Banner, Dot, Empty, Facts, Modal, PageHead, Panel, Table, Tag, type Tone } from "@/components/ld"
import { ReviewStep } from "@/components/git-repos/review-step"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { shortAgo } from "@/lib/fleet"
import { GITOPS_STATUS, def } from "@/lib/status"
import type { ActionPack, GitRepository, HostGroup, RepoScanResponse } from "@/lib/types"

const CRUMBS = [
  { label: "settings", href: "/settings" },
  { label: "integrations", href: "/settings" },
  { label: "git repositories", href: "/git-repos" },
]

const AUTH: Record<string, { label: string; tone?: Tone }> = {
  ssh_key: { label: "ssh", tone: "accent" },
  https_token: { label: "https", tone: "warn" },
  none: { label: "public" },
}

function RescanModal({ repoId, repoName, onClose }: { repoId: number; repoName: string; onClose: () => void }) {
  const scanMutation = useApiMutation<RepoScanResponse, void>({
    mutationFn: () => apiFetch<RepoScanResponse>(`/api/git-repos/${repoId}/scan`, { method: "POST" }),
  })

  const startedRef = useRef(false)
  useEffect(() => {
    if (startedRef.current) return
    startedRef.current = true
    scanMutation.mutate()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <Modal title="Re-scan repository" meta={repoName} w={860} onClose={onClose}>
      {scanMutation.isPending || (!scanMutation.data && !scanMutation.error) ? (
        <div className="flex flex-col items-center gap-2 py-8 text-center">
          <span className="inline-flex items-center gap-2 text-xs" style={{ color: "var(--sync)" }}>
            <Dot tone="sync" pulse />
            cloning and scanning the repository…
          </span>
        </div>
      ) : scanMutation.error ? (
        <>
          <Banner tone="danger">
            <span className="font-medium">Scan failed.</span> {scanMutation.error.message}
          </Banner>
          <div className="flex gap-2">
            <button
              type="button"
              className="btn btn-sm btn-primary"
              onClick={() => {
                scanMutation.reset()
                scanMutation.mutate()
              }}
              disabled={scanMutation.isPending}
            >
              Retry
            </button>
            <button type="button" className="btn btn-sm" onClick={onClose}>
              Close
            </button>
          </div>
        </>
      ) : scanMutation.data ? (
        <ReviewStep repoId={repoId} scanResult={scanMutation.data} onActivated={onClose} />
      ) : null}
    </Modal>
  )
}

/**
 * One repository: how it is connected, what it feeds — action packs and
 * GitOps-bound groups — and a re-scan that walks the same review as the
 * wizard did.
 */
export default function GitRepoDetailPage() {
  const params = useParams()
  const router = useRouter()
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
    return <PageHead crumbs={CRUMBS} title={<span className="text-text-faint">…</span>} />
  }
  if (repoQuery.error || !repoQuery.data) {
    return (
      <>
        <PageHead crumbs={CRUMBS} title="Repository not found" />
        <Empty
          title="No such repository"
          note={repoQuery.error?.message ?? "It may have been deleted."}
          action={
            <Link href="/git-repos" className="btn btn-sm hover:no-underline">
              Back to repositories
            </Link>
          }
        />
      </>
    )
  }

  const repo = repoQuery.data
  const linkedPacks = (packsQuery.data ?? []).filter((p) => p.git_repository_id === repo.id)
  const linkedGroups = (groupsQuery.data ?? []).filter((g) => g.git_repository_id === repo.id)
  const auth = AUTH[repo.auth_type] ?? { label: repo.auth_type }

  return (
    <>
      <PageHead
        crumbs={CRUMBS}
        title={
          <>
            <span className="mono">{repo.name}</span>
            <Tag>{repo.branch}</Tag>
            <Tag tone={auth.tone}>{auth.label}</Tag>
          </>
        }
        sub={<span className="mono">{repo.url}</span>}
        actions={
          <button type="button" className="btn btn-sm btn-primary" onClick={() => setRescanOpen(true)} data-testid="rescan-button">
            Re-scan
          </button>
        }
      />

      <div className="scroll flex flex-1 flex-col gap-3 p-3.5" style={{ maxWidth: 960 }}>
        <Panel title="connection" meta={repo.last_sync_at ? `synced ${shortAgo(repo.last_sync_at)} ago` : "never synced"}>
          <div className="p-[11px]">
            <Facts
              min={150}
              items={[
                { k: "branch", v: repo.branch, mono: true },
                { k: "auth", v: auth.label },
                { k: "last commit", v: repo.last_commit_sha ? repo.last_commit_sha.slice(0, 12) : <span className="text-text-faint">never synced</span>, mono: true },
                { k: "last sync", v: repo.last_sync_at ? `${shortAgo(repo.last_sync_at)} ago` : <span className="text-text-faint">never</span>, mono: true, title: repo.last_sync_at ? new Date(repo.last_sync_at).toLocaleString() : undefined },
                { k: "webhook secret", v: repo.has_webhook_secret ? "set" : <span className="text-text-faint">none</span> },
              ]}
            />
          </div>
        </Panel>

        <Panel title="action packs" meta={`${linkedPacks.length} from this repository`}>
          <Table<ActionPack>
            cols={[
              { k: "name", label: "pack", w: "minmax(160px,1fr)", sortable: false, cell: (p) => <span className="font-medium text-text">{p.name}</span> },
              { k: "path", label: "path", w: "minmax(160px,1.4fr)", sortable: false, cell: (p) => <span className="mono text-[11px]">{p.path || "(repo root)"}</span> },
              { k: "state", label: "", w: "90px", right: true, sortable: false, cell: (p) => (p.enabled ? <Tag tone="ok">enabled</Tag> : <Tag>disabled</Tag>) },
            ]}
            rows={linkedPacks}
            keyOf={(p) => p.id}
            loading={packsQuery.isLoading}
            empty="No action packs linked to this repository yet — re-scan to pick some up."
          />
        </Panel>

        <Panel title="gitops-bound groups" meta={`${linkedGroups.length} import from this repository`}>
          <Table<HostGroup>
            cols={[
              { k: "name", label: "group", w: "minmax(140px,1fr)", sortable: false, cell: (g) => <span className="mono font-medium text-text">{g.name}</span> },
              { k: "file", label: "file", w: "minmax(160px,1.4fr)", sortable: false, cell: (g) => <span className="mono text-[11px]">{g.gitops_file_path ?? "—"}</span> },
              { k: "status", label: "status", w: "110px", sortable: false, cell: (g) => <Tag tone={def(GITOPS_STATUS, g.gitops_status ?? "disconnected").tone}>{g.gitops_status ?? "disconnected"}</Tag> },
              { k: "go", label: "", w: "78px", right: true, sortable: false, cell: () => <span className="tt text-ld-accent">open →</span> },
            ]}
            rows={linkedGroups}
            keyOf={(g) => g.id}
            onRowClick={(g) => router.push(`/groups/${g.id}`)}
            loading={groupsQuery.isLoading}
            empty="No host group draws its configuration from this repository."
          />
        </Panel>
      </div>

      {rescanOpen && <RescanModal repoId={repo.id} repoName={repo.name} onClose={() => setRescanOpen(false)} />}
    </>
  )
}
