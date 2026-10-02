"use client"

import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { shortAgo } from "@/lib/fleet"
import { showError, showSuccess } from "@/lib/toast"
import type { EmailSettings, NotificationDelivery, NotificationEventType, SMTPTLSMode } from "@/lib/types"
import { Banner, Field, PageHead, Panel, Table, Tag, type Tone } from "@/components/ld"
import { SettingsEditor } from "../settings/settings-editor"

const CRUMBS = [
  { label: "settings", href: "/settings" },
  { label: "integrations", href: "/settings" },
]

/** The port each mode is almost always on, so changing the mode can fix
 *  the port too — but only while the port is still one of these, never
 *  over a number someone typed. */
const DEFAULT_PORT: Record<SMTPTLSMode, number> = { starttls: 587, tls: 465, none: 25 }

/** The two fields of a settings row this page reads. */
type SettingRow = { key: string; value: string }

const STATUS_TONE: Record<NotificationDelivery["status"], Tone> = { pending: "hold", sent: "ok", failed: "danger" }

interface FormState {
  enabled: boolean
  host: string
  port: string
  tls_mode: SMTPTLSMode
  username: string
  password: string
  clear_password: boolean
  from_address: string
}

function toForm(s: EmailSettings): FormState {
  return {
    enabled: s.enabled,
    host: s.host,
    port: String(s.port),
    tls_mode: s.tls_mode,
    username: s.username ?? "",
    password: "",
    clear_password: false,
    from_address: s.from_address,
  }
}

function payload(f: FormState) {
  return {
    enabled: f.enabled,
    host: f.host.trim(),
    port: Number(f.port) || DEFAULT_PORT[f.tls_mode],
    tls_mode: f.tls_mode,
    username: f.username.trim() || null,
    // Blank keeps the stored password; only a typed one replaces it.
    password: f.password || null,
    clear_password: f.clear_password,
    from_address: f.from_address.trim(),
  }
}

/**
 * Email — the mail server, LabDog's own address for links, what the
 * signed-in user wants to hear about, and what was actually sent. One
 * page, because "why didn't I get an email?" can be answered by any of
 * the four and nobody should have to visit four places to find out which.
 */
export default function NotificationsPage() {
  const queryClient = useQueryClient()
  const { data: email, isLoading } = useQuery<EmailSettings>({
    queryKey: ["notification-email"],
    queryFn: () => apiFetch<EmailSettings>("/api/notifications/email"),
  })
  // Shared with the settings editor below, so saving the public URL there
  // updates the readiness banner here without a second request.
  const { data: settings } = useQuery<SettingRow[]>({ queryKey: ["settings"], queryFn: () => apiFetch<SettingRow[]>("/api/settings") })
  const publicUrl = settings?.find((s) => s.key === "notifications.public_url")?.value ?? email?.public_url ?? ""

  const missing: string[] = []
  if (email && !email.enabled) missing.push("email is switched off")
  if (email && (!email.host || !email.from_address)) missing.push("no mail server is set")
  if (!publicUrl) missing.push("LabDog's address (notifications.public_url) is not set, and every notification links back to it")

  return (
    <>
      <PageHead crumbs={CRUMBS} title="Email" sub="Alerts, changes waiting for a decision, and automatic fixes — sent to whoever asks for them." />
      <div className="scroll flex flex-1 flex-col gap-3 p-3.5">
        {email && missing.length > 0 && (
          <Banner tone="warn">
            Nothing is being sent: {missing.join("; ")}.
          </Banner>
        )}
        <div className="grid items-start gap-3" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))" }}>
          {isLoading || !email ? (
            <Panel title="mail server">
              <span className="p-3 text-xs text-text-3">Loading…</span>
            </Panel>
          ) : (
            <MailServer settings={email} onSaved={(s) => queryClient.setQueryData(["notification-email"], s)} />
          )}
          <div className="flex flex-col gap-3">
            <SettingsEditor categories={["notifications"]} />
            <Subscriptions />
          </div>
        </div>
        <Deliveries />
      </div>
    </>
  )
}

function MailServer({ settings, onSaved }: { settings: EmailSettings; onSaved: (s: EmailSettings) => void }) {
  const { user } = useAuth()
  const [form, setForm] = useState<FormState>(() => toForm(settings))
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testTo, setTestTo] = useState("")
  const [result, setResult] = useState<{ success: boolean; message: string } | null>(null)

  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => setForm((p) => ({ ...p, [k]: v }))

  function setMode(mode: SMTPTLSMode) {
    setForm((p) => ({
      ...p,
      tls_mode: mode,
      port: Object.values(DEFAULT_PORT).map(String).includes(p.port) ? String(DEFAULT_PORT[mode]) : p.port,
    }))
  }

  async function save() {
    setSaving(true)
    try {
      const saved = await apiFetch<EmailSettings>("/api/notifications/email", { method: "PUT", json: payload(form) })
      onSaved(saved)
      setForm(toForm(saved))
      showSuccess("Mail server saved")
    } catch (err) {
      showError(err instanceof Error ? err.message : "Could not save")
    } finally {
      setSaving(false)
    }
  }

  async function test() {
    setTesting(true)
    setResult(null)
    try {
      setResult(
        await apiFetch<{ success: boolean; message: string }>("/api/notifications/email/test", {
          method: "POST",
          json: { ...payload(form), to: testTo.trim() || null },
        }),
      )
    } catch (err) {
      setResult({ success: false, message: err instanceof Error ? err.message : "Test failed" })
    } finally {
      setTesting(false)
    }
  }

  return (
    <Panel
      title="mail server"
      meta={settings.updated_at ? `saved ${shortAgo(settings.updated_at)} ago` : "not configured"}
      footer={
        <div className="flex flex-wrap items-center gap-2">
          <input className="inp mono min-w-0 flex-1" style={{ maxWidth: 240 }} placeholder={user?.email ?? "send the test to…"} value={testTo} onChange={(e) => setTestTo(e.target.value)} aria-label="test recipient" />
          <button type="button" className="btn btn-sm" disabled={testing || !form.host || !form.from_address} onClick={test}>
            {testing ? "Sending…" : "Send test"}
          </button>
          <button type="button" className="btn btn-sm btn-primary ml-auto" disabled={saving} onClick={save}>
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      }
    >
      <div className="flex flex-col gap-[11px] p-[11px]">
        <label className="flex items-center gap-2 text-xs text-text">
          <input type="checkbox" checked={form.enabled} onChange={(e) => set("enabled", e.target.checked)} />
          send email
          <span className="text-text-3">— while off, nothing is queued, so switching on later sends no backlog</span>
        </label>
        <div className="grid gap-[11px]" style={{ gridTemplateColumns: "1fr 90px" }}>
          <Field label="smtp server" htmlFor="m-host">
            <input id="m-host" className="inp mono" placeholder="mail.example.com" value={form.host} onChange={(e) => set("host", e.target.value)} />
          </Field>
          <Field label="port" htmlFor="m-port">
            <input id="m-port" className="inp mono num" inputMode="numeric" value={form.port} onChange={(e) => set("port", e.target.value)} />
          </Field>
        </div>
        <Field label="security" htmlFor="m-tls">
          <select id="m-tls" className="inp" value={form.tls_mode} onChange={(e) => setMode(e.target.value as SMTPTLSMode)}>
            <option value="starttls">STARTTLS (usually port 587)</option>
            <option value="tls">TLS from the start (usually port 465)</option>
            <option value="none">None — a relay on this machine or the LAN only</option>
          </select>
        </Field>
        <div className="grid gap-[11px]" style={{ gridTemplateColumns: "1fr 1fr" }}>
          <Field label="username" htmlFor="m-user" hint="blank for a relay that needs no login">
            <input id="m-user" className="inp mono" autoComplete="off" value={form.username} onChange={(e) => set("username", e.target.value)} />
          </Field>
          <Field label="password" htmlFor="m-pass" hint={settings.password_set ? "blank keeps the current one" : undefined}>
            <input
              id="m-pass"
              type="password"
              className="inp mono"
              autoComplete="new-password"
              placeholder={settings.password_set && !form.clear_password ? "•••••••• (stored)" : ""}
              value={form.password}
              disabled={form.clear_password}
              onChange={(e) => set("password", e.target.value)}
            />
            {settings.password_set && (
              <button type="button" className="self-start text-[11px] text-text-3 underline" onClick={() => setForm((p) => ({ ...p, password: "", clear_password: !p.clear_password }))}>
                {form.clear_password ? "keep the stored password" : "remove the stored password"}
              </button>
            )}
          </Field>
        </div>
        <Field label="from address" htmlFor="m-from" hint="a plain address — the server decides whether it may send as it">
          <input id="m-from" className="inp mono" placeholder="labdog@example.com" value={form.from_address} onChange={(e) => set("from_address", e.target.value)} />
        </Field>
        {result && <Banner tone={result.success ? "ok" : "danger"}>{result.message}</Banner>}
      </div>
    </Panel>
  )
}

function Subscriptions() {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const { data: events } = useQuery<NotificationEventType[]>({
    queryKey: ["notification-events"],
    queryFn: () => apiFetch<NotificationEventType[]>("/api/notifications/events"),
  })
  const { data: mine } = useQuery<{ event_types: string[] }>({
    queryKey: ["notification-subscriptions"],
    queryFn: () => apiFetch<{ event_types: string[] }>("/api/notifications/subscriptions"),
  })
  const [chosen, setChosen] = useState<Set<string> | null>(null)
  const [saving, setSaving] = useState(false)

  // The server's list until the first click; the user's own after that.
  const current = chosen ?? new Set(mine?.event_types ?? [])
  const dirty = !!mine && (current.size !== mine.event_types.length || mine.event_types.some((k) => !current.has(k)))

  function toggle(key: string) {
    const next = new Set(current)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    setChosen(next)
  }

  async function save() {
    setSaving(true)
    try {
      const saved = await apiFetch<{ event_types: string[] }>("/api/notifications/subscriptions", {
        method: "PUT",
        json: { event_types: [...current] },
      })
      queryClient.setQueryData(["notification-subscriptions"], saved)
      setChosen(new Set(saved.event_types))
      showSuccess("Notifications saved")
    } catch (err) {
      showError(err instanceof Error ? err.message : "Could not save")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Panel
      title="your notifications"
      meta={user?.email}
      footer={
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-text-3">Each is opt-in, and only for you. Everything due in the same minute arrives as one email.</span>
          <button type="button" className="btn btn-sm btn-primary ml-auto" disabled={!dirty || saving} onClick={save}>
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      }
    >
      <div id="yours" className="flex flex-col gap-2 p-[11px]">
        {(events ?? []).map((e) => (
          <label key={e.key} className="flex items-start gap-2 text-xs">
            <input type="checkbox" className="mt-0.5" checked={current.has(e.key)} onChange={() => toggle(e.key)} />
            <span className="flex flex-col">
              <span className="text-text">{e.label}</span>
              <span className="text-[11px] text-text-3">{e.description}</span>
            </span>
          </label>
        ))}
      </div>
    </Panel>
  )
}

function Deliveries() {
  const { data: rows, isLoading } = useQuery<NotificationDelivery[]>({
    queryKey: ["notification-deliveries"],
    queryFn: () => apiFetch<NotificationDelivery[]>("/api/notifications/deliveries?limit=100"),
    refetchInterval: 30_000,
  })
  return (
    <Panel title="recent notifications" meta="what was sent, to whom, and what the server said">
      <Table<NotificationDelivery>
        cols={[
          { k: "when", label: "queued", w: "84px", sortable: false, cell: (r) => <span className="mono num text-[11px] text-text-3">{shortAgo(r.created_at)} ago</span> },
          { k: "status", label: "status", w: "84px", sortable: false, cell: (r) => <Tag tone={STATUS_TONE[r.status]}>{r.status}</Tag> },
          { k: "to", label: "to", w: "minmax(140px,0.8fr)", sortable: false, cell: (r) => <span className="mono trunc text-[11.5px]">{r.recipient}</span> },
          { k: "subject", label: "subject", w: "minmax(180px,1.6fr)", sortable: false, cell: (r) => <span className="trunc text-text-2">{r.subject}</span> },
          {
            k: "detail", label: "detail", w: "minmax(160px,1.2fr)", sortable: false,
            cell: (r) =>
              r.last_error ? (
                <span className="trunc text-[11px] text-danger" title={r.last_error}>
                  {r.status === "pending" ? `attempt ${r.attempts} failed, retrying — ` : ""}
                  {r.last_error}
                </span>
              ) : r.sent_at ? (
                <span className="text-[11px] text-text-3">sent {shortAgo(r.sent_at)} ago</span>
              ) : (
                <span className="text-[11px] text-text-faint">waiting for the next send (once a minute)</span>
              ),
          },
        ]}
        rows={rows ?? []}
        keyOf={(r) => r.id}
        loading={isLoading}
        empty={<span>Nothing has been queued. Subscribe to something above, and make sure the banner at the top of the page is gone.</span>}
      />
    </Panel>
  )
}
