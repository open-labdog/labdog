"use client"

import Link from "next/link"
import { useRouter, useSearchParams } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { shortAgo } from "@/lib/fleet"
import type { AIProvider, AIUsageSummary, GitRepository, GrafanaInstance, MetricsStatus, ProxmoxNode, SSHKey } from "@/lib/types"
import { MetricsScrapeCard } from "@/components/metrics-scrape-card"
import { Dot, PageHead, Panel, Tabs, type Tone } from "@/components/ld"

import { SettingsEditor } from "./settings-editor"
import AboutPage from "./about/client-page"

type Section = "integrations" | "ai" | "access" | "fleet" | "system"

const SECTIONS: { k: Section; label: string }[] = [
  { k: "integrations", label: "Integrations" },
  { k: "ai", label: "AI" },
  { k: "access", label: "Access" },
  { k: "fleet", label: "Fleet defaults" },
  { k: "system", label: "System" },
]

/**
 * Settings — five stable sections with room to grow. Integrations are a
 * registry: each is a card with what is connected, and the next one is a
 * data entry, not a sidebar link. Settings sits at the bottom of the rail,
 * not as a peer of the work.
 */
export default function SettingsPage() {
  const router = useRouter()
  const search = useSearchParams()
  const { user } = useAuth()
  const section = (search.get("section") as Section | null) ?? "integrations"
  const setSection = (s: string) => router.push(s === "integrations" ? "/settings" : `/settings?section=${s}`)

  return (
    <>
      <PageHead title="Settings" sub="Integrations are a registry; the rest is database-backed and takes effect immediately.">
        <Tabs tabs={SECTIONS} value={section} onChange={setSection} />
      </PageHead>
      <div className="scroll flex-1 p-3.5">
        {section === "integrations" && <Integrations />}
        {section === "ai" && (
          <div className="flex flex-col gap-3">
            <Panel title="providers" meta="who answers the assistant">
              <ProvidersRow />
            </Panel>
            <SettingsEditor categories={["ai"]} />
          </div>
        )}
        {section === "access" && <Access superuser={!!user?.is_superuser} />}
        {section === "fleet" && <SettingsEditor categories={["drift", "ssh", "ansible", "actions", "workflow", "discovery"]} includeUncategorised />}
        {section === "system" && (
          <div className="flex flex-col gap-3">
            <SettingsEditor categories={["logging"]} />
            <Panel title="prometheus export" meta="scrape LabDog itself">
              <div className="p-3">
                <MetricsScrapeCard />
              </div>
            </Panel>
            <Panel title="about" meta="build + licence">
              <div className="p-3">
                <AboutPage embedded />
              </div>
            </Panel>
          </div>
        )}
      </div>
    </>
  )
}

function Card({ name, state, status, detail, meta, href, action }: { name: string; state: Tone; status?: string; detail: string; meta: string; href: string; action: string }) {
  return (
    <Panel title={name} meta={status ?? (state === "ok" ? "connected" : state === "warn" ? "needs attention" : "not configured")}>
      <div className="flex flex-col gap-2 p-[11px]">
        <div className="flex items-center gap-2">
          <Dot tone={state} />
          <span className="mono trunc text-[11.5px]">{detail}</span>
        </div>
        <span className="text-[11.5px] text-text-3">{meta}</span>
        <div className="flex gap-1.5">
          <Link href={href} className="btn btn-sm hover:no-underline">
            {action}
          </Link>
        </div>
      </div>
    </Panel>
  )
}

function Integrations() {
  const q = { retry: false }
  const { data: nodes } = useQuery<ProxmoxNode[]>({ queryKey: ["proxmox-nodes"], queryFn: () => apiFetch<ProxmoxNode[]>("/api/proxmox/nodes"), ...q })
  const { data: grafana } = useQuery<GrafanaInstance[]>({ queryKey: ["grafana-instances"], queryFn: () => apiFetch<GrafanaInstance[]>("/api/grafana/instances"), ...q })
  const { data: repos } = useQuery<GitRepository[]>({ queryKey: ["git-repos"], queryFn: () => apiFetch<GitRepository[]>("/api/git-repos"), ...q })
  const { data: providers } = useQuery<AIProvider[]>({ queryKey: ["ai-providers"], queryFn: () => apiFetch<AIProvider[]>("/api/ai/providers"), ...q })
  const { data: metrics } = useQuery<MetricsStatus>({ queryKey: ["metrics-status"], queryFn: () => apiFetch<MetricsStatus>("/api/metrics/status"), ...q })

  const enabledProviders = (providers ?? []).filter((p) => p.enabled)
  const lastGit = repos?.map((r) => r.last_sync_at).filter(Boolean).sort().at(-1) ?? null

  return (
    <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))" }}>
      <Card
        name="Proxmox"
        state={nodes?.length ? "ok" : "idle"}
        detail={nodes?.length ? nodes.map((n) => n.name).join(", ") : "no nodes"}
        meta={nodes?.length ? `${nodes.length} node${nodes.length === 1 ? "" : "s"} · snapshots before destructive actions` : "connect a node to snapshot guests before destructive actions"}
        href="/hypervisors"
        action="Configure…"
      />
      <Card
        name="Grafana / Mimir / Loki"
        state={grafana?.length ? "ok" : "idle"}
        detail={grafana?.length ? grafana.map((g) => `${g.kind}: ${g.name}`).join(" · ") : "no instances"}
        meta={grafana?.length ? "host metrics and logs on the host page" : "register Mimir for metrics, Loki for logs"}
        href="/grafana"
        action="Configure…"
      />
      <Card
        name="Git remotes"
        state={repos?.length ? (repos.every((r) => r.last_sync_at) ? "ok" : "warn") : "idle"}
        detail={repos?.length ? repos.map((r) => r.name).join(", ") : "no repositories"}
        meta={repos?.length ? `last sync ${lastGit ? `${shortAgo(lastGit)} ago` : "never"} · GitOps + action packs` : "import desired state and action packs from a repo"}
        href="/git-repos"
        action="Configure…"
      />
      <Card
        name="AI providers"
        state={enabledProviders.length ? "ok" : "idle"}
        detail={enabledProviders.length ? enabledProviders.map((p) => `${p.name} · ${p.model}`).join(", ") : "none enabled"}
        meta={enabledProviders.length ? "budgets and autonomy under Settings · AI" : "the assistant is off until a provider is enabled"}
        href="/ai-providers"
        action="Configure…"
      />
      <Card
        name="Prometheus export"
        state={metrics?.enabled ? "ok" : "idle"}
        detail={metrics?.enabled ? metrics.scrape_url : "disabled"}
        meta={metrics?.enabled ? `cached ${metrics.cache_ttl_seconds}s · unauthenticated by design` : "opt-in; LabDog's own health as a scrape target"}
        href="/settings?section=system"
        action="Details"
      />
      <Card
        name="Webhooks"
        state="idle"
        status="inbound endpoints"
        detail="/api/webhooks/{github,gitlab,gitea,grafana-alerts}"
        meta="inbound: Git pushes trigger a GitOps sync; Grafana alerts land in Pending"
        href="/git-repos"
        action="Git remotes"
      />
    </div>
  )
}

function ProvidersRow() {
  const { data: providers } = useQuery<AIProvider[]>({ queryKey: ["ai-providers"], queryFn: () => apiFetch<AIProvider[]>("/api/ai/providers"), retry: false })
  const { data: usage } = useQuery<AIUsageSummary>({ queryKey: ["ai-usage"], queryFn: () => apiFetch<AIUsageSummary>("/api/ai/usage"), retry: false })
  const enabled = (providers ?? []).filter((p) => p.enabled)
  return (
    <div className="flex flex-wrap items-center gap-3 p-[11px]">
      <Dot tone={enabled.length ? "ok" : "idle"} />
      <span className="text-xs text-text">
        {enabled.length ? `${enabled.length} enabled: ${enabled.map((p) => `${p.name} (${p.model})`).join(", ")}` : "No provider enabled — the assistant cannot run."}
      </span>
      {usage && (
        <span className="mono num text-[11px] text-text-3">
          {usage.currency} {usage.day_spend.toFixed(2)} today{usage.day_limit ? ` of ${usage.day_limit.toFixed(2)}` : ""} · {usage.month_spend.toFixed(2)} this month
        </span>
      )}
      <Link href="/ai-providers" className="btn btn-sm ml-auto hover:no-underline">
        Manage providers…
      </Link>
    </div>
  )
}

function Access({ superuser }: { superuser: boolean }) {
  const { data: keys } = useQuery<SSHKey[]>({ queryKey: ["ssh-keys"], queryFn: () => apiFetch<SSHKey[]>("/api/ssh-keys"), retry: false })
  return (
    <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))" }}>
      <Card
        name="SSH keys"
        state={keys?.length ? "ok" : "warn"}
        detail={keys?.length ? keys.map((k) => k.name).join(", ") : "no keys"}
        meta={keys?.length ? `${keys.length} key${keys.length === 1 ? "" : "s"} — the credential every host is reached with` : "add a key before adding hosts"}
        href="/ssh-keys"
        action="Manage keys…"
      />
      <Card
        name="Users"
        state={superuser ? "ok" : "idle"}
        detail={superuser ? "you are a superuser" : "administrators only"}
        meta={superuser ? "accounts and superuser status" : "ask an administrator to manage accounts"}
        href={superuser ? "/users" : "/settings?section=access"}
        action={superuser ? "Manage users…" : "Superuser gate"}
      />
      <Panel title="your account" meta="password + sign out">
        <div className="flex flex-col gap-2 p-[11px] text-[11.5px] text-text-3">
          <span>Changing your password and signing out live behind the avatar at the foot of the rail, where they cannot be mistaken for navigation.</span>
        </div>
      </Panel>
    </div>
  )
}
