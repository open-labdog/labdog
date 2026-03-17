"use client"

import { useState, useMemo } from "react"
import { useParams } from "next/navigation"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Lock, GitBranch, GripVertical, ChevronUp, ChevronDown } from "lucide-react"
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
import { apiFetch } from "@/lib/api"
import type { FirewallRule, HostGroup } from "@/lib/types"

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

export default function GroupRulesPage() {
  const params = useParams()
  const id = Number(params.id)
  const queryClient = useQueryClient()

  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingRule, setEditingRule] = useState<FirewallRule | null>(null)
  const [deletingId, setDeletingId] = useState<number | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [reorderError, setReorderError] = useState<string | null>(null)

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

  const systemRules = useMemo(() => rules?.filter((r) => r.is_system) ?? [], [rules])
  const userRules = useMemo(() => rules?.filter((r) => !r.is_system) ?? [], [rules])
  const allRules = useMemo(() => [...systemRules, ...userRules], [systemRules, userRules])
  const sortableIds = useMemo(() => allRules.map((r) => r.id), [allRules])

  const gitopsEnabled = !!group?.gitops_enabled

  async function handleReorder(newOrder: FirewallRule[]) {
    const ruleIds = newOrder.filter((r) => !r.is_system).map((r) => r.id)
    setReorderError(null)
    try {
      await apiFetch(`/api/groups/${id}/rules/reorder`, {
        method: "PUT",
        body: JSON.stringify({ rule_ids: ruleIds }),
      })
      await queryClient.invalidateQueries({ queryKey: ["rules", id] })
    } catch (err) {
      setReorderError(err instanceof Error ? err.message : "Reorder failed")
    }
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

  const handleDelete = async (rule: FirewallRule) => {
    if (!confirm(`Delete rule #${rule.priority} (${rule.action} ${rule.protocol})?`)) return
    setDeletingId(rule.id)
    setDeleteError(null)
    try {
      await apiFetch(`/api/groups/${id}/rules/${rule.id}`, { method: "DELETE" })
      await queryClient.invalidateQueries({ queryKey: ["rules", id] })
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : "Delete failed")
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <div className="space-y-6">
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

      {isLoading && (
        <div className="text-slate-400 py-8 text-center">Loading rules…</div>
      )}

      {error && (
        <div className="text-red-400 py-8 text-center">Failed to load rules</div>
      )}

      {deleteError && (
        <div className="text-red-400 text-sm">{deleteError}</div>
      )}

      {reorderError && (
        <div className="text-red-400 text-sm">{reorderError}</div>
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
                        deletingId={deletingId}
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
    </div>
  )
}
