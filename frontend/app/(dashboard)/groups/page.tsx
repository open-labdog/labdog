"use client"

import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { buttonVariants } from "@/components/ui/button"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import { cn, useDelayedLoading } from "@/lib/utils"
import { TableSkeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { GitOpsStatusBadge } from "@/components/status-badge"
import { apiFetch } from "@/lib/api"
import type { HostGroup } from "@/lib/types"

export default function GroupsPage() {
  const { data: groups, isLoading, error } = useQuery<HostGroup[]>({
    queryKey: ["groups"],
    queryFn: () => apiFetch<HostGroup[]>("/api/groups"),
  })
  const showLoading = useDelayedLoading(isLoading)

  return (
    <div className="space-y-6">
      <Breadcrumb items={[{ label: "Groups" }]} />
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Groups</h1>
          <p className="text-slate-400 text-sm mt-1">Manage host groups for firewall rule organization</p>
        </div>
        <Link href="/groups/new" className={cn(buttonVariants())}>New Group</Link>
      </div>

      {showLoading && <TableSkeleton rows={5} columns={3} />}

      {error && (
        <div className="text-red-400 py-8 text-center">Failed to load groups</div>
      )}

      {!isLoading && !error && groups && groups.length === 0 && (
        <div className="text-slate-400 py-8 text-center">
          No groups yet.{" "}
          <Link href="/groups/new" className="underline hover:text-white">
            Create your first group
          </Link>
        </div>
      )}

      {!isLoading && !error && groups && groups.length > 0 && (
        <div className="rounded-lg border border-slate-700 bg-slate-900">
          <Table>
            <TableHeader>
              <TableRow className="border-slate-700">
                <TableHead>Name</TableHead>
                <TableHead>Priority</TableHead>
                <TableHead>GitOps</TableHead>
                <TableHead>Description</TableHead>
                <TableHead>Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {groups.map((group) => (
                <TableRow key={group.id} className="border-slate-700">
                  <TableCell className="font-medium text-white">{group.name}</TableCell>
                  <TableCell>{group.priority}</TableCell>
                  <TableCell>
                    {group.gitops_enabled && group.gitops_status ? (
                      <GitOpsStatusBadge status={group.gitops_status} />
                    ) : (
                      <span className="text-slate-500">—</span>
                    )}
                  </TableCell>
                  <TableCell className="text-slate-400">{group.description ?? "—"}</TableCell>
                  <TableCell>
                    <Link href={`/groups/${group.id}`} className={cn(buttonVariants({ variant: "ghost", size: "sm" }))}>View</Link>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  )
}
