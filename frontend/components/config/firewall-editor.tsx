"use client"

import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { plural } from "@/lib/fleet"
import { FIREWALL_ACTION, def } from "@/lib/status"
import type { ChainPolicies, FirewallRule, Host } from "@/lib/types"
import { Banner, Confirm, Table, Tag, Toolbar } from "@/components/ld"
import { GROUP_KEYS, GitOpsBanner, stop, useEditorGroup } from "./shared"
import { RuleDialog } from "./rule-dialog"

function formatPorts(rule: FirewallRule): string {
  if (rule.port_start == null) return "any"
  if (rule.port_end != null && rule.port_end !== rule.port_start) return `${rule.port_start}–${rule.port_end}`
  return String(rule.port_start)
}

const move = <T,>(arr: T[], from: number, to: number): T[] => {
  const next = [...arr]
  const [item] = next.splice(from, 1)
  next.splice(to, 0, item)
  return next
}

/**
 * Firewall rules a group declares, in the order they are evaluated —
 * the ▲/▼ buttons reorder, which is what the table's order means — plus
 * the chain's default policies. Embedded in the group page's Config tab.
 */
export function FirewallEditor({ groupId }: { groupId: number }) {
  const { group, gitops } = useEditorGroup(groupId)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingRule, setEditingRule] = useState<FirewallRule | null>(null)
  const [deleting, setDeleting] = useState<FirewallRule | null>(null)

  const { data: rules, isLoading, error } = useQuery<FirewallRule[]>({
    queryKey: ["rules", groupId],
    queryFn: () => apiFetch<FirewallRule[]>(`/api/groups/${groupId}/rules`),
    enabled: !!groupId,
  })
  const { data: hosts = [] } = useQuery<Host[]>({ queryKey: ["hosts"], queryFn: () => apiFetch<Host[]>("/api/hosts") })
  const hostName = (id: number | null) => (id == null ? null : (hosts.find((h) => h.id === id)?.hostname ?? `host ${id}`))
  const allRules = useMemo(() => rules ?? [], [rules])

  useQuery<ChainPolicies>({
    queryKey: ["policies", groupId],
    queryFn: () => apiFetch<ChainPolicies>(`/api/groups/${groupId}/policies`),
    enabled: !!groupId,
  })

  const reorderMutation = useApiMutation({
    mutationFn: (ruleIds: number[]) => apiFetch(`/api/groups/${groupId}/rules/reorder`, { method: "PUT", body: JSON.stringify({ rule_ids: ruleIds }) }),
    invalidateKeys: [["rules", groupId]],
  })
  const deleteMutation = useApiMutation({
    mutationFn: (ruleId: number) => apiFetch(`/api/groups/${groupId}/rules/${ruleId}`, { method: "DELETE" }),
    invalidateKeys: [["rules", groupId], ...GROUP_KEYS],
    onSuccess: () => setDeleting(null),
  })
  const policyMutation = useApiMutation({
    mutationFn: (body: { input_policy: string | null; output_policy: string | null }) => apiFetch(`/api/groups/${groupId}/policies`, { method: "PUT", body: JSON.stringify(body) }),
    invalidateKeys: [["policies", groupId], ["group", groupId]],
  })

  function handlePolicyChange(chain: "input" | "output", value: string) {
    const policyValue = value === "" ? null : value
    policyMutation.mutate({
      input_policy: chain === "input" ? policyValue : (group?.input_policy ?? null),
      output_policy: chain === "output" ? policyValue : (group?.output_policy ?? null),
    })
  }

  const reorder = (from: number, to: number) => {
    if (gitops || to < 0 || to >= allRules.length) return
    reorderMutation.mutate(move(allRules, from, to).map((r) => r.id))
  }

  const openEdit = (rule: FirewallRule) => {
    setEditingRule(rule)
    setDialogOpen(true)
  }

  const side = (cidr: string | null, hostId: number | null) => {
    const h = hostName(hostId)
    if (h) return <Tag tone="accent" title="resolved to the host's address at sync time">{h}</Tag>
    return <span className="mono text-[11px]">{cidr ?? <span className="text-text-faint">any</span>}</span>
  }
  const busy = reorderMutation.isPending || policyMutation.isPending

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Toolbar
        actions={
          !gitops && (
            <button
              type="button"
              className="btn btn-sm btn-primary"
              onClick={() => {
                setEditingRule(null)
                setDialogOpen(true)
              }}
            >
              Add rule
            </button>
          )
        }
      >
        <span className="tt">{plural(allRules.length, "rule")} declared here</span>
        <label className="flex items-center gap-1.5">
          <span className="tt">input</span>
          <select id="input-policy" className="inp" style={{ width: 118, padding: "3px 6px" }} value={group?.input_policy ?? ""} onChange={(e) => handlePolicyChange("input", e.target.value)} disabled={gitops || busy}>
            <option value="">default (drop)</option>
            <option value="drop">drop</option>
            <option value="accept">accept</option>
          </select>
        </label>
        <label className="flex items-center gap-1.5">
          <span className="tt">output</span>
          <select id="output-policy" className="inp" style={{ width: 128, padding: "3px 6px" }} value={group?.output_policy ?? ""} onChange={(e) => handlePolicyChange("output", e.target.value)} disabled={gitops || busy}>
            <option value="">default (accept)</option>
            <option value="accept">accept</option>
            <option value="drop">drop</option>
          </select>
        </label>
      </Toolbar>
      {gitops && <GitOpsBanner group={group} what="rules are" />}
      {group?.input_policy === "accept" && (
        <Banner tone="warn" flush>
          INPUT policy is accept — every inbound connection is allowed unless a rule denies it.
        </Banner>
      )}
      {group?.output_policy === "drop" && (
        <Banner tone="warn" flush>
          OUTPUT policy is drop — every outbound connection is blocked unless a rule allows it.
        </Banner>
      )}
      {policyMutation.error && (
        <Banner tone="danger" flush>
          {policyMutation.error.message}
        </Banner>
      )}
      {error && (
        <Banner tone="danger" flush>
          Could not load rules: {error.message}
        </Banner>
      )}

      <Table<FirewallRule>
        cols={[
          {
            k: "order",
            label: "#",
            w: "78px",
            sortable: false,
            cell: (rule) => {
              const idx = allRules.findIndex((r) => r.id === rule.id)
              return (
                <span className="flex items-center gap-0.5" onClick={stop}>
                  <span className="mono num w-5 text-text-faint">{rule.priority}</span>
                  <button type="button" className="btn btn-sm btn-ghost px-1" disabled={gitops || busy || idx <= 0} onClick={() => reorder(idx, idx - 1)} aria-label="Move up" title="move up">
                    ▲
                  </button>
                  <button type="button" className="btn btn-sm btn-ghost px-1" disabled={gitops || busy || idx >= allRules.length - 1} onClick={() => reorder(idx, idx + 1)} aria-label="Move down" title="move down">
                    ▼
                  </button>
                </span>
              )
            },
          },
          { k: "action", label: "action", w: "80px", sortable: false, cell: (r) => <Tag tone={def(FIREWALL_ACTION, r.action).tone}>{r.action}</Tag> },
          { k: "proto", label: "proto", w: "64px", sortable: false, cell: (r) => <span className="mono text-[11px]">{r.protocol}</span> },
          { k: "dir", label: "dir", w: "64px", sortable: false, cell: (r) => <span className="mono text-[11px]">{r.direction}</span> },
          { k: "src", label: "source", w: "minmax(120px,1fr)", sortable: false, cell: (r) => side(r.source_cidr, r.source_host_id) },
          { k: "dst", label: "destination", w: "minmax(120px,1fr)", sortable: false, cell: (r) => side(r.destination_cidr, r.destination_host_id) },
          {
            k: "port",
            label: "port",
            w: "90px",
            sortable: false,
            cell: (r) => (
              <span className="flex items-center gap-1">
                <span className="mono text-[11px]">{formatPorts(r)}</span>
                {r.port_start === 22 && <Tag tone="hold" title="SSH — the control-plane rule is re-injected on apply">ssh</Tag>}
              </span>
            ),
          },
          { k: "comment", label: "comment", w: "minmax(140px,1.4fr)", sortable: false, cell: (r) => <span className="text-[11.5px] text-text-3">{r.comment ?? ""}</span> },
          {
            k: "actions",
            label: "",
            w: "118px",
            right: true,
            sortable: false,
            cell: (r) => (
              <span className="flex gap-0.5" onClick={stop}>
                <button type="button" className="btn btn-sm btn-ghost" disabled={gitops} onClick={() => openEdit(r)}>
                  edit
                </button>
                <button type="button" className="btn btn-sm btn-ghost text-danger" disabled={gitops || deleteMutation.isPending} onClick={() => setDeleting(r)}>
                  delete
                </button>
              </span>
            ),
          },
        ]}
        rows={allRules}
        keyOf={(r) => r.id}
        onRowClick={gitops ? undefined : openEdit}
        loading={isLoading}
        empty="Nothing declared. Add rule declares this module for the group; LabDog always injects its own SSH allow so a plan cannot lock you out."
        footer={`evaluated top to bottom · ${allRules.filter((r) => r.action === "allow").length} allow · ${allRules.filter((r) => r.action !== "allow").length} deny/reject`}
      />

      <RuleDialog open={dialogOpen} onOpenChange={setDialogOpen} groupId={groupId} rule={editingRule} />

      {deleting && (
        <Confirm
          open
          onOpenChange={(open) => !open && setDeleting(null)}
          title="Delete rule"
          description={`Rule #${deleting.priority} (${deleting.action} ${deleting.protocol} ${formatPorts(deleting)}) is no longer declared by this group; the hosts' rulesets change on the next plan. This cannot be undone.`}
          confirmLabel="Delete"
          variant="destructive"
          loading={deleteMutation.isPending}
          onConfirm={() => deleteMutation.mutate(deleting.id)}
        />
      )}
    </div>
  )
}
