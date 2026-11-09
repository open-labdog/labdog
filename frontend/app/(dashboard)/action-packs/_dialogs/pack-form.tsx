"use client"

import Link from "next/link"
import { Banner, Field, Modal } from "@/components/ld"
import type { ActionPack, GitRepository, PackSourceType } from "@/lib/types"
import type { PackFormState } from "../_hooks/use-pack-form"

/** Add or edit one pack source. The state behind it is `usePackForm`'s. */
export function PackFormDialog({
  editing,
  form,
  formError,
  formSaving,
  gitRepos,
  setF,
  onClose,
  onSave,
}: {
  editing: ActionPack | null
  form: PackFormState
  formError: string | null
  formSaving: boolean
  gitRepos: GitRepository[] | undefined
  setF: <K extends keyof PackFormState>(k: K, v: PackFormState[K]) => void
  onClose: () => void
  onSave: () => void
}) {
  const hasGitRepos = (gitRepos?.length ?? 0) > 0
  return (
    <Modal
      title={editing ? "Edit action pack" : "Add action pack"}
      meta={editing?.name}
      w={560}
      onClose={onClose}
      onSubmit={(e) => {
        e.preventDefault()
        onSave()
      }}
      footer={
        <>
          <span className="tt mr-auto">LabDog looks for actions/*.manifest.yml</span>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={formSaving}>
            {formSaving ? "Saving…" : editing ? "Save changes" : "Add pack"}
          </button>
        </>
      }
    >
      <div className="grid gap-[11px]" style={{ gridTemplateColumns: "1fr 160px" }}>
        <Field label="name" htmlFor="pack-name">
          <input id="pack-name" className="inp mono" placeholder="labdog-default" value={form.name} onChange={(e) => setF("name", e.target.value)} />
        </Field>
        <Field label="source" htmlFor="pack-source">
          <select id="pack-source" className="inp" value={form.source_type} onChange={(e) => setF("source_type", e.target.value as PackSourceType)}>
            <option value="git">git repository</option>
            <option value="local">local directory</option>
          </select>
        </Field>
      </div>

      {form.source_type === "git" && (
        <>
          <Field label="git repository" htmlFor="pack-repo">
            {!hasGitRepos ? (
              <Banner tone="warn">
                No git repositories yet — add one under{" "}
                <Link href="/git-repos" className="underline">
                  Settings › Git repositories
                </Link>{" "}
                first.
              </Banner>
            ) : (
              <select id="pack-repo" className="inp mono" value={form.git_repository_id ?? ""} onChange={(e) => setF("git_repository_id", e.target.value ? Number(e.target.value) : null)}>
                <option value="">— pick a repository —</option>
                {gitRepos!.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.name} ({r.url} @ {r.branch})
                  </option>
                ))}
              </select>
            )}
          </Field>
          <Field label="path inside the repo" htmlFor="pack-path" hint="blank when the pack is at the repository root">
            <input id="pack-path" className="inp mono" placeholder="packs/ops" value={form.path} onChange={(e) => setF("path", e.target.value)} />
          </Field>
        </>
      )}

      {form.source_type === "local" && (
        <Field label="filesystem path" htmlFor="pack-local-path" hint="absolute, on the LabDog host — read in place, nothing is cloned">
          <input id="pack-local-path" className="inp mono" placeholder="/var/lib/labdog/my-pack" value={form.local_path} onChange={(e) => setF("local_path", e.target.value)} />
        </Field>
      )}

      <label className="flex items-center gap-2 text-xs text-text">
        <input id="pack-enabled" type="checkbox" checked={form.enabled} onChange={(e) => setF("enabled", e.target.checked)} />
        enabled
      </label>
      <label className="flex items-start gap-2 text-xs text-text">
        <input id="pack-trusted" type="checkbox" className="mt-0.5" checked={form.trusted} onChange={(e) => setF("trusted", e.target.checked)} />
        <span>
          allow content that runs on the LabDog host
          <span className="mt-0.5 block text-[11px] text-text-3">
            Ansible plugin directories, and plays targeting <span className="mono">localhost</span>, execute on the LabDog server itself rather than on a managed host. A pack containing either is refused unless this
            is set. Leave it off unless you have read the pack and intend it.
          </span>
        </span>
      </label>

      {formError && <Banner tone="danger">{formError}</Banner>}
    </Modal>
  )
}
