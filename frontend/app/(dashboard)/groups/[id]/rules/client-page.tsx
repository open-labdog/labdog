"use client"

import { useState, useMemo } from "react"
import { useParams } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { Lock, GitBranch, GripVertical, ChevronUp, ChevronDown } from "lucide-react"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core"
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable"
import { CSS } from "@dnd-kit/utilities"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { RuleDialog } from "@/components/rule-dialog"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { useDelayedLoading } from "@/lib/utils"
import { TableSkeleton } from "@/components/ui/skeleton"
import { Label } from "@/components/ui/label"
import type { FirewallRule, HostGroup, ChainPolicies } from "@/lib/types"

function ActionBadge({ action }: { action: string }) {
  const config: Record<string, string> = {
    allow: "bg-green-600 text-white",
    deny: "bg-red-600 text-white",
    reject: "bg-amber-600 text-white",
  }
  return (
    <Badge className={config[action] ?? ""}>
      {action.charAt(0).toUpperCase() + action.slice(1)}
    </Badge>
  )
}

function formatPorts(rule: FirewallRule): string {
  if (rule.port_start == null) return "—"
  if (rule.port_end != null && rule.port_end !== rule.port_start) {
    return `${rule.port_start}–${rule.port_end}`
  }
  return String(rule.port_start)
}

function SortableRow({
  rule,
  isFirstNonSystem,
  isLastRule,
  isDragDisabled,
  onMoveUp,
  onMoveDown,
  onEdit,
  onDelete,
  deletingId,
  gitopsEnabled,
}: {
  rule: FirewallRule
  isFirstNonSystem: boolean
  isLastRule: boolean
  isDragDisabled: boolean
  onMoveUp: (rule: FirewallRule) => void
  onMoveDown: (rule: FirewallRule) => void
  onEdit: (rule: FirewallRule) => void
  onDelete: (rule: FirewallRule) => void
  deletingId: number | null
  gitopsEnabled: boolean
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({
    id: rule.id,
    disabled: isDragDisabled,
  })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
    position: "relative" as const,
    zIndex: isDragging ? 10 : undefined,
    background: isDragging ? "rgba(59, 130, 246, 0.08)" : undefined,
    outline: isDragging ? "1px solid rgba(59, 130, 246, 0.3)" : undefined,
    borderRadius: isDragging ? "6px" : undefined,
  }

  const arrowDisabled = rule.is_system || gitopsEnabled

  return (
    <TableRow
      ref={setNodeRef}
      style={style}
      className="border-slate-700"
    >
      {/* Drag handle */}
      <TableCell className="w-8 px-2">
        {!isDragDisabled ? (
          <button
            {...attributes}
            {...listeners}
            className="cursor-grab active:cursor-grabbing text-slate-500 hover:text-slate-300 p-0.5 rounded transition-colors"
            aria-label="Drag to reorder"
          >
            <GripVertical className="h-4 w-4" />
          </button>
        ) : (
          <span className="text-slate-700 p-0.5">
            <GripVertical className="h-4 w-4" />
          </span>
        )}
      </TableCell>

      <TableCell className="font-mono text-slate-300">
        <div className="flex items-center gap-1">
          {rule.is_system && (
            <Lock className="h-3 w-3 text-slate-500" aria-label="System rule" />
          )}
          {rule.priority}
        </div>
      </TableCell>
      <TableCell>
        <ActionBadge action={rule.action} />
      </TableCell>
      <TableCell className="text-slate-300 uppercase text-xs">{rule.protocol}</TableCell>
      <TableCell className="text-slate-300 capitalize text-xs">{rule.direction}</TableCell>
      <TableCell className="font-mono text-slate-300 text-xs">{rule.source_cidr ?? "any"}</TableCell>
      <TableCell className="font-mono text-slate-300 text-xs">{rule.destination_cidr ?? "any"}</TableCell>
      <TableCell className="font-mono text-slate-300 text-xs">{formatPorts(rule)}</TableCell>
      <TableCell className="text-slate-400 text-xs max-w-[160px] truncate">{rule.comment ?? "—"}</TableCell>
       <TableCell>
         <div className="flex gap-1">
           {/* Reorder arrows */}
           <Button
             size="sm"
             variant="ghost"
             disabled={arrowDisabled || isFirstNonSystem}
             onClick={() => onMoveUp(rule)}
             title={
               gitopsEnabled
                 ? "Rules are managed via GitOps"
                 : rule.is_system
                   ? "System rules cannot be reordered"
                   : isFirstNonSystem
                     ? "Already at top"
                     : "Move up"
             }
             className="h-7 w-7 p-0"
           >
             <ChevronUp className="h-4 w-4" />
           </Button>
           <Button
             size="sm"
             variant="ghost"
             disabled={arrowDisabled || isLastRule}
             onClick={() => onMoveDown(rule)}
             title={
               gitopsEnabled
                 ? "Rules are managed via GitOps"
                 : rule.is_system
                   ? "System rules cannot be reordered"
                   : isLastRule
                     ? "Already at bottom"
                     : "Move down"
             }
             className="h-7 w-7 p-0"
           >
             <ChevronDown className="h-4 w-4" />
           </Button>
           <Button
             size="sm"
             variant="ghost"
             disabled={rule.is_system || gitopsEnabled}
             onClick={() => onEdit(rule)}
             title={
               gitopsEnabled
                 ? "Rules are managed via GitOps"
                 : rule.is_system
                   ? "System rules cannot be edited"
                   : "Edit rule"
             }
           >
             Edit
           </Button>
           <Button
             size="sm"
             variant="ghost"
             disabled={rule.is_system || deletingId === rule.id || gitopsEnabled}
             onClick={() => onDelete(rule)}
             title={
               gitopsEnabled
                 ? "Rules are managed via GitOps"
                 : rule.is_system
                   ? "System rules cannot be deleted"
                   : "Delete rule"
             }
             className="text-red-400 hover:text-red-300 hover:bg-red-950"
           >
             {deletingId === rule.id ? "…" : "Delete"}
           </Button>
         </div>
       </TableCell>
    </TableRow>
  )
}

export default function GroupRulesPage({ embedded = false }: { embedded?: boolean } = {}) {
  const params = useParams()
  const id = Number(params.id)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingRule, setEditingRule] = useState<FirewallRule | null>(null)
  const [confirmState, setConfirmState] = useState<{
    open: boolean; title: string; description: string; action: () => void | Promise<void>; loading?: boolean
  } | null>(null)

  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: { distance: 5 },
    }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  )

  const { data: group, isLoading: groupLoading } = useQuery<HostGroup>({
    queryKey: ["group", id],
    queryFn: () => apiFetch<HostGroup>(`/api/groups/${id}`),
    enabled: !!id,
  })

  const { data: rules, isLoading, error } = useQuery<FirewallRule[]>({
    queryKey: ["rules", id],
    queryFn: () => apiFetch<FirewallRule[]>(`/api/groups/${id}/rules`),
    enabled: !!id,
  })
  const showLoading = useDelayedLoading(isLoading)

  const systemRules = useMemo(() => rules?.filter((r) => r.is_system) ?? [], [rules])
  const userRules = useMemo(() => rules?.filter((r) => !r.is_system) ?? [], [rules])
  const allRules = useMemo(() => [...systemRules, ...userRules], [systemRules, userRules])
  const sortableIds = useMemo(() => allRules.map((r) => r.id), [allRules])

  const gitopsEnabled = !!group?.gitops_enabled

  const reorderMutation = useApiMutation({
    mutationFn: (ruleIds: number[]) =>
      apiFetch(`/api/groups/${id}/rules/reorder`, { method: "PUT", body: JSON.stringify({ rule_ids: ruleIds }) }),
    invalidateKeys: [["rules", id]],
  })

  const deleteMutation = useApiMutation({
    mutationFn: (ruleId: number) =>
      apiFetch(`/api/groups/${id}/rules/${ruleId}`, { method: "DELETE" }),
    invalidateKeys: [["rules", id]],
  })

  const { data: policies } = useQuery<ChainPolicies>({
    queryKey: ["policies", id],
    queryFn: () => apiFetch<ChainPolicies>(`/api/groups/${id}/policies`),
    enabled: !!id,
  })

  const policyMutation = useApiMutation({
    mutationFn: (body: { input_policy: string | null; output_policy: string | null }) =>
      apiFetch(`/api/groups/${id}/policies`, { method: "PUT", body: JSON.stringify(body) }),
    invalidateKeys: [["policies", id], ["group", id]],
  })

  function handlePolicyChange(chain: "input" | "output", value: string) {
    const policyValue = value === "" ? null : value
    policyMutation.mutate({
      input_policy: chain === "input" ? policyValue : (group?.input_policy ?? null),
      output_policy: chain === "output" ? policyValue : (group?.output_policy ?? null),
    })
  }

  function handleReorder(newOrder: FirewallRule[]) {
    const ruleIds = newOrder.filter((r) => !r.is_system).map((r) => r.id)
    reorderMutation.mutate(ruleIds)
  }

  function handleMoveUp(rule: FirewallRule) {
    if (rule.is_system || gitopsEnabled) return
    const idx = userRules.findIndex((r) => r.id === rule.id)
    if (idx <= 0) return
    const newUserRules = arrayMove(userRules, idx, idx - 1)
    handleReorder([...systemRules, ...newUserRules])
  }

  function handleMoveDown(rule: FirewallRule) {
    if (rule.is_system || gitopsEnabled) return
    const idx = userRules.findIndex((r) => r.id === rule.id)
    if (idx < 0 || idx >= userRules.length - 1) return
    const newUserRules = arrayMove(userRules, idx, idx + 1)
    handleReorder([...systemRules, ...newUserRules])
  }

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event
    if (!over || active.id === over.id) return

    const activeRule = allRules.find((r) => r.id === active.id)
    const overRule = allRules.find((r) => r.id === over.id)
    if (!activeRule || !overRule) return

    // Don't allow dragging system rules or dropping onto system rules
    if (activeRule.is_system || overRule.is_system) return

    const oldIndex = userRules.findIndex((r) => r.id === active.id)
    const newIndex = userRules.findIndex((r) => r.id === over.id)
    if (oldIndex === -1 || newIndex === -1) return

    const newUserRules = arrayMove(userRules, oldIndex, newIndex)
    handleReorder([...systemRules, ...newUserRules])
  }

  const handleAdd = () => {
    setEditingRule(null)
    setDialogOpen(true)
  }

  const handleEdit = (rule: FirewallRule) => {
    setEditingRule(rule)
    setDialogOpen(true)
  }

  const handleDelete = (rule: FirewallRule) => {
    setConfirmState({
      open: true,
      title: "Delete Rule",
      description: `Delete rule #${rule.priority} (${rule.action} ${rule.protocol})? This action cannot be undone.`,
      action: async () => {
        setConfirmState((prev: typeof confirmState) => prev ? { ...prev, loading: true } : null)
        try {
          await deleteMutation.mutateAsync(rule.id)
        } finally {
          setConfirmState(null)
        }
      },
    })
  }

  return (
    <div className="space-y-6">
      {!embedded && <Breadcrumb items={[{ label: "Groups", href: "/groups" }, { label: group?.name ?? "Group", href: `/groups/${id}` }, { label: "Rules" }]} />}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Firewall Rules</h1>
          <p className="text-slate-400 text-sm mt-1">Group ID: {id}</p>
        </div>
        {!gitopsEnabled && <Button onClick={handleAdd}>Add Rule</Button>}
      </div>

      {gitopsEnabled && (
        <div className="flex items-start gap-3 p-4 rounded-lg bg-blue-950 border border-blue-800">
          <GitBranch className="h-5 w-5 text-blue-400 flex-shrink-0 mt-0.5" />
          <div>
            <p className="text-blue-200 font-medium">GitOps Enabled</p>
            <p className="text-blue-300 text-sm mt-1">Rules are managed via GitOps. Changes must be pushed to Git.</p>
          </div>
        </div>
      )}

      {/* Chain Default Policies */}
      <div className="rounded-lg border border-slate-700 bg-slate-900 p-4">
        <h2 className="text-sm font-medium text-slate-300 mb-3">Chain Default Policies</h2>
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-1">
            <Label htmlFor="input-policy" className="text-slate-400 text-xs">INPUT</Label>
            <select
              id="input-policy"
              value={group?.input_policy ?? ""}
              onChange={(e) => handlePolicyChange("input", e.target.value)}
              disabled={gitopsEnabled || policyMutation.isPending}
              className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-slate-500 disabled:opacity-50"
            >
              <option value="">Default (drop)</option>
              <option value="drop">drop</option>
              <option value="accept">accept</option>
            </select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="output-policy" className="text-slate-400 text-xs">OUTPUT</Label>
            <select
              id="output-policy"
              value={group?.output_policy ?? ""}
              onChange={(e) => handlePolicyChange("output", e.target.value)}
              disabled={gitopsEnabled || policyMutation.isPending}
              className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-slate-500 disabled:opacity-50"
            >
              <option value="">Default (accept)</option>
              <option value="accept">accept</option>
              <option value="drop">drop</option>
            </select>
          </div>
        </div>
        {(group?.input_policy === "accept") && (
          <p className="text-amber-400 text-xs mt-2">Warning: INPUT policy set to ACCEPT. All inbound traffic will be allowed by default.</p>
        )}
        {(group?.output_policy === "drop") && (
          <p className="text-amber-400 text-xs mt-2">Warning: OUTPUT policy set to DROP. All outbound traffic will be blocked by default.</p>
        )}
        {policyMutation.error && (
          <p className="text-red-400 text-xs mt-2">{policyMutation.error.message}</p>
        )}
      </div>

      {showLoading && <TableSkeleton rows={5} columns={5} />}

      {error && (
        <div className="text-red-400 py-8 text-center">Failed to load rules</div>
      )}

      {reorderMutation.error && (
        <div className="text-red-400 text-sm">{reorderMutation.error.message}</div>
      )}

      {!isLoading && !error && rules && rules.length === 0 && (
        <div className="text-slate-400 py-8 text-center">
          No rules yet. Click <strong>Add Rule</strong> to create one.
        </div>
      )}

      {!isLoading && !error && rules && rules.length > 0 && (
        <div className="rounded-lg border border-slate-700 bg-slate-900">
          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragEnd={handleDragEnd}
          >
            <SortableContext items={sortableIds} strategy={verticalListSortingStrategy}>
              <Table>
                <TableHeader>
                  <TableRow className="border-slate-700">
                    <TableHead className="w-8 px-2" />
                    <TableHead className="w-16">Priority</TableHead>
                    <TableHead>Action</TableHead>
                    <TableHead>Protocol</TableHead>
                    <TableHead>Direction</TableHead>
                    <TableHead>Source</TableHead>
                    <TableHead>Dest</TableHead>
                    <TableHead>Port(s)</TableHead>
                    <TableHead>Comment</TableHead>
                    <TableHead className="w-48">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {allRules.map((rule, idx) => {
                    const userIdx = userRules.findIndex((r) => r.id === rule.id)
                    const isFirstNonSystem = !rule.is_system && userIdx === 0
                    const isLastRule = !rule.is_system && userIdx === userRules.length - 1
                    const isDragDisabled = rule.is_system || gitopsEnabled

                    return (
                      <SortableRow
                        key={rule.id}
                        rule={rule}
                        isFirstNonSystem={isFirstNonSystem}
                        isLastRule={isLastRule}
                        isDragDisabled={isDragDisabled}
                        onMoveUp={handleMoveUp}
                        onMoveDown={handleMoveDown}
                        onEdit={handleEdit}
                        onDelete={handleDelete}
                        deletingId={deleteMutation.isPending ? -1 : null}
                        gitopsEnabled={gitopsEnabled}
                      />
                    )
                  })}
                </TableBody>
              </Table>
            </SortableContext>
          </DndContext>
        </div>
      )}

      <RuleDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        groupId={id}
        rule={editingRule}
      />

      {confirmState && (
        <ConfirmDialog
          open={confirmState.open}
          onOpenChange={(open) => !open && setConfirmState(null)}
          title={confirmState.title}
          description={confirmState.description}
          confirmLabel="Delete"
          variant="destructive"
          loading={confirmState.loading}
          onConfirm={confirmState.action}
        />
      )}
    </div>
  )
}
