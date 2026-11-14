"use client"

import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { useCan } from "@/lib/auth"
import { Banner } from "@/components/ld"
import type { HostGroup } from "@/lib/types"

/**
 * The group an editor works on, and whether it may be edited here.
 * Under GitOps the editor is read-only and says so, because the next
 * import would overwrite any edit.
 *
 * `managedByGit`: Git owns the module, so edits happen in the repository.
 * `locked`: no edits from this screen, either because of that or because
 * the user's role lacks `config.edit`. Editors disable on `locked` and show
 * the GitOps banner on `managedByGit`.
 */
export function useEditorGroup(groupId: number) {
  const { data: group } = useQuery<HostGroup>({
    queryKey: ["group", groupId],
    queryFn: () => apiFetch<HostGroup>(`/api/groups/${groupId}`),
    enabled: !!groupId,
  })
  const canEdit = useCan("config.edit")
  const managedByGit = !!group?.gitops_enabled
  return { group, managedByGit, locked: managedByGit || !canEdit }
}

/** The strip under the toolbar when Git owns the module. */
export function GitOpsBanner({ group, what }: { group: HostGroup | undefined; what: string }) {
  return (
    <Banner
      tone="sync"
      flush
      action={
        <Link href="/git-repos" className="btn btn-sm hover:no-underline">
          repository →
        </Link>
      }
    >
      Managed by GitOps — {what} imported from <span className="mono">{group?.gitops_file_path ?? "the repository"}</span>; edit them in Git.
    </Banner>
  )
}

/** Query keys every editor mutation invalidates besides its own list:
 *  the group page's module counts come from the summary. */
export const GROUP_KEYS: (string | number)[][] = [["groups-summary"], ["groups"]]

/** Stop a row action's click from also firing the row's own handler. */
export const stop = (e: React.MouseEvent) => e.stopPropagation()
