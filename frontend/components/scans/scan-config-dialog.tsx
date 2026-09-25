"use client"

import { useEffect, useRef, useState } from "react"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { scanConfigSchema, cidrNetworkRegex, type ScanConfigInput } from "@/lib/schemas"
import { Banner, Field, Modal, Seg, Tag } from "@/components/ld"
import { GroupMultiSelect } from "@/components/group-multi-select"
import type { ScanConfig, SSHKey, HostGroup } from "@/lib/types"

interface ScanConfigDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  config?: ScanConfig
}

type IntervalUnit = "minutes" | "hours" | "days"

function toValueAndUnit(minutes: number): { value: number; unit: IntervalUnit } {
  if (minutes % (60 * 24) === 0) return { value: minutes / (60 * 24), unit: "days" }
  if (minutes % 60 === 0) return { value: minutes / 60, unit: "hours" }
  return { value: minutes, unit: "minutes" }
}
function toMinutes(value: number, unit: IntervalUnit): number {
  if (unit === "days") return value * 60 * 24
  if (unit === "hours") return value * 60
  return value
}

const emptyDefaults: ScanConfigInput = {
  name: "", cidrs: [], ssh_key_id: 0, ssh_port: 22, default_group_ids: [],
  schedule_type: "interval", interval_value: 60, interval_unit: "minutes",
  cron_expression: null, enabled: true, auto_add: false,
}

function configToFormValues(c: ScanConfig): ScanConfigInput {
  const scheduleType: "interval" | "cron" = c.interval_minutes != null ? "interval" : "cron"
  const { value, unit } = c.interval_minutes != null ? toValueAndUnit(c.interval_minutes) : { value: 60, unit: "minutes" as IntervalUnit }
  return {
    name: c.name, cidrs: c.cidrs, ssh_key_id: c.ssh_key_id, ssh_port: c.ssh_port, default_group_ids: c.default_group_ids,
    schedule_type: scheduleType, interval_value: value, interval_unit: unit,
    cron_expression: c.cron_expression ?? null, enabled: c.enabled, auto_add: c.auto_add,
  }
}

/** A CIDR chip list — enter, tab or blur to add one. */
function CidrTagInput({ value, onChange }: { value: string[]; onChange: (tags: string[]) => void }) {
  const [inputVal, setInputVal] = useState("")
  const [inputError, setInputError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  function validateAndAdd(raw: string) {
    const cidr = raw.trim()
    if (!cidr) return
    if (!cidrNetworkRegex.test(cidr)) {
      setInputError(`"${cidr}" is not a valid CIDR (e.g. 192.168.1.0/24)`)
      return
    }
    if (value.includes(cidr)) {
      setInputError(`"${cidr}" is already in the list`)
      return
    }
    setInputError(null)
    setInputVal("")
    onChange([...value, cidr])
  }

  return (
    <>
      <div className="inp mono flex min-h-[32px] flex-wrap items-center gap-1.5 py-1.5" onClick={() => inputRef.current?.focus()}>
        {value.map((cidr) => (
          <Tag key={cidr} onClick={() => onChange(value.filter((c) => c !== cidr))} title="click to remove">
            {cidr} ×
          </Tag>
        ))}
        <input
          ref={inputRef}
          type="text"
          value={inputVal}
          onChange={(e) => { setInputVal(e.target.value); setInputError(null) }}
          onKeyDown={(e) => {
            if (e.key === "Enter") { e.preventDefault(); validateAndAdd(inputVal) }
            if (e.key === "Backspace" && inputVal === "" && value.length > 0) onChange(value.slice(0, -1))
          }}
          onBlur={() => (inputVal.trim() ? validateAndAdd(inputVal) : setInputError(null))}
          placeholder={value.length === 0 ? "e.g. 192.168.1.0/24 — enter to add" : ""}
          className="min-w-[140px] flex-1 border-0 bg-transparent p-0 outline-none"
        />
      </div>
      {inputError && <span className="text-[11px] text-danger">{inputError}</span>}
    </>
  )
}

export function ScanConfigDialog({ open, onOpenChange, config }: ScanConfigDialogProps) {
  const isEdit = config != null

  const { data: sshKeys = [] } = useQuery<SSHKey[]>({ queryKey: ["ssh-keys"], queryFn: () => apiFetch<SSHKey[]>("/api/ssh-keys"), enabled: open })
  const { data: groups = [] } = useQuery<HostGroup[]>({ queryKey: ["groups"], queryFn: () => apiFetch<HostGroup[]>("/api/groups"), enabled: open })

  const form = useForm<ScanConfigInput>({ resolver: zodResolver(scanConfigSchema), defaultValues: config ? configToFormValues(config) : emptyDefaults, mode: "onSubmit" })
  const scheduleType = form.watch("schedule_type")
  const autoAdd = form.watch("auto_add")
  const cidrs = form.watch("cidrs")
  const defaultGroupIds = form.watch("default_group_ids") ?? []

  useEffect(() => {
    if (open) {
      form.reset(config ? configToFormValues(config) : emptyDefaults)
      saveMutation.reset()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, config])

  const saveMutation = useApiMutation<ScanConfig, ScanConfigInput>({
    mutationFn: (data) => {
      const payload = {
        name: data.name, cidrs: data.cidrs, ssh_key_id: data.ssh_key_id, ssh_port: data.ssh_port, default_group_ids: data.default_group_ids,
        interval_minutes: data.schedule_type === "interval" && data.interval_value != null ? toMinutes(data.interval_value, data.interval_unit ?? "minutes") : null,
        cron_expression: data.schedule_type === "cron" ? (data.cron_expression ?? null) : null,
        enabled: data.enabled, auto_add: data.auto_add,
      }
      return isEdit
        ? apiFetch<ScanConfig>(`/api/scans/${config.id}`, { method: "PUT", body: JSON.stringify(payload) })
        : apiFetch<ScanConfig>("/api/scans", { method: "POST", body: JSON.stringify(payload) })
    },
    invalidateKeys: [["scans"]],
    successMessage: isEdit ? "Scan config updated" : "Scan config created",
    onSuccess: () => onOpenChange(false),
  })

  const onSubmit = form.handleSubmit((data) => saveMutation.mutate(data))
  const { errors } = form.formState

  return (
    <Modal
      title={isEdit ? "Edit scan config" : "Add scan config"}
      w={560}
      onClose={() => onOpenChange(false)}
      onSubmit={onSubmit}
      footer={
        <>
          <button type="button" className="btn ml-auto" disabled={saveMutation.isPending} onClick={() => onOpenChange(false)}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={saveMutation.isPending}>
            {saveMutation.isPending ? (isEdit ? "Saving…" : "Creating…") : isEdit ? "Save changes" : "Create"}
          </button>
        </>
      }
    >
      <Field label="name" htmlFor="sc-name" error={errors.name?.message}>
        <input id="sc-name" className="inp" placeholder="e.g. Office LAN weekly scan" {...form.register("name")} />
      </Field>

      <Field as="div" label="cidrs" hint="enter or tab to add" error={errors.cidrs?.message}>
        <CidrTagInput value={cidrs} onChange={(tags) => form.setValue("cidrs", tags, { shouldValidate: false })} />
      </Field>

      <div className="grid grid-cols-1 gap-[11px] sm:grid-cols-[1fr_100px]">
        <Field as="div" label="ssh key" error={errors.ssh_key_id?.message}>
          <select id="sc-ssh-key" className="inp mono" {...form.register("ssh_key_id", { setValueAs: (v: string) => (v === "" ? 0 : parseInt(v, 10)) })}>
            <option value="">— select a key —</option>
            {sshKeys.map((k) => <option key={k.id} value={k.id}>{k.name}</option>)}
          </select>
        </Field>
        <Field label="ssh port" htmlFor="sc-ssh-port" error={errors.ssh_port?.message}>
          <input id="sc-ssh-port" type="number" min={1} max={65535} className="inp mono num" {...form.register("ssh_port", { setValueAs: (v: string) => (v === "" ? 22 : parseInt(v, 10)) })} />
        </Field>
      </div>

      <GroupMultiSelect groups={groups} selected={defaultGroupIds} onChange={(ids) => form.setValue("default_group_ids", ids, { shouldValidate: false })} label="default groups" />

      <Field as="div" label="schedule">
        <Seg options={[{ k: "interval", label: "Interval" }, { k: "cron", label: "Cron" }]} value={scheduleType} onChange={(k) => form.setValue("schedule_type", k as "interval" | "cron")} />
      </Field>

      {scheduleType === "interval" && (
        <div className="grid grid-cols-[1fr_120px] gap-[11px]">
          <Field label="every" htmlFor="sc-interval-value" error={errors.interval_value?.message}>
            <input id="sc-interval-value" type="number" min={1} max={10080} className="inp mono num" placeholder="60" {...form.register("interval_value", { setValueAs: (v: string) => (v === "" ? null : parseInt(v, 10)) })} />
          </Field>
          <Field as="div" label="unit">
            <select className="inp" {...form.register("interval_unit")}>
              <option value="minutes">minutes</option>
              <option value="hours">hours</option>
              <option value="days">days</option>
            </select>
          </Field>
        </div>
      )}

      {scheduleType === "cron" && (
        <Field label="cron expression" htmlFor="sc-cron" hint="5-field cron — minute hour day month weekday" error={errors.cron_expression?.message}>
          <input id="sc-cron" className="inp mono" placeholder="0 2 * * *" {...form.register("cron_expression")} />
        </Field>
      )}

      <label className="flex items-center gap-2 text-xs text-text">
        <input id="sc-enabled" type="checkbox" {...form.register("enabled")} /> enabled
      </label>

      <label className="flex items-center gap-2 text-xs text-text">
        <input id="sc-auto-add" type="checkbox" {...form.register("auto_add")} /> automatically add discovered hosts without manual approval
      </label>
      {autoAdd && <Banner tone="warn">Hosts will be added without manual review. Use only on networks you trust completely.</Banner>}

      {saveMutation.error && <Banner tone="danger">{saveMutation.error.message}</Banner>}
    </Modal>
  )
}
