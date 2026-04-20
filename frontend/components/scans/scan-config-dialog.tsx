"use client"

import { useEffect, useRef, useState } from "react"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { X, InfoIcon } from "lucide-react"
import { useQuery } from "@tanstack/react-query"
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
import { Badge } from "@/components/ui/badge"
import { Tooltip } from "@/components/ui/tooltip"
import { GroupMultiSelect } from "@/components/group-multi-select"
import { apiFetch } from "@/lib/api"
import { useApiMutation } from "@/lib/mutations"
import { scanConfigSchema, cidrNetworkRegex, type ScanConfigInput } from "@/lib/schemas"
import type { ScanConfig, SSHKey, HostGroup } from "@/lib/types"

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ScanConfigDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  config?: ScanConfig
}

// ---------------------------------------------------------------------------
// Helpers — convert interval_minutes to/from value+unit
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Default form values
// ---------------------------------------------------------------------------

const emptyDefaults: ScanConfigInput = {
  name: "",
  cidrs: [],
  ssh_key_id: 0,
  ssh_user: "root",
  ssh_port: 22,
  default_group_ids: [],
  schedule_type: "interval",
  interval_value: 60,
  interval_unit: "minutes",
  cron_expression: null,
  enabled: true,
  auto_add: false,
}

function configToFormValues(c: ScanConfig): ScanConfigInput {
  const scheduleType: "interval" | "cron" = c.interval_minutes != null ? "interval" : "cron"
  const { value, unit } =
    c.interval_minutes != null ? toValueAndUnit(c.interval_minutes) : { value: 60, unit: "minutes" as IntervalUnit }
  return {
    name: c.name,
    cidrs: c.cidrs,
    ssh_key_id: c.ssh_key_id,
    ssh_user: c.ssh_user,
    ssh_port: c.ssh_port,
    default_group_ids: c.default_group_ids,
    schedule_type: scheduleType,
    interval_value: value,
    interval_unit: unit,
    cron_expression: c.cron_expression ?? null,
    enabled: c.enabled,
    auto_add: c.auto_add,
  }
}

// ---------------------------------------------------------------------------
// CIDR tag-input sub-component
// ---------------------------------------------------------------------------

interface CidrTagInputProps {
  value: string[]
  onChange: (tags: string[]) => void
  error?: string
}

function CidrTagInput({ value, onChange, error }: CidrTagInputProps) {
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

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") {
      e.preventDefault()
      validateAndAdd(inputVal)
    }
    if (e.key === "Backspace" && inputVal === "" && value.length > 0) {
      onChange(value.slice(0, -1))
    }
  }

  function handleBlur() {
    if (inputVal.trim()) {
      validateAndAdd(inputVal)
    } else {
      setInputError(null)
    }
  }

  function remove(cidr: string) {
    onChange(value.filter((c) => c !== cidr))
  }

  return (
    <div className="space-y-1">
      <div
        className="flex min-h-[38px] w-full flex-wrap items-center gap-1.5 rounded-lg border border-input bg-transparent px-2.5 py-1.5 text-sm text-foreground focus-within:ring-2 focus-within:ring-ring focus-within:border-ring dark:bg-input/30 cursor-text"
        onClick={() => inputRef.current?.focus()}
      >
        {value.map((cidr) => (
          <Badge
            key={cidr}
            variant="secondary"
            className="gap-1 pr-1 font-mono text-xs"
          >
            {cidr}
            <span
              role="button"
              tabIndex={0}
              aria-label={`Remove ${cidr}`}
              className="ml-0.5 rounded-full p-0.5 hover:bg-muted-foreground/20 cursor-pointer"
              onMouseDown={(e) => {
                e.preventDefault()
                e.stopPropagation()
                remove(cidr)
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault()
                  e.stopPropagation()
                  remove(cidr)
                }
              }}
            >
              <X className="h-3 w-3" />
            </span>
          </Badge>
        ))}
        <input
          ref={inputRef}
          type="text"
          value={inputVal}
          onChange={(e) => {
            setInputVal(e.target.value)
            setInputError(null)
          }}
          onKeyDown={handleKeyDown}
          onBlur={handleBlur}
          placeholder={value.length === 0 ? "e.g. 192.168.1.0/24 — Enter to add" : ""}
          className="flex-1 min-w-[160px] bg-transparent outline-none placeholder:text-muted-foreground font-mono text-xs"
          autoComplete="off"
          autoCorrect="off"
          spellCheck={false}
        />
      </div>
      {(inputError ?? error) && (
        <p className="text-sm text-red-400">{inputError ?? error}</p>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main dialog
// ---------------------------------------------------------------------------

export function ScanConfigDialog({ open, onOpenChange, config }: ScanConfigDialogProps) {
  const isEdit = config != null

  // ── Remote data ──────────────────────────────────────────────────────────
  const { data: sshKeys = [] } = useQuery<SSHKey[]>({
    queryKey: ["ssh-keys"],
    queryFn: () => apiFetch<SSHKey[]>("/api/ssh-keys"),
    enabled: open,
  })

  const { data: groups = [] } = useQuery<HostGroup[]>({
    queryKey: ["groups"],
    queryFn: () => apiFetch<HostGroup[]>("/api/groups"),
    enabled: open,
  })

  // ── Form ─────────────────────────────────────────────────────────────────
  const form = useForm<ScanConfigInput>({
    resolver: zodResolver(scanConfigSchema),
    defaultValues: config ? configToFormValues(config) : emptyDefaults,
    mode: "onSubmit",
  })

  const scheduleType = form.watch("schedule_type")
  const autoAdd = form.watch("auto_add")
  const cidrs = form.watch("cidrs")
  const defaultGroupIds = form.watch("default_group_ids") ?? []

  // Reset when dialog opens/config changes
  useEffect(() => {
    if (open) {
      form.reset(config ? configToFormValues(config) : emptyDefaults)
      saveMutation.reset()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, config])

  // ── Mutation ─────────────────────────────────────────────────────────────
  const saveMutation = useApiMutation<ScanConfig, ScanConfigInput>({
    mutationFn: (data) => {
      const intervalMinutes =
        data.schedule_type === "interval" && data.interval_value != null
          ? toMinutes(data.interval_value, data.interval_unit ?? "minutes")
          : null
      const cronExpression =
        data.schedule_type === "cron" ? (data.cron_expression ?? null) : null

      const payload = {
        name: data.name,
        cidrs: data.cidrs,
        ssh_key_id: data.ssh_key_id,
        ssh_user: data.ssh_user,
        ssh_port: data.ssh_port,
        default_group_ids: data.default_group_ids,
        interval_minutes: intervalMinutes,
        cron_expression: cronExpression,
        enabled: data.enabled,
        auto_add: data.auto_add,
      }

      if (isEdit) {
        return apiFetch<ScanConfig>(`/api/scans/${config.id}`, {
          method: "PUT",
          body: JSON.stringify(payload),
        })
      }
      return apiFetch<ScanConfig>("/api/scans", {
        method: "POST",
        body: JSON.stringify(payload),
      })
    },
    invalidateKeys: [["scans"]],
    successMessage: isEdit ? "Scan config updated" : "Scan config created",
    onSuccess: () => onOpenChange(false),
  })

  // ── Submit ────────────────────────────────────────────────────────────────
  const onSubmit = form.handleSubmit((data) => {
    saveMutation.mutate(data)
  })

  const { errors } = form.formState

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="bg-slate-900 border-slate-700 text-white max-w-xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit Scan Config" : "Add Scan Config"}</DialogTitle>
        </DialogHeader>

        <form onSubmit={onSubmit} className="space-y-6 mt-1">
          {/* ── Section 1: Identity ───────────────────────────────────────── */}
          <section className="space-y-3">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
              Identity
            </h3>

            <div className="space-y-1.5">
              <Label htmlFor="sc-name">Name</Label>
              <Input
                id="sc-name"
                {...form.register("name")}
                placeholder="e.g. Office LAN weekly scan"
                className="bg-slate-800 border-slate-700 text-white placeholder:text-slate-500"
              />
              {errors.name && (
                <p className="text-sm text-red-400">{errors.name.message}</p>
              )}
            </div>
          </section>

          {/* ── Section 2: Scan targets ───────────────────────────────────── */}
          <section className="space-y-3">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
              Scan Targets
            </h3>

            {/* CIDRs tag input */}
            <div className="space-y-1.5">
              <div className="flex items-center gap-1.5">
                <Label>CIDRs</Label>
                <Tooltip content="Enter one CIDR block per tag (e.g. 10.0.0.0/8). Press Enter or Tab away to add.">
                  <InfoIcon className="w-3.5 h-3.5 text-slate-500 cursor-help" />
                </Tooltip>
              </div>
              <CidrTagInput
                value={cidrs}
                onChange={(tags) => form.setValue("cidrs", tags, { shouldValidate: false })}
                error={errors.cidrs?.message}
              />
            </div>

            {/* SSH key */}
            <div className="space-y-1.5">
              <Label htmlFor="sc-ssh-key">SSH Key</Label>
              <select
                id="sc-ssh-key"
                {...form.register("ssh_key_id", {
                  setValueAs: (v: string) => (v === "" ? 0 : parseInt(v, 10)),
                })}
                className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-slate-500"
              >
                <option value="">— Select a key —</option>
                {sshKeys.map((k) => (
                  <option key={k.id} value={k.id}>
                    {k.name}
                    {k.public_key
                      ? ` · ${k.public_key.split(" ").slice(0, 2).join(" ").slice(-32)}`
                      : ""}
                  </option>
                ))}
              </select>
              {errors.ssh_key_id && (
                <p className="text-sm text-red-400">{errors.ssh_key_id.message}</p>
              )}
            </div>

            {/* SSH user + SSH port side by side */}
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <Label htmlFor="sc-ssh-user">SSH User</Label>
                <Input
                  id="sc-ssh-user"
                  {...form.register("ssh_user")}
                  placeholder="root"
                  className="bg-slate-800 border-slate-700 text-white placeholder:text-slate-500"
                />
                {errors.ssh_user && (
                  <p className="text-sm text-red-400">{errors.ssh_user.message}</p>
                )}
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="sc-ssh-port">SSH Port</Label>
                <Input
                  id="sc-ssh-port"
                  type="number"
                  min={1}
                  max={65535}
                  {...form.register("ssh_port", {
                    setValueAs: (v: string) => (v === "" ? 22 : parseInt(v, 10)),
                  })}
                  className="bg-slate-800 border-slate-700 text-white"
                />
                {errors.ssh_port && (
                  <p className="text-sm text-red-400">{errors.ssh_port.message}</p>
                )}
              </div>
            </div>
          </section>

          {/* ── Section 3: Behaviour ─────────────────────────────────────── */}
          <section className="space-y-3">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
              Behaviour
            </h3>

            {/* Default groups */}
            <GroupMultiSelect
              groups={groups}
              selected={defaultGroupIds}
              onChange={(ids) =>
                form.setValue("default_group_ids", ids, { shouldValidate: false })
              }
              label="Default Groups"
            />

            {/* Schedule toggle */}
            <div className="space-y-2">
              <Label>Schedule</Label>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => form.setValue("schedule_type", "interval")}
                  className={`px-3 py-1.5 rounded-md text-sm border transition-colors ${
                    scheduleType === "interval"
                      ? "bg-slate-700 border-slate-600 text-white"
                      : "border-slate-700 text-slate-400 hover:border-slate-600 hover:text-slate-300"
                  }`}
                >
                  Interval
                </button>
                <button
                  type="button"
                  onClick={() => form.setValue("schedule_type", "cron")}
                  className={`px-3 py-1.5 rounded-md text-sm border transition-colors ${
                    scheduleType === "cron"
                      ? "bg-slate-700 border-slate-600 text-white"
                      : "border-slate-700 text-slate-400 hover:border-slate-600 hover:text-slate-300"
                  }`}
                >
                  Cron
                </button>
              </div>

              {scheduleType === "interval" && (
                <div className="flex gap-2 items-start">
                  <div className="flex-1 space-y-1">
                    <Input
                      type="number"
                      min={1}
                      max={10080}
                      placeholder="60"
                      {...form.register("interval_value", {
                        setValueAs: (v: string) =>
                          v === "" ? null : parseInt(v, 10),
                      })}
                      className="bg-slate-800 border-slate-700 text-white"
                    />
                    {errors.interval_value && (
                      <p className="text-sm text-red-400">
                        {errors.interval_value.message}
                      </p>
                    )}
                  </div>
                  <select
                    {...form.register("interval_unit")}
                    className="rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-slate-500"
                  >
                    <option value="minutes">minutes</option>
                    <option value="hours">hours</option>
                    <option value="days">days</option>
                  </select>
                </div>
              )}

              {scheduleType === "cron" && (
                <div className="space-y-1">
                  <div className="flex items-center gap-1.5">
                    <Label htmlFor="sc-cron">Cron expression</Label>
                    <Tooltip content="5-field cron (minute hour day month weekday). Click the link for help.">
                      <a
                        href="https://crontab.guru"
                        target="_blank"
                        rel="noopener noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        tabIndex={0}
                        className="inline-flex items-center text-blue-400 hover:text-blue-300"
                        aria-label="Open crontab.guru"
                      >
                        <InfoIcon className="w-3.5 h-3.5 cursor-help" />
                      </a>
                    </Tooltip>
                  </div>
                  <Input
                    id="sc-cron"
                    {...form.register("cron_expression")}
                    placeholder="0 2 * * *"
                    className="bg-slate-800 border-slate-700 text-white font-mono placeholder:text-slate-500"
                  />
                  {errors.cron_expression && (
                    <p className="text-sm text-red-400">
                      {errors.cron_expression.message}
                    </p>
                  )}
                </div>
              )}
            </div>

            {/* Enabled toggle */}
            <div className="flex items-center gap-2">
              <input
                id="sc-enabled"
                type="checkbox"
                {...form.register("enabled")}
                className="rounded border-input"
              />
              <Label htmlFor="sc-enabled">Enabled</Label>
            </div>

            {/* Auto-add */}
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <input
                  id="sc-auto-add"
                  type="checkbox"
                  {...form.register("auto_add")}
                  className="rounded border-input"
                />
                <Label htmlFor="sc-auto-add">
                  Automatically add discovered hosts without manual approval
                </Label>
              </div>

              {autoAdd && (
                <div className="rounded-md border border-amber-600/50 bg-amber-600/10 px-3 py-2 text-sm text-amber-300">
                  Hosts will be added without manual review. Use only on networks you trust completely.
                </div>
              )}
            </div>
          </section>

          {/* ── Top-level API error ───────────────────────────────────────── */}
          {saveMutation.error && (
            <p className="text-sm text-red-400">{saveMutation.error.message}</p>
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
              {saveMutation.isPending
                ? isEdit
                  ? "Saving…"
                  : "Creating…"
                : isEdit
                  ? "Save Changes"
                  : "Create"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
