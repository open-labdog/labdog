"use client"

import { useMemo, useState } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { shortAgo } from "@/lib/fleet"
import { gitRepoSchema, type GitRepoInput } from "@/lib/schemas"
import { detectAuthFromUrl } from "@/lib/git-repos"
import type { GitRepository, GitRepoUpdate, SSHKey, HostGroup } from "@/lib/types"
import { Banner, CodeBlock, Confirm, Copy, Field, Filter, Modal, PageHead, Table, Tag, type Sort, type Tone } from "@/components/ld"

const CRUMBS = [
  { label: "settings", href: "/settings" },
  { label: "integrations", href: "/settings" },
]

type SyncHealth = "healthy" | "stale" | "never"

function syncHealth(repo: GitRepository): SyncHealth {
  if (!repo.last_sync_at) return "never"
  const age = Date.now() - new Date(repo.last_sync_at).getTime()
  return age > 24 * 60 * 60 * 1000 ? "stale" : "healthy"
}

const HEALTH: Record<SyncHealth, { label: string; tone: Tone }> = {
  healthy: { label: "synced", tone: "ok" },
  stale: { label: "stale", tone: "warn" },
  never: { label: "never synced", tone: "idle" },
}

const AUTH: Record<string, { label: string; tone?: Tone }> = {
  ssh_key: { label: "ssh", tone: "accent" },
  https_token: { label: "https", tone: "warn" },
  none: { label: "public" },
}

const defaultFormValues: GitRepoInput = {
  name: "",
  url: "",
  branch: "main",
  ssh_key_id: "",
  https_token: "",
  webhook_secret: "",
}

/**
 * Git repositories — where GitOps-bound groups and action packs are
 * imported from. Connecting one is a wizard (`/git-repos/new`); a row
 * opens the repository's page; editing here is the connection itself.
 */
export default function GitReposPage() {
  const router = useRouter()
  const [q, setQ] = useState("")
  const [auth, setAuth] = useState("all")
  const [sort, setSort] = useState<Sort>({ k: "name", dir: 1 })
  const [editingRepo, setEditingRepo] = useState<GitRepository | null>(null)
  const [deleteRepo, setDeleteRepo] = useState<GitRepository | null>(null)
  const [webhookRepo, setWebhookRepo] = useState<GitRepository | null>(null)

  const form = useForm<GitRepoInput>({
    resolver: zodResolver(gitRepoSchema),
    defaultValues: defaultFormValues,
    mode: "onSubmit",
  })

  const url = form.watch("url")
  const detectedAuth = detectAuthFromUrl(url)

  const { data: repos, isLoading, error } = useQuery<GitRepository[]>({
    queryKey: ["git-repos"],
    queryFn: () => apiFetch<GitRepository[]>("/api/git-repos"),
  })
  const { data: sshKeys } = useQuery<SSHKey[]>({
    queryKey: ["ssh-keys"],
    queryFn: () => apiFetch<SSHKey[]>("/api/ssh-keys"),
  })
  const { data: groups } = useQuery<HostGroup[]>({
    queryKey: ["groups"],
    queryFn: () => apiFetch<HostGroup[]>("/api/groups"),
  })
  const groupCountByRepo = useMemo(() => {
    const map = new Map<number, number>()
    groups?.forEach((g) => {
      if (g.git_repository_id != null) map.set(g.git_repository_id, (map.get(g.git_repository_id) ?? 0) + 1)
    })
    return map
  }, [groups])

  const all = useMemo(() => repos ?? [], [repos])
  const rows = useMemo(() => {
    const ql = q.trim().toLowerCase()
    const r = all.filter((x) => (auth === "all" || x.auth_type === auth) && (!ql || x.name.toLowerCase().includes(ql) || x.url.toLowerCase().includes(ql)))
    const key: Record<string, (x: GitRepository) => string | number> = {
      name: (x) => x.name.toLowerCase(),
      url: (x) => x.url,
      branch: (x) => x.branch,
      auth: (x) => x.auth_type,
      groups: (x) => groupCountByRepo.get(x.id) ?? 0,
      sync: (x) => x.last_sync_at ?? "",
    }
    const f = key[sort.k] ?? key.name
    return r.sort((a, b) => {
      const x = f(a)
      const y = f(b)
      return (x > y ? 1 : x < y ? -1 : 0) * sort.dir
    })
  }, [all, q, auth, sort, groupCountByRepo])

  const saveMutation = useApiMutation({
    mutationFn: ({ editId, data }: { editId: number; data: GitRepoInput }) => {
      const a = detectAuthFromUrl(data.url)
      const sshKeyId = a === "ssh_key" && data.ssh_key_id ? Number(data.ssh_key_id) : null
      const token = a === "https" && data.https_token ? data.https_token : undefined
      const body: GitRepoUpdate = {
        name: data.name,
        url: data.url,
        branch: data.branch,
        ssh_key_id: sshKeyId,
        webhook_secret: data.webhook_secret || null,
      }
      if (token) body.https_token = token
      return apiFetch(`/api/git-repos/${editId}`, { method: "PUT", body: JSON.stringify(body) })
    },
    invalidateKeys: [["git-repos"]],
    onSuccess: () => closeEdit(),
  })

  const deleteMutation = useApiMutation({
    mutationFn: (id: number) => apiFetch(`/api/git-repos/${id}`, { method: "DELETE" }),
    invalidateKeys: [["git-repos"]],
    onSuccess: () => setDeleteRepo(null),
  })

  function openEdit(repo: GitRepository) {
    setEditingRepo(repo)
    form.reset({
      name: repo.name,
      url: repo.url,
      branch: repo.branch,
      ssh_key_id: repo.ssh_key_id ? String(repo.ssh_key_id) : "",
      https_token: "",
      // SEC-28: the stored secret is write-only. Blank means "keep it".
      webhook_secret: "",
    })
    saveMutation.reset()
  }

  function closeEdit() {
    setEditingRepo(null)
    form.reset(defaultFormValues)
    saveMutation.reset()
  }

  const onSubmit = form.handleSubmit((data) => {
    if (!editingRepo) return
    saveMutation.mutate({ editId: editingRepo.id, data })
  })

  const webhookUrls = useMemo(() => {
    const origin = typeof window !== "undefined" ? window.location.origin : ""
    return [
      { label: "GitHub", url: `${origin}/api/webhooks/github` },
      { label: "GitLab", url: `${origin}/api/webhooks/gitlab` },
      { label: "Gitea", url: `${origin}/api/webhooks/gitea` },
    ]
  }, [])

  const stop = (e: React.MouseEvent) => e.stopPropagation()

  return (
    <>
      <PageHead
        crumbs={CRUMBS}
        title={
          <>
            Git repositories <span className="mono num text-[12.5px] font-normal text-text-faint">{all.length}</span>
          </>
        }
        sub="Where GitOps-bound groups and action packs are imported from. A push to a connected repository — or a manual sync — imports what changed."
        actions={
          <Link href="/git-repos/new" className="btn btn-sm btn-primary hover:no-underline">
            Add Repository
          </Link>
        }
      >
        <div className="flex flex-wrap items-center gap-[7px]">
          <input className="inp mono" style={{ width: 220, fontSize: 11.5, padding: "4px 8px" }} placeholder="Search name or url…" aria-label="search repositories" value={q} onChange={(e) => setQ(e.target.value)} />
          <Filter label="auth" value={auth} onChange={setAuth} options={Object.entries(AUTH).map(([k, v]) => ({ k, label: v.label, n: all.filter((x) => x.auth_type === k).length }))} />
          <span className="tt ml-auto hidden sm:inline">a row opens the repository</span>
        </div>
      </PageHead>

      {error && (
        <Banner tone="danger" flush>
          Could not load repositories: {error.message}
        </Banner>
      )}

      <Table<GitRepository>
        cols={[
          {
            k: "name",
            label: "name",
            w: "minmax(160px,1fr)",
            cell: (r) => (
              <span className="flex min-w-0 flex-col">
                <span className="mono trunc font-medium text-text">{r.name}</span>
                {r.last_commit_sha && (
                  <span className="mono text-[10.5px] text-text-faint" title={r.last_commit_sha}>
                    {r.last_commit_sha.slice(0, 7)}
                  </span>
                )}
              </span>
            ),
          },
          { k: "url", label: "url", w: "minmax(200px,1.6fr)", cell: (r) => <span className="mono text-[11px]" title={r.url}>{r.url}</span> },
          { k: "branch", label: "branch", w: "96px", cell: (r) => <Tag>{r.branch}</Tag> },
          {
            k: "auth",
            label: "auth",
            w: "84px",
            cell: (r) => {
              const a = AUTH[r.auth_type] ?? { label: r.auth_type }
              return <Tag tone={a.tone}>{a.label}</Tag>
            },
          },
          {
            k: "groups",
            label: "groups",
            w: "70px",
            right: true,
            cell: (r) => {
              const n = groupCountByRepo.get(r.id) ?? 0
              return <span className={`mono num ${n ? "text-text" : "text-text-faint"}`}>{n}</span>
            },
          },
          {
            k: "sync",
            label: "last sync",
            w: "130px",
            cell: (r) => {
              const h = HEALTH[syncHealth(r)]
              return (
                <span className="flex items-center gap-1.5" title={r.last_sync_at ? new Date(r.last_sync_at).toLocaleString() : undefined}>
                  <Tag tone={h.tone}>{h.label}</Tag>
                  {r.last_sync_at && <span className="mono num text-[11px]">{shortAgo(r.last_sync_at)} ago</span>}
                </span>
              )
            },
          },
          {
            k: "actions",
            label: "",
            w: "190px",
            right: true,
            sortable: false,
            cell: (r) => (
              <span className="flex gap-0.5" onClick={stop}>
                <button type="button" className="btn btn-sm btn-ghost" onClick={() => setWebhookRepo(r)}>
                  webhooks
                </button>
                <button type="button" className="btn btn-sm btn-ghost" onClick={() => openEdit(r)}>
                  edit
                </button>
                <button type="button" className="btn btn-sm btn-ghost text-danger" disabled={deleteMutation.isPending} onClick={() => setDeleteRepo(r)}>
                  delete
                </button>
              </span>
            ),
          },
        ]}
        rows={rows}
        keyOf={(r) => r.id}
        sort={sort}
        onSort={(k) => setSort((s) => ({ k, dir: s.k === k ? ((-s.dir) as 1 | -1) : 1 }))}
        onRowClick={(r) => router.push(`/git-repos/${r.id}`)}
        rowTone={(r) => (syncHealth(r) === "stale" ? "warn" : undefined)}
        loading={isLoading}
        empty={
          all.length === 0 ? (
            <span>
              No repositories connected. Link one to manage group configuration declaratively via YAML and to import action packs —{" "}
              <Link href="/git-repos/new" className="text-ld-accent">
                Add Repository
              </Link>
              .
            </span>
          ) : (
            "No repository matches."
          )
        }
      />

      {editingRepo && (
        <Modal
          title="Edit repository"
          meta={editingRepo.name}
          w={520}
          onClose={closeEdit}
          onSubmit={onSubmit}
          footer={
            <>
              <span className="tt mr-auto">secrets are write-only — blank keeps them</span>
              <button type="button" className="btn" onClick={closeEdit}>
                Cancel
              </button>
              <button type="submit" className="btn btn-primary" disabled={saveMutation.isPending}>
                {saveMutation.isPending ? "Saving…" : "Update repository"}
              </button>
            </>
          }
        >
          <div className="grid gap-[11px]" style={{ gridTemplateColumns: "1fr 120px" }}>
            <Field label="name" htmlFor="repo-name" error={form.formState.errors.name?.message}>
              <input id="repo-name" className="inp mono" placeholder="e.g. infra-config" {...form.register("name")} />
            </Field>
            <Field label="branch" htmlFor="repo-branch">
              <input id="repo-branch" className="inp mono" placeholder="main" {...form.register("branch")} />
            </Field>
          </div>
          <Field label="url" htmlFor="repo-url" error={form.formState.errors.url?.message}>
            <input id="repo-url" className="inp mono" placeholder="git@github.com:org/repo.git" {...form.register("url")} />
          </Field>
          {detectedAuth === "ssh_key" && (
            <Field label="ssh key" htmlFor="ssh-key-select" hint="SSH URL — pick the deploy key LabDog uses" error={form.formState.errors.ssh_key_id?.message}>
              <select id="ssh-key-select" className="inp mono" {...form.register("ssh_key_id")}>
                <option value="">— pick an SSH key —</option>
                {sshKeys?.map((key) => (
                  <option key={key.id} value={key.id}>
                    {key.name}
                    {key.is_default ? " (default)" : ""}
                  </option>
                ))}
              </select>
            </Field>
          )}
          {detectedAuth === "https" && (
            <Field label="personal access token" htmlFor="https-token" hint="HTTPS URL — blank for public repos, or to keep the current token">
              <input id="https-token" type="password" className="inp mono" placeholder="leave blank to keep the existing token" autoComplete="off" {...form.register("https_token")} />
            </Field>
          )}
          <Field label="webhook secret" htmlFor="webhook-secret" hint={editingRepo.has_webhook_secret ? "set — blank keeps it" : "optional"}>
            <input id="webhook-secret" type="password" className="inp mono" autoComplete="off" {...form.register("webhook_secret")} />
          </Field>
          {saveMutation.error && <Banner tone="danger">{saveMutation.error.message}</Banner>}
        </Modal>
      )}

      {webhookRepo && (
        <Modal
          title="Webhook URLs"
          meta={webhookRepo.name}
          w={520}
          onClose={() => setWebhookRepo(null)}
          footer={
            <button type="button" className="btn btn-primary ml-auto" onClick={() => setWebhookRepo(null)}>
              Done
            </button>
          }
        >
          <div className="text-[11.5px] text-text-2">Configure your git provider to send push events to one of these URLs:</div>
          {webhookUrls.map((wh) => (
            <CodeBlock key={wh.label} title={wh.label} actions={<Copy text={wh.url} />} maxH={60}>
              {wh.url}
            </CodeBlock>
          ))}
          {/* SEC-28: the secret is stored encrypted and never returned,
              so there is nothing to copy here — only whether one is set. */}
          {webhookRepo.has_webhook_secret ? (
            <Banner tone="ok">A webhook secret is set. It is stored encrypted and cannot be shown again — edit the repository to replace it.</Banner>
          ) : (
            <Banner tone="warn">No webhook secret — anyone who learns the URL can trigger a sync. Set one when you edit the repository.</Banner>
          )}
        </Modal>
      )}

      {deleteRepo && (
        <Confirm
          open
          onOpenChange={(open) => !open && setDeleteRepo(null)}
          title="Delete repository"
          description={`${deleteRepo.name} is disconnected; ${groupCountByRepo.get(deleteRepo.id) ?? 0} GitOps-bound group(s) stop importing and keep what they have. This cannot be undone.${deleteMutation.error ? ` ${deleteMutation.error.message}` : ""}`}
          confirmLabel="Delete"
          variant="destructive"
          loading={deleteMutation.isPending}
          onConfirm={() => deleteMutation.mutate(deleteRepo.id)}
        />
      )}
    </>
  )
}
