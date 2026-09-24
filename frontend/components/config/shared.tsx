"use client"

import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { Banner } from "@/components/ld"
import type { HostGroup } from "@/lib/types"

/**
 * The group an editor edits, and whether Git owns its desired state.
 * Every module editor reads this: under GitOps the editor is read-only
 * and says so, because the next import would overwrite any edit.
 */
export function useEditorGroup(groupId: number) {
  const { data: group } = useQuery<HostGroup>({
    queryKey: ["group", groupId],
    queryFn: () => apiFetch<HostGroup>(`/api/groups/${groupId}`),
    enabled: !!groupId,
  })
  return { group, gitops: !!group?.gitops_enabled }
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
