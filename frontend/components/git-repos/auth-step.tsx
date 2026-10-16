"use client"

import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { useQuery } from "@tanstack/react-query"
import { Banner, Field, Panel } from "@/components/ld"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { gitRepoSchema, type GitRepoInput } from "@/lib/schemas"
import { detectAuthFromUrl } from "@/lib/git-repos"
import type { GitRepoCreate, GitRepository, SSHKey } from "@/lib/types"

const defaultFormValues: GitRepoInput = {
  name: "",
  url: "",
  branch: "main",
  ssh_key_id: "",
  https_token: "",
  webhook_secret: "",
}

/** Step 1 of connecting a repository: where it is and how LabDog gets in. */
export function AuthStep({ onCreated }: { onCreated: (repo: { id: number; name: string }) => void }) {
  const form = useForm<GitRepoInput>({
    resolver: zodResolver(gitRepoSchema),
    defaultValues: defaultFormValues,
    mode: "onSubmit",
  })

  const url = form.watch("url")
  const detectedAuth = detectAuthFromUrl(url)

  const { data: sshKeys } = useQuery<SSHKey[]>({
    queryKey: ["ssh-keys"],
    queryFn: () => apiFetch<SSHKey[]>("/api/ssh-keys"),
  })

  const createMutation = useApiMutation<GitRepository, GitRepoInput>({
    mutationFn: (data) => {
      const auth = detectAuthFromUrl(data.url)
      const sshKeyId = auth === "ssh_key" && data.ssh_key_id ? Number(data.ssh_key_id) : null
      const token = auth === "https" && data.https_token ? data.https_token : undefined
      const body: GitRepoCreate = {
        name: data.name,
        url: data.url,
        branch: data.branch,
        ssh_key_id: sshKeyId,
        webhook_secret: data.webhook_secret || null,
      }
      if (token) body.https_token = token
      return apiFetch<GitRepository>("/api/git-repos", { method: "POST", body: JSON.stringify(body) })
    },
    invalidateKeys: [["git-repos"]],
    onSuccess: (data) => onCreated({ id: data.id, name: data.name }),
  })

  const onSubmit = form.handleSubmit((data) => createMutation.mutate(data))

  return (
    <Panel title="connect" meta="step 1 · nothing is imported yet">
      <form onSubmit={onSubmit} noValidate className="flex flex-col gap-[11px] p-[13px]">
        <div className="grid gap-[11px]" style={{ gridTemplateColumns: "1fr 120px" }}>
          <Field label="name" htmlFor="repo-name" error={form.formState.errors.name?.message}>
            <input id="repo-name" className="inp mono" placeholder="e.g. infra-config" {...form.register("name")} />
          </Field>
          <Field label="branch" htmlFor="repo-branch">
            <input id="repo-branch" className="inp mono" placeholder="main" {...form.register("branch")} />
          </Field>
        </div>
        <Field label="url" htmlFor="repo-url" hint="ssh or https — the auth fields follow the scheme" error={form.formState.errors.url?.message}>
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
          <Field label="personal access token" htmlFor="https-token" hint="HTTPS URL — blank for public repos">
            <input id="https-token" type="password" className="inp mono" autoComplete="off" {...form.register("https_token")} />
          </Field>
        )}

        <Field label="webhook secret" htmlFor="webhook-secret" hint="optional — lets a push trigger a sync">
          <input id="webhook-secret" type="text" className="inp mono" autoComplete="off" {...form.register("webhook_secret")} />
        </Field>

        {createMutation.error && <Banner tone="danger">{createMutation.error.message}</Banner>}

        <div className="flex items-center gap-2">
          <span className="tt mr-auto">connecting clones the repository and scans it</span>
          <button type="submit" className="btn btn-primary" disabled={createMutation.isPending}>
            {createMutation.isPending ? "Connecting…" : "Connect & scan"}
          </button>
        </div>
      </form>
    </Panel>
  )
}
