"use client"

import { useRouter, useSearchParams } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import type { PendingSummary, ScanConfig } from "@/lib/types"
import { Banner, PageHead, Tabs, Tag } from "@/components/ld"

import PendingApprovalPage from "@/app/(dashboard)/hosts/pending/client-page"
import PendingReviewClientPage from "@/app/(dashboard)/hosts/discovery/[id]/pending/client-page"
import ScansPage from "@/app/(dashboard)/hosts/discovery/client-page"
import DiscoverHostsPage from "@/app/(dashboard)/hosts/discover/client-page"

type Tab = "pending" | "schedules" | "scan"

/**
 * Discovery — one route, three tabs. "Discover" vs "Discovery" was a
 * coin-flip for the user, and the approval queue lived in three places;
 * here the scan form, the scan schedules and the pending queue are tabs of
 * one screen. Discovery is read-only: it probes, fingerprints and queues.
 * Nothing is managed until you approve it.
 */
export default function DiscoveryPage() {
  const router = useRouter()
  const search = useSearchParams()
  const tab = (search.get("tab") as Tab | null) ?? "pending"
  const scanParam = search.get("scan")
  const scanId = scanParam ? Number(scanParam) : null

  const { data: pending } = useQuery<PendingSummary>({
    queryKey: ["scans", "pending-summary"],
    queryFn: () => apiFetch<PendingSummary>("/api/scans/pending-summary"),
    refetchInterval: 30_000,
  })
  const { data: scans } = useQuery<ScanConfig[]>({ queryKey: ["scan-configs"], queryFn: () => apiFetch<ScanConfig[]>("/api/scans") })
  const total = pending?.total ?? 0

  const setTab = (t: string) => router.push(t === "pending" ? "/discovery" : `/discovery?tab=${t}`)

  return (
    <>
      <PageHead
        crumbs={[{ label: "fleet" }]}
        title={
          <>
            Discovery {total > 0 && <Tag tone="hold">{total} pending approval</Tag>}
          </>
        }
        sub={
          scans && scans.length > 0
            ? `${scans.length} scan schedule${scans.length === 1 ? "" : "s"} · ${scans.filter((s) => s.enabled).length} enabled · last run ${scans.map((s) => s.last_run_at).filter(Boolean).sort().at(-1) ? new Date(scans.map((s) => s.last_run_at).filter(Boolean).sort().at(-1)!).toLocaleString() : "never"}`
            : "Find hosts on the network and queue them for approval. Nothing joins the fleet until you say so."
        }
        actions={
          <>
            <button type="button" className="btn btn-sm" onClick={() => setTab("scan")}>
              New scan
            </button>
            <button type="button" className="btn btn-sm btn-primary" disabled={total === 0} onClick={() => setTab("pending")}>
              Review {total || ""}
            </button>
          </>
        }
      >
        <Tabs
          tabs={[
            { k: "pending", label: "Pending approval" },
            { k: "schedules", label: "Scan schedules" },
            { k: "scan", label: "Scan now" },
          ]}
          value={tab}
          onChange={setTab}
          counts={{ pending: total, schedules: scans?.length }}
        />
      </PageHead>

      {tab === "pending" && (
        <Banner tone="hold" flush>
          Nothing is managed until you approve it. Approving assigns groups — which is what decides the config a host receives on its first sync.
        </Banner>
      )}

      <div className="scroll flex-1 p-3.5">
        {tab === "pending" && (scanId !== null && !Number.isNaN(scanId) ? <PendingReviewClientPage embedded scanId={scanId} /> : <PendingApprovalPage embedded />)}
        {tab === "schedules" && <ScansPage embedded />}
        {tab === "scan" && <DiscoverHostsPage embedded />}
      </div>
    </>
  )
}
