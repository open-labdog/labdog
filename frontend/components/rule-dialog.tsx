"use client"

import { useEffect } from "react"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { InfoIcon } from "lucide-react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Tooltip } from "@/components/ui/tooltip"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { ruleSchema, type RuleInput } from "@/lib/schemas"
import type { FirewallRule } from "@/lib/types"
import { HostCombobox } from "@/components/host-combobox"

interface RuleDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  groupId: number
  rule?: FirewallRule | null
}

const defaultValues: RuleInput = {
  action: "allow",
  protocol: "tcp",
  direction: "input",
  source_mode: "cidr",
  destination_mode: "cidr",
  source_cidr: "",
  destination_cidr: "",
  source_host_id: null,
  destination_host_id: null,
  port_start: null,
  port_end: null,
  comment: "",
}

function ruleToFormValues(rule: FirewallRule): RuleInput {
  return {
    action: rule.action as RuleInput["action"],
    protocol: rule.protocol as RuleInput["protocol"],
    direction: rule.direction as RuleInput["direction"],
    source_mode: rule.source_host_id != null ? "host" : "cidr",
    destination_mode: rule.destination_host_id != null ? "host" : "cidr",
    source_cidr: rule.source_cidr ?? "",
    destination_cidr: rule.destination_cidr ?? "",
    source_host_id: rule.source_host_id,
    destination_host_id: rule.destination_host_id,
    port_start: rule.port_start ?? null,
    port_end: rule.port_end ?? null,
    comment: rule.comment ?? "",
  }
}

function SideField({
  label,
  mode,
  onModeChange,
  cidrValue,
  onCidrChange,
  hostId,
  onHostChange,
  cidrError,
}: {
  label: string
  mode: "cidr" | "host"
  onModeChange: (m: "cidr" | "host") => void
  cidrValue: string
  onCidrChange: (v: string) => void
  hostId: number | null
  onHostChange: (id: number | null) => void
  cidrError?: string
}) {
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <Label className="text-slate-300">{label}</Label>
        <div className="flex gap-1 text-xs">
          <button
            type="button"
            onClick={() => onModeChange("cidr")}
            className={`px-2 py-0.5 rounded ${mode === "cidr" ? "bg-slate-700 text-white" : "text-slate-400"}`}
          >
            CIDR
          </button>
          <button
            type="button"
            onClick={() => onModeChange("host")}
            className={`px-2 py-0.5 rounded ${mode === "host" ? "bg-slate-700 text-white" : "text-slate-400"}`}
          >
            Host
          </button>
        </div>
      </div>
      {mode === "cidr" ? (
        <>
          <Input
            placeholder="0.0.0.0/0"
            value={cidrValue}
            onChange={(e) => onCidrChange(e.target.value)}
            className="bg-slate-800 border-slate-700 text-white placeholder:text-slate-500"
          />
          {cidrError && <p className="text-sm text-red-400">{cidrError}</p>}
        </>
      ) : (
        <HostCombobox value={hostId} onChange={onHostChange} />
      )}
    </div>
  )
}


export function RuleDialog({ open, onOpenChange, groupId, rule }: RuleDialogProps) {
  const form = useForm<RuleInput>({
    resolver: zodResolver(ruleSchema),
    defaultValues: rule ? ruleToFormValues(rule) : defaultValues,
    mode: "onSubmit",
  })

  const protocol = form.watch("protocol")
  const showPorts = protocol !== "icmp" && protocol !== "any"

  const saveMutation = useApiMutation({
    mutationFn: ({ ruleId, body }: { ruleId?: number; body: Record<string, unknown> }) => {
      if (ruleId) {
        return apiFetch(`/api/groups/${groupId}/rules/${ruleId}`, { method: "PUT", body: JSON.stringify(body) })
      }
      return apiFetch(`/api/groups/${groupId}/rules`, { method: "POST", body: JSON.stringify(body) })
    },
    invalidateKeys: [["rules", groupId]],
    onSuccess: () => onOpenChange(false),
  })

  useEffect(() => {
    if (open) {
      form.reset(rule ? ruleToFormValues(rule) : defaultValues)
      saveMutation.reset()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, rule, form])

  const onSubmit = form.handleSubmit((data) => {
    const body: Record<string, unknown> = {
      action: data.action,
      protocol: data.protocol,
      direction: data.direction,
      source_cidr: data.source_mode === "cidr" ? (data.source_cidr || null) : null,
      source_host_id: data.source_mode === "host" ? (data.source_host_id ?? null) : null,
      destination_cidr: data.destination_mode === "cidr" ? (data.destination_cidr || null) : null,
      destination_host_id: data.destination_mode === "host" ? (data.destination_host_id ?? null) : null,
      port_start: showPorts ? data.port_start : null,
      port_end: showPorts ? data.port_end : null,
      comment: data.comment || null,
    }
    saveMutation.mutate({ ruleId: rule?.id, body })
  })

  const { errors } = form.formState

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="bg-slate-900 border-slate-700 text-white max-w-lg">
        <DialogHeader>
          <DialogTitle>{rule ? "Edit Rule" : "Add Rule"}</DialogTitle>
        </DialogHeader>

        <form onSubmit={onSubmit} className="space-y-4">
          <div className="grid grid-cols-3 gap-4">
            {/* Action */}
            <div className="space-y-1">
              <Label htmlFor="action" className="text-slate-300">Action</Label>
              <select
                id="action"
                {...form.register("action")}
                className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-slate-500"
              >
                <option value="allow">Allow</option>
                <option value="deny">Deny</option>
                <option value="reject">Reject</option>
              </select>
              {errors.action?.message && <p className="text-sm text-red-400">{errors.action.message}</p>}
            </div>

            {/* Protocol */}
            <div className="space-y-1">
              <Label htmlFor="protocol" className="text-slate-300">Protocol</Label>
              <select
                id="protocol"
                {...form.register("protocol")}
                className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-slate-500"
              >
                <option value="tcp">TCP</option>
                <option value="udp">UDP</option>
                <option value="icmp">ICMP</option>
                <option value="any">Any</option>
              </select>
              {errors.protocol?.message && <p className="text-sm text-red-400">{errors.protocol.message}</p>}
            </div>

            {/* Direction */}
            <div className="space-y-1">
              <Label htmlFor="direction" className="text-slate-300">Direction</Label>
              <select
                id="direction"
                {...form.register("direction")}
                className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-slate-500"
              >
                <option value="input">Input</option>
                <option value="output">Output</option>
              </select>
              {errors.direction?.message && <p className="text-sm text-red-400">{errors.direction.message}</p>}
            </div>
          </div>

           {/* Source / Dest */}
           <div className="grid grid-cols-2 gap-4">
             <SideField
               label="Source"
               mode={form.watch("source_mode")}
               onModeChange={(m) => form.setValue("source_mode", m)}
               cidrValue={form.watch("source_cidr") ?? ""}
               onCidrChange={(v) => form.setValue("source_cidr", v)}
               hostId={form.watch("source_host_id") ?? null}
               onHostChange={(id) => form.setValue("source_host_id", id)}
               cidrError={errors.source_cidr?.message}
             />
             <SideField
               label="Destination"
               mode={form.watch("destination_mode")}
               onModeChange={(m) => form.setValue("destination_mode", m)}
               cidrValue={form.watch("destination_cidr") ?? ""}
               onCidrChange={(v) => form.setValue("destination_cidr", v)}
               hostId={form.watch("destination_host_id") ?? null}
               onHostChange={(id) => form.setValue("destination_host_id", id)}
               cidrError={errors.destination_cidr?.message}
             />
           </div>

           {/* Ports - only shown for tcp/udp */}
           {showPorts && (
             <div className="grid grid-cols-2 gap-4">
               <div className="space-y-1">
                 <div className="flex items-center gap-1.5">
                   <Label htmlFor="port_start" className="text-slate-300">Port Start</Label>
                   <Tooltip content="Single port (e.g., 80) or start of range. Leave empty for all ports.">
                     <InfoIcon className="w-3.5 h-3.5 text-slate-500 cursor-help" />
                   </Tooltip>
                 </div>
                 <Input
                   id="port_start"
                   type="number"
                   min={1}
                   max={65535}
                   placeholder="e.g. 80"
                   {...form.register("port_start", { setValueAs: (v: unknown) => {
    if (v == null || v === "") return null
    const n = typeof v === "number" ? v : parseInt(String(v), 10)
    return Number.isNaN(n) ? null : n
  }
})}
                   className="bg-slate-800 border-slate-700 text-white placeholder:text-slate-500"
                 />
                 {errors.port_start?.message && <p className="text-sm text-red-400">{errors.port_start.message}</p>}
               </div>
              <div className="space-y-1">
                <Label htmlFor="port_end" className="text-slate-300">Port End <span className="text-slate-500">(optional)</span></Label>
                <Input
                  id="port_end"
                  type="number"
                  min={1}
                  max={65535}
                  placeholder="e.g. 443"
                  {...form.register("port_end", { setValueAs: (v: unknown) => {
    if (v == null || v === "") return null
    const n = typeof v === "number" ? v : parseInt(String(v), 10)
    return Number.isNaN(n) ? null : n
  }
})}
                  className="bg-slate-800 border-slate-700 text-white placeholder:text-slate-500"
                />
                {errors.port_end?.message && <p className="text-sm text-red-400">{errors.port_end.message}</p>}
              </div>
            </div>
           )}

          {/* Comment */}
          <div className="space-y-1">
            <Label htmlFor="comment" className="text-slate-300">Comment</Label>
            <textarea
              id="comment"
              rows={2}
              placeholder="Optional description"
              {...form.register("comment")}
              className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-white placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-500 resize-none"
            />
          </div>

          {saveMutation.error && (
            <p className="text-red-400 text-sm">{saveMutation.error.message}</p>
          )}

          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              onClick={() => onOpenChange(false)}
              disabled={saveMutation.isPending}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={saveMutation.isPending}>
              {saveMutation.isPending ? "Saving…" : rule ? "Save Changes" : "Add Rule"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
