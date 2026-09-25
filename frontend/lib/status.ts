import type { StatusDef, Tone } from "@/lib/fleet"

/**
 * Status vocabularies for everything that is not a host's sync status
 * (that one lives in lib/fleet.ts). Each map says what word a backend
 * value gets and what tone it is painted in, so an enum reads the same
 * in a table cell, a page head and a modal. Chips render inline:
 *
 *   <Tag tone={def(ITEM_STATE, s.state).tone}>{def(ITEM_STATE, s.state).label}</Tag>
 *
 * Tones share one chroma by design — nothing shouts louder than its
 * severity — so a map only has to pick the right tone, never a colour.
 */

export function def(map: Record<string, StatusDef>, key: string | null | undefined, fallback?: StatusDef): StatusDef {
  return map[key ?? ""] ?? fallback ?? { label: key ?? "unknown", tone: "idle" }
}

/** Action runs (`/api/actions/runs`). `pending` is claim-or-defer: another
 *  operation holds the host, and the run waits for it — worth a different
 *  word from `queued` (waiting for a worker) so an operator can tell at a
 *  glance that something on the host is blocking them. */
export const RUN_STATUS: Record<string, StatusDef> = {
  queued: { label: "queued", tone: "idle" },
  pending: { label: "host busy", tone: "warn" },
  running: { label: "running", tone: "sync" },
  succeeded: { label: "succeeded", tone: "ok" },
  completed: { label: "succeeded", tone: "ok" },
  failed: { label: "failed", tone: "danger" },
  partial: { label: "partial", tone: "warn" },
  cancelled: { label: "cancelled", tone: "idle" },
  skipped: { label: "skipped", tone: "idle" },
}

/** Sync jobs (`/api/sync/jobs`) and the activity stream's five words. */
export const JOB_STATUS: Record<string, StatusDef> = {
  pending: { label: "queued", tone: "idle" },
  queued: { label: "queued", tone: "idle" },
  running: { label: "running", tone: "sync" },
  success: { label: "ok", tone: "ok" },
  ok: { label: "ok", tone: "ok" },
  failed: { label: "failed", tone: "danger" },
  cancelled: { label: "cancelled", tone: "idle" },
}

export const GITOPS_STATUS: Record<string, StatusDef> = {
  synced: { label: "synced", tone: "ok" },
  error: { label: "error", tone: "danger" },
  importing: { label: "importing", tone: "sync" },
  disconnected: { label: "disconnected", tone: "idle" },
}

/** present / absent in the desired state — cron jobs, users, groups,
 *  repos, CA certs, hosts-file entries. */
export const ITEM_STATE: Record<string, StatusDef> = {
  present: { label: "present", tone: "ok" },
  absent: { label: "absent", tone: "del" },
}

/** Packages add `latest` on top of present / absent. */
export const PACKAGE_STATE: Record<string, StatusDef> = {
  ...ITEM_STATE,
  latest: { label: "latest", tone: "sync" },
}

/** systemd active-state. */
export const SYSTEMD_STATE: Record<string, StatusDef> = {
  active: { label: "active", tone: "ok" },
  running: { label: "running", tone: "ok" },
  failed: { label: "failed", tone: "danger" },
  activating: { label: "activating", tone: "warn" },
  deactivating: { label: "deactivating", tone: "warn" },
  inactive: { label: "inactive", tone: "idle" },
  stopped: { label: "stopped", tone: "idle" },
}

export function enabledDef(enabled: boolean): StatusDef {
  return enabled ? { label: "enabled", tone: "ok" } : { label: "disabled", tone: "idle" }
}

export const FIREWALL_ACTION: Record<string, StatusDef> = {
  allow: { label: "allow", tone: "ok" },
  deny: { label: "deny", tone: "danger" },
  reject: { label: "reject", tone: "warn" },
  drop: { label: "drop", tone: "danger" },
}

/** A backend name is a plain mono chip; only `unknown` carries a tone. */
export function firewallBackendDef(backend: string | null | undefined): StatusDef | null {
  if (!backend || backend === "unknown") return { label: "no firewall", tone: "warn" }
  return null
}

export const AUDIT_ACTION: Record<string, StatusDef> = {
  create: { label: "create", tone: "add" },
  update: { label: "update", tone: "sync" },
  delete: { label: "delete", tone: "del" },
  login: { label: "login", tone: "idle" },
  logout: { label: "logout", tone: "idle" },
  session_start: { label: "session", tone: "hold" },
  ssh_session: { label: "ssh", tone: "hold" },
}

export const ALERT_STATUS: Record<string, StatusDef> = {
  firing: { label: "firing", tone: "danger" },
  resolved: { label: "resolved", tone: "idle" },
}

export const ALERT_SEVERITY: Record<string, StatusDef> = {
  critical: { label: "critical", tone: "danger" },
  error: { label: "error", tone: "danger" },
  warning: { label: "warning", tone: "warn" },
  info: { label: "info", tone: "sync" },
}

/** What the assistant's tool call would do to a host. */
export const AI_CLASSIFICATION: Record<string, StatusDef> = {
  read_only: { label: "read", tone: "ok" },
  mutating: { label: "write", tone: "warn" },
  denied: { label: "blocked", tone: "danger" },
  privileged: { label: "privileged", tone: "danger" },
  unknown: { label: "unknown", tone: "idle" },
}

export const AI_SESSION_STATUS: Record<string, StatusDef> = {
  queued: { label: "queued", tone: "idle" },
  running: { label: "running", tone: "sync" },
  waiting_approval: { label: "waiting for you", tone: "hold" },
  succeeded: { label: "succeeded", tone: "ok" },
  failed: { label: "failed", tone: "danger" },
  cancelled: { label: "cancelled", tone: "idle" },
}

export const AI_APPROVAL: Record<string, StatusDef> = {
  pending: { label: "pending", tone: "hold" },
  approved: { label: "approved", tone: "ok" },
  rejected: { label: "denied", tone: "danger" },
  expired: { label: "expired", tone: "idle" },
}

/** The three autonomy levels, in the words the user guide and the
 *  settings use — the design's read / propose / act would be a second
 *  vocabulary for the same three API values. */
export const AI_AUTONOMY: Record<string, StatusDef> = {
  read_only: { label: "read-only", tone: "idle" },
  approval: { label: "approval required", tone: "hold" },
  full_auto: { label: "full auto", tone: "warn" },
}

export type { StatusDef, Tone }
