"use client"

import { useEffect } from "react"
import { useQuery } from "@tanstack/react-query"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { ruleSchema, type RuleInput } from "@/lib/schemas"
import type { FirewallRule, Host } from "@/lib/types"
import { Banner, Field, Modal, Seg } from "@/components/ld"
import { GROUP_KEYS } from "./shared"

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

const toPort = (v: unknown) => {
  if (v == null || v === "") return null
  const n = typeof v === "number" ? v : parseInt(String(v), 10)
  return Number.isNaN(n) ? null : n
}

/** One side of a rule: a CIDR, or a managed host whose address is resolved at sync time. */
function SideField({
  label,
  mode,
  onModeChange,
  cidrValue,
  onCidrChange,
  hostId,
  onHostChange,
  hosts,
  cidrError,
}: {
  label: string
  mode: "cidr" | "host"
  onModeChange: (m: "cidr" | "host") => void
  cidrValue: string
  onCidrChange: (v: string) => void
  hostId: number | null
  onHostChange: (id: number | null) => void
  hosts: Host[]
  cidrError?: string
}) {
  const err = mode === "cidr" ? cidrError : undefined
  return (
    <div className="field flex min-w-0 flex-col gap-1" data-invalid={err ? "true" : undefined}>
      <span className="tt flex items-center gap-2">
        {label}
        {err && <span className="normal-case tracking-normal text-danger">· {err}</span>}
        <span className="ml-auto normal-case tracking-normal">
          <Seg
            sm
            options={[
              { k: "cidr", label: "CIDR" },
              { k: "host", label: "Host" },
            ]}
            value={mode}
            onChange={(k) => onModeChange(k as "cidr" | "host")}
          />
        </span>
      </span>
      {mode === "cidr" ? (
        <input className="inp mono" placeholder="0.0.0.0/0" aria-label={`${label} cidr`} value={cidrValue} onChange={(e) => onCidrChange(e.target.value)} />
      ) : (
        <select className="inp mono" aria-label={`${label} host`} value={hostId ?? ""} onChange={(e) => onHostChange(e.target.value ? Number(e.target.value) : null)}>
          <option value="">— pick a host —</option>
          {hosts.map((h) => (
            <option key={h.id} value={h.id}>
              {h.hostname} · {h.ip_address}
            </option>
          ))}
        </select>
      )}
    </div>
  )
}

/** Add or edit one firewall rule of a group. */
export function RuleDialog({ open, onOpenChange, groupId, rule }: { open: boolean; onOpenChange: (open: boolean) => void; groupId: number; rule?: FirewallRule | null }) {
  const form = useForm<RuleInput>({ resolver: zodResolver(ruleSchema), defaultValues: rule ? ruleToFormValues(rule) : defaultValues, mode: "onSubmit" })
  const { data: hosts = [] } = useQuery<Host[]>({ queryKey: ["hosts"], queryFn: () => apiFetch<Host[]>("/api/hosts"), enabled: open })

  const protocol = form.watch("protocol")
  const showPorts = protocol !== "icmp" && protocol !== "any"

  const saveMutation = useApiMutation({
    mutationFn: ({ ruleId, body }: { ruleId?: number; body: Record<string, unknown> }) =>
      ruleId ? apiFetch(`/api/groups/${groupId}/rules/${ruleId}`, { method: "PUT", body: JSON.stringify(body) }) : apiFetch(`/api/groups/${groupId}/rules`, { method: "POST", body: JSON.stringify(body) }),
    invalidateKeys: [["rules", groupId], ...GROUP_KEYS],
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
    saveMutation.mutate({
      ruleId: rule?.id,
      body: {
        action: data.action,
        protocol: data.protocol,
        direction: data.direction,
        source_cidr: data.source_mode === "cidr" ? data.source_cidr || null : null,
        source_host_id: data.source_mode === "host" ? (data.source_host_id ?? null) : null,
        destination_cidr: data.destination_mode === "cidr" ? data.destination_cidr || null : null,
        destination_host_id: data.destination_mode === "host" ? (data.destination_host_id ?? null) : null,
        port_start: showPorts ? data.port_start : null,
        port_end: showPorts ? data.port_end : null,
        comment: data.comment || null,
      },
    })
  })

  const { errors } = form.formState
  if (!open) return null

  return (
    <Modal
      title={rule ? "Edit rule" : "Add rule"}
      meta={rule ? `rule #${rule.priority}` : undefined}
      w={560}
      onClose={() => onOpenChange(false)}
      onSubmit={onSubmit}
      footer={
        <>
          <span className="tt mr-auto">desired state only — nothing applies until a plan runs</span>
          <button type="button" className="btn" onClick={() => onOpenChange(false)} disabled={saveMutation.isPending}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={saveMutation.isPending}>
            {saveMutation.isPending ? "Saving…" : rule ? "Save changes" : "Add rule"}
          </button>
        </>
      }
    >
      <div className="grid gap-[11px] grid-cols-1 sm:grid-cols-[1fr_1fr_1fr]">
        <Field label="action" htmlFor="action" error={errors.action?.message}>
          <select id="action" className="inp" {...form.register("action")}>
            <option value="allow">allow</option>
            <option value="deny">deny</option>
            <option value="reject">reject</option>
          </select>
        </Field>
        <Field label="protocol" htmlFor="protocol" error={errors.protocol?.message}>
          <select id="protocol" className="inp" {...form.register("protocol")}>
            <option value="tcp">tcp</option>
            <option value="udp">udp</option>
            <option value="icmp">icmp</option>
            <option value="any">any</option>
          </select>
        </Field>
        <Field label="direction" htmlFor="direction" error={errors.direction?.message}>
          <select id="direction" className="inp" {...form.register("direction")}>
            <option value="input">input</option>
            <option value="output">output</option>
          </select>
        </Field>
      </div>

      <div className="grid gap-[11px] grid-cols-1 sm:grid-cols-[1fr_1fr]">
        <SideField
          label="source"
          mode={form.watch("source_mode")}
          onModeChange={(m) => form.setValue("source_mode", m)}
          cidrValue={form.watch("source_cidr") ?? ""}
          onCidrChange={(v) => form.setValue("source_cidr", v)}
          hostId={form.watch("source_host_id") ?? null}
          onHostChange={(id) => form.setValue("source_host_id", id)}
          hosts={hosts}
          cidrError={errors.source_cidr?.message}
        />
        <SideField
          label="destination"
          mode={form.watch("destination_mode")}
          onModeChange={(m) => form.setValue("destination_mode", m)}
          cidrValue={form.watch("destination_cidr") ?? ""}
          onCidrChange={(v) => form.setValue("destination_cidr", v)}
          hostId={form.watch("destination_host_id") ?? null}
          onHostChange={(id) => form.setValue("destination_host_id", id)}
          hosts={hosts}
          cidrError={errors.destination_cidr?.message}
        />
      </div>

      {showPorts && (
        <div className="grid gap-[11px] grid-cols-1 sm:grid-cols-[1fr_1fr]">
          <Field label="port" htmlFor="port_start" hint="blank = all ports" error={errors.port_start?.message}>
            <input id="port_start" type="number" min={1} max={65535} className="inp mono num" placeholder="80" {...form.register("port_start", { setValueAs: toPort })} />
          </Field>
          <Field label="port end" htmlFor="port_end" hint="for a range" error={errors.port_end?.message}>
            <input id="port_end" type="number" min={1} max={65535} className="inp mono num" placeholder="443" {...form.register("port_end", { setValueAs: toPort })} />
          </Field>
        </div>
      )}

      <Field label="comment" htmlFor="comment" hint="why does this rule exist?">
        <textarea id="comment" className="inp" rows={2} {...form.register("comment")} />
      </Field>

      {saveMutation.error && <Banner tone="danger">{saveMutation.error.message}</Banner>}
    </Modal>
  )
}
