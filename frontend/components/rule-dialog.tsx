"use client"

import { useState, useEffect } from "react"
import { useQueryClient } from "@tanstack/react-query"
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
import { ruleSchema, type RuleInput } from "@/lib/schemas"
import type { FirewallRule } from "@/lib/types"

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
  source_cidr: "",
  destination_cidr: "",
  port_start: null,
  port_end: null,
  comment: "",
}

function ruleToFormValues(rule: FirewallRule): RuleInput {
  return {
    action: rule.action as RuleInput["action"],
    protocol: rule.protocol as RuleInput["protocol"],
    direction: rule.direction as RuleInput["direction"],
    source_cidr: rule.source_cidr ?? "",
    destination_cidr: rule.destination_cidr ?? "",
    port_start: rule.port_start ?? null,
    port_end: rule.port_end ?? null,
    comment: rule.comment ?? "",
  }
}

export function RuleDialog({ open, onOpenChange, groupId, rule }: RuleDialogProps) {
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)

  const form = useForm<RuleInput>({
    resolver: zodResolver(ruleSchema),
    defaultValues: rule ? ruleToFormValues(rule) : defaultValues,
    mode: "onSubmit",
  })

  const protocol = form.watch("protocol")
  const showPorts = protocol !== "icmp" && protocol !== "any"

  // Reset form when dialog opens or rule changes
  useEffect(() => {
    if (open) {
      form.reset(rule ? ruleToFormValues(rule) : defaultValues)
      setError(null)
    }
  }, [open, rule, form])

  const onSubmit = form.handleSubmit(async (data) => {
    setError(null)

    const body: Record<string, unknown> = {
      action: data.action,
      protocol: data.protocol,
      direction: data.direction,
      source_cidr: data.source_cidr || null,
      destination_cidr: data.destination_cidr || null,
      port_start: showPorts ? data.port_start : null,
      port_end: showPorts ? data.port_end : null,
      comment: data.comment || null,
    }

    try {
      if (rule) {
        await apiFetch(`/api/groups/${groupId}/rules/${rule.id}`, {
          method: "PUT",
          body: JSON.stringify(body),
        })
      } else {
        await apiFetch(`/api/groups/${groupId}/rules`, {
          method: "POST",
          body: JSON.stringify(body),
        })
      }
      await queryClient.invalidateQueries({ queryKey: ["rules", groupId] })
      onOpenChange(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save rule")
    }
  })

  const { errors, isSubmitting } = form.formState

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
             <div className="space-y-1">
               <div className="flex items-center gap-1.5">
                 <Label htmlFor="source_cidr" className="text-slate-300">Source CIDR</Label>
                 <Tooltip content="IP range in CIDR notation, e.g., 10.0.0.0/8 or 192.168.1.0/24">
                   <InfoIcon className="w-3.5 h-3.5 text-slate-500 cursor-help" />
                 </Tooltip>
               </div>
               <Input
                 id="source_cidr"
                 placeholder="0.0.0.0/0"
                 {...form.register("source_cidr")}
                 className="bg-slate-800 border-slate-700 text-white placeholder:text-slate-500"
               />
               {errors.source_cidr?.message && <p className="text-sm text-red-400">{errors.source_cidr.message}</p>}
             </div>
             <div className="space-y-1">
               <div className="flex items-center gap-1.5">
                 <Label htmlFor="destination_cidr" className="text-slate-300">Dest CIDR</Label>
                 <Tooltip content="IP range in CIDR notation, e.g., 10.0.0.0/8 or 192.168.1.0/24">
                   <InfoIcon className="w-3.5 h-3.5 text-slate-500 cursor-help" />
                 </Tooltip>
               </div>
               <Input
                 id="destination_cidr"
                 placeholder="0.0.0.0/0"
                 {...form.register("destination_cidr")}
                 className="bg-slate-800 border-slate-700 text-white placeholder:text-slate-500"
               />
               {errors.destination_cidr?.message && <p className="text-sm text-red-400">{errors.destination_cidr.message}</p>}
             </div>
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
                   {...form.register("port_start", { setValueAs: (v: string) => v === "" ? null : parseInt(v, 10) })}
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
                  {...form.register("port_end", { setValueAs: (v: string) => v === "" ? null : parseInt(v, 10) })}
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

          {error && (
            <p className="text-red-400 text-sm">{error}</p>
          )}

          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              onClick={() => onOpenChange(false)}
              disabled={isSubmitting}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? "Saving…" : rule ? "Save Changes" : "Add Rule"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
