"use client"

import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { useQuery } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
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

export function AuthStep({
  onCreated,
}: {
  onCreated: (repo: { id: number; name: string }) => void
}) {
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
      return apiFetch<GitRepository>("/api/git-repos", {
        method: "POST",
        body: JSON.stringify(body),
      })
    },
    invalidateKeys: [["git-repos"]],
    onSuccess: (data) => onCreated({ id: data.id, name: data.name }),
  })

  const onSubmit = form.handleSubmit((data) => createMutation.mutate(data))

  return (
    <form
      onSubmit={onSubmit}
      noValidate
      className="space-y-4 rounded-lg border border-slate-700 bg-slate-900 p-6"
    >
      <div className="space-y-2">
        <Label htmlFor="repo-name">Name</Label>
        <Input id="repo-name" type="text" placeholder="e.g. infra-config" {...form.register("name")} />
        {form.formState.errors.name && (
          <p className="text-sm text-red-400">{form.formState.errors.name.message}</p>
        )}
      </div>

      <div className="space-y-2">
        <Label htmlFor="repo-url">URL</Label>
        <Input
          id="repo-url"
          type="text"
          placeholder="git@github.com:org/repo.git"
          {...form.register("url")}
        />
        {form.formState.errors.url && (
          <p className="text-sm text-red-400">{form.formState.errors.url.message}</p>
        )}
      </div>

      <div className="space-y-2">
        <Label htmlFor="repo-branch">Branch</Label>
        <Input id="repo-branch" type="text" placeholder="main" {...form.register("branch")} />
      </div>

      {detectedAuth === "ssh_key" && (
        <div className="space-y-2">
          <Label htmlFor="ssh-key-select">SSH Key</Label>
          <select
            id="ssh-key-select"
            {...form.register("ssh_key_id")}
            className="w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:border-ring dark:bg-input/30"
          >
            <option value="">Select an SSH key...</option>
            {sshKeys?.map((key) => (
              <option key={key.id} value={key.id}>
                {key.name}
                {key.is_default ? " (default)" : ""}
              </option>
            ))}
          </select>
          {form.formState.errors.ssh_key_id && (
            <p className="text-sm text-red-400">{form.formState.errors.ssh_key_id.message}</p>
          )}
          <p className="text-xs text-slate-500">
            SSH URL detected — pick the deploy key LabDog should use.
          </p>
        </div>
      )}

      {detectedAuth === "https" && (
        <div className="space-y-2">
          <Label htmlFor="https-token">Personal Access Token (optional)</Label>
          <Input
            id="https-token"
            type="password"
            placeholder="Leave blank for public repos"
            {...form.register("https_token")}
          />
          <p className="text-xs text-slate-500">
            HTTPS URL detected — leave the token blank for public repos.
          </p>
        </div>
      )}

      <div className="space-y-2">
        <Label htmlFor="webhook-secret">Webhook Secret (optional)</Label>
        <Input
          id="webhook-secret"
          type="text"
          placeholder="Optional webhook secret"
          {...form.register("webhook_secret")}
        />
      </div>

      {createMutation.error && (
        <p className="text-sm text-red-400">{createMutation.error.message}</p>
      )}

      <div className="flex justify-end pt-2">
        <Button type="submit" disabled={createMutation.isPending}>
          {createMutation.isPending ? "Connecting..." : "Connect & scan"}
        </Button>
      </div>
    </form>
  )
}
