"use client"

import { useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
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
import { apiFetch } from "@/lib/api"
import type { FirewallRule } from "@/lib/types"

interface RuleDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  groupId: number
  rule?: FirewallRule | null
}

type FormData = {
  action: string
  protocol: string
  direction: string
  source_cidr: string
  destination_cidr: string
  port_start: string
  port_end: string
  comment: string
}

const defaultForm: FormData = {
  action: "allow",
  protocol: "tcp",
  direction: "input",
  source_cidr: "",
  destination_cidr: "",
  port_start: "",
  port_end: "",
  comment: "",
}

export function RuleDialog({ open, onOpenChange, groupId, rule }: RuleDialogProps) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<FormData>(() =>
    rule
      ? {
          action: rule.action,
          protocol: rule.protocol,
          direction: rule.direction,
          source_cidr: rule.source_cidr ?? "",
          destination_cidr: rule.destination_cidr ?? "",
          port_start: rule.port_start != null ? String(rule.port_start) : "",
          port_end: rule.port_end != null ? String(rule.port_end) : "",
          comment: rule.comment ?? "",
        }
      : defaultForm
  )
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  // Reset form when dialog opens/closes or rule changes
  const handleOpenChange = (val: boolean) => {
    if (val) {
      setForm(
        rule
          ? {
              action: rule.action,
              protocol: rule.protocol,
              direction: rule.direction,
              source_cidr: rule.source_cidr ?? "",
              destination_cidr: rule.destination_cidr ?? "",
              port_start: rule.port_start != null ? String(rule.port_start) : "",
              port_end: rule.port_end != null ? String(rule.port_end) : "",
              comment: rule.comment ?? "",
            }
          : defaultForm
      )
      setError(null)
    }
    onOpenChange(val)
  }

  const set = (field: keyof FormData) => (
    e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>
  ) => setForm((f) => ({ ...f, [field]: e.target.value }))

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setSubmitting(true)

    const body: Record<string, unknown> = {
      action: form.action,
      protocol: form.protocol,
      direction: form.direction,
      source_cidr: form.source_cidr || null,
      destination_cidr: form.destination_cidr || null,
      port_start: form.port_start ? parseInt(form.port_start, 10) : null,
      port_end: form.port_end ? parseInt(form.port_end, 10) : null,
      comment: form.comment || null,
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
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="bg-slate-900 border-slate-700 text-white max-w-lg">
        <DialogHeader>
          <DialogTitle>{rule ? "Edit Rule" : "Add Rule"}</DialogTitle>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid grid-cols-3 gap-4">
            {/* Action */}
            <div className="space-y-1">
              <Label htmlFor="action" className="text-slate-300">Action</Label>
              <select
                id="action"
                value={form.action}
                onChange={set("action")}
                className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-slate-500"
              >
                <option value="allow">Allow</option>
                <option value="deny">Deny</option>
                <option value="reject">Reject</option>
              </select>
            </div>

            {/* Protocol */}
            <div className="space-y-1">
              <Label htmlFor="protocol" className="text-slate-300">Protocol</Label>
              <select
                id="protocol"
                value={form.protocol}
                onChange={set("protocol")}
                className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-slate-500"
              >
                <option value="tcp">TCP</option>
                <option value="udp">UDP</option>
                <option value="icmp">ICMP</option>
                <option value="any">Any</option>
              </select>
            </div>

            {/* Direction */}
            <div className="space-y-1">
              <Label htmlFor="direction" className="text-slate-300">Direction</Label>
              <select
                id="direction"
                value={form.direction}
                onChange={set("direction")}
                className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-slate-500"
              >
                <option value="input">Input</option>
                <option value="output">Output</option>
              </select>
            </div>
          </div>

          {/* Source / Dest */}
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1">
              <Label htmlFor="source_cidr" className="text-slate-300">Source CIDR</Label>
              <Input
                id="source_cidr"
                placeholder="0.0.0.0/0"
                value={form.source_cidr}
                onChange={set("source_cidr")}
                className="bg-slate-800 border-slate-700 text-white placeholder:text-slate-500"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="destination_cidr" className="text-slate-300">Dest CIDR</Label>
              <Input
                id="destination_cidr"
                placeholder="0.0.0.0/0"
                value={form.destination_cidr}
                onChange={set("destination_cidr")}
                className="bg-slate-800 border-slate-700 text-white placeholder:text-slate-500"
              />
            </div>
          </div>

          {/* Ports */}
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1">
              <Label htmlFor="port_start" className="text-slate-300">Port Start</Label>
              <Input
                id="port_start"
                type="number"
                min={1}
                max={65535}
                placeholder="e.g. 80"
                value={form.port_start}
                onChange={set("port_start")}
                className="bg-slate-800 border-slate-700 text-white placeholder:text-slate-500"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="port_end" className="text-slate-300">Port End <span className="text-slate-500">(optional)</span></Label>
              <Input
                id="port_end"
                type="number"
                min={1}
                max={65535}
                placeholder="e.g. 443"
                value={form.port_end}
                onChange={set("port_end")}
                className="bg-slate-800 border-slate-700 text-white placeholder:text-slate-500"
              />
            </div>
          </div>

          {/* Comment */}
          <div className="space-y-1">
            <Label htmlFor="comment" className="text-slate-300">Comment</Label>
            <textarea
              id="comment"
              rows={2}
              placeholder="Optional description"
              value={form.comment}
              onChange={set("comment")}
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
              disabled={submitting}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={submitting}>
              {submitting ? "Saving…" : rule ? "Save Changes" : "Add Rule"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
