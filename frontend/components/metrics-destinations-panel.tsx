"use client"

import { useState } from "react"
import Link from "next/link"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { apiFetch } from "@/lib/api"
import { Field, Panel } from "@/components/ld"
import type { DestinationAssignment, EffectiveDestination, GrafanaInstance, GrafanaKind, HostDestinations } from "@/lib/types"

const KINDS: { kind: GrafanaKind; label: string; field: keyof DestinationAssignment }[] = [
  { kind: "mimir", label: "metrics (Mimir)", field: "mimir_instance_id" },
  { kind: "loki", label: "logs (Loki)", field: "loki_instance_id" },
]

function describe(eff: EffectiveDestination | undefined): string {
  if (!eff || eff.instance_id == null) return "nothing: no default is set"
  if (eff.source === "group") return `${eff.instance_name}, from group ${eff.group_name}`
  if (eff.source === "default") return `${eff.instance_name}, the default`
  return eff.instance_name ?? ""
}

/**
 * Where a host's or a group's Alloy ships metrics and logs. A host's own
 * choice wins, then its highest-priority group's, then the default
 * instance. Saving changes what unbork queries at once; the host itself
 * picks the change up the next time the metrics agent action runs on it.
 */
export function MetricsDestinationsPanel({ scope, id }: { scope: "host" | "group"; id: number }) {
  const qc = useQueryClient()
  const path = `/api/grafana/destinations/${scope === "host" ? "hosts" : "groups"}/${id}`
  const { data: instances } = useQuery<GrafanaInstance[]>({
    queryKey: ["grafana-instances"],
    queryFn: () => apiFetch<GrafanaInstance[]>("/api/grafana/instances"),
    staleTime: 30_000,
  })
  const { data } = useQuery<HostDestinations | DestinationAssignment>({
    queryKey: ["metrics-destinations", scope, id],
    queryFn: () => apiFetch(path),
  })
  const assigned: DestinationAssignment | undefined = data
    ? "assigned" in data
      ? data.assigned
      : data
    : undefined
  const effective = data && "effective" in data ? data.effective : undefined

  // Unsaved edits; null means "showing what is stored".
  const [edits, setEdits] = useState<DestinationAssignment | null>(null)
  const draft = edits ?? assigned ?? null
  const [saving, setSaving] = useState(false)

  const dirty = !!edits && !!assigned && KINDS.some(({ field }) => edits[field] !== assigned[field])

  async function save() {
    if (!draft) return
    setSaving(true)
    try {
      await apiFetch(path, { method: "PUT", json: draft })
      setEdits(null)
      await qc.invalidateQueries({ queryKey: ["metrics-destinations"] })
      await qc.invalidateQueries({ queryKey: ["host-metrics"] })
      toast.success("Destinations saved. Run the metrics agent action to apply them on the host.")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not save destinations")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Panel
      title="metrics destinations"
      meta={scope === "host" ? "host, then group, then default" : "for members that don't set their own"}
      footer={
        <button type="button" className="btn btn-sm btn-primary" disabled={!dirty || saving} onClick={save}>
          {saving ? "Saving…" : "Save"}
        </button>
      }
      pad={11}
    >
      <div className="flex flex-col gap-[11px]">
        {KINDS.map(({ kind, label, field }) => {
          const options = (instances ?? []).filter((i) => i.kind === kind)
          const inherited = effective?.[kind] && effective[kind].source !== "host" ? effective[kind] : undefined
          const inherit =
            scope === "group"
              ? "No choice (a lower group, or the default)"
              : inherited
                ? `Inherit (${describe(inherited)})`
                : "Inherit from a group, or the default"
          return (
            <Field key={kind} label={label} htmlFor={`dest-${scope}-${kind}`} hint={scope === "host" && effective ? `Ships to ${describe(effective[kind])}.` : undefined}>
              {options.length === 0 ? (
                <span className="text-[11.5px] text-text-3">
                  None registered.{" "}
                  <Link href="/grafana" className="underline">
                    Add one
                  </Link>
                  .
                </span>
              ) : (
                <select
                  id={`dest-${scope}-${kind}`}
                  className="inp mono"
                  value={draft?.[field] == null ? "" : String(draft[field])}
                  onChange={(e) => setEdits({ ...(draft ?? { mimir_instance_id: null, loki_instance_id: null }), [field]: e.target.value === "" ? null : Number(e.target.value) })}
                >
                  <option value="">{inherit}</option>
                  {options.map((i) => (
                    <option key={i.id} value={String(i.id)}>
                      {i.name}
                      {i.org_id ? ` (tenant ${i.org_id})` : ""} — {i.url}
                    </option>
                  ))}
                </select>
              )}
            </Field>
          )
        })}
      </div>
    </Panel>
  )
}
