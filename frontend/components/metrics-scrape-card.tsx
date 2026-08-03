"use client"

import { useQuery } from "@tanstack/react-query"
import { AlertTriangleIcon } from "lucide-react"
import { apiFetch, ApiError } from "@/lib/api"
import { useDelayedLoading } from "@/lib/utils"
import { CardSkeleton } from "@/components/ui/skeleton"
import { CopyButton } from "@/components/ui/copy-button"
import type { MetricsStatus } from "@/lib/types"

const SECURITY_HARDENING_URL =
  "https://open-labdog.github.io/labdog/security-hardening#exposing-metrics"
const PROMETHEUS_EXAMPLES_URL =
  "https://github.com/open-labdog/labdog/tree/main/docs/examples/prometheus"

/** Builds a copy-paste `prometheus.yml` scrape_configs snippet from the
 *  resolved scrape URL. Purely client-side — the backend only reports
 *  status, not a rendered scrape config. Falls back to a placeholder host
 *  if scrape_url isn't a parseable absolute URL. */
function buildPrometheusYml(status: MetricsStatus): string {
  let scheme = "https"
  let host = "<labdog-host:port>"
  try {
    const u = new URL(status.scrape_url)
    scheme = u.protocol.replace(":", "") || scheme
    host = u.host || host
  } catch {
    // scrape_url wasn't absolute — keep the placeholder host.
  }
  return [
    "scrape_configs:",
    "  - job_name: labdog",
    `    scheme: ${scheme}`,
    `    metrics_path: ${status.path}`,
    "    static_configs:",
    `      - targets: ["${host}"]`,
  ].join("\n")
}

function StatusDot({ on }: { on: boolean }) {
  return (
    <span
      className={`inline-block h-2 w-2 shrink-0 rounded-full ${on ? "bg-green-500" : "bg-slate-600"}`}
    />
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-8 py-2.5 border-b border-slate-700 last:border-0">
      <span className="text-sm text-slate-400 shrink-0 w-24">{label}</span>
      <span className="text-sm text-slate-200 text-right flex-1 flex items-center justify-end gap-2 min-w-0">
        {children}
      </span>
    </div>
  )
}

function CodeBlock({ title, code }: { title: string; code: string }) {
  return (
    <div className="rounded-md border border-slate-700 bg-slate-950 overflow-hidden">
      <div className="flex items-center justify-between border-b border-slate-700 bg-slate-900/60 px-3 py-1.5">
        <span className="text-xs font-medium text-slate-400">{title}</span>
        <CopyButton text={code} />
      </div>
      <pre className="overflow-x-auto px-3 py-2 text-xs font-mono text-slate-300 whitespace-pre">
        {code}
      </pre>
    </div>
  )
}

/** Outbound Prometheus scrape card for the Grafana integration page. Reads
 *  GET /api/metrics/status (authenticated, unlike the /metrics endpoint it
 *  describes). Degrades quietly — not with a red error — when the endpoint
 *  404s, since that just means the metrics-export backend isn't deployed
 *  on this instance yet rather than a real failure. */
export function MetricsScrapeCard() {
  const { data, isLoading, error } = useQuery<MetricsStatus>({
    queryKey: ["metrics-status"],
    queryFn: () => apiFetch<MetricsStatus>("/api/metrics/status"),
    staleTime: 60_000,
    retry: 1,
  })
  const showLoading = useDelayedLoading(isLoading)
  const notYetAvailable = error instanceof ApiError && error.status === 404

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900 p-4">
      {showLoading && <CardSkeleton lines={4} />}

      {!isLoading && error && notYetAvailable && (
        <p className="text-sm text-slate-400 py-2">
          Prometheus scrape status isn&apos;t available yet — the metrics export API may not be
          deployed on this instance.
        </p>
      )}

      {!isLoading && error && !notYetAvailable && (
        <p className="text-sm text-red-400 py-2">Failed to load Prometheus scrape status.</p>
      )}

      {!isLoading && !error && data && (
        <div className="space-y-4">
          <div className="divide-y divide-slate-700">
            <Row label="Status">
              <StatusDot on={data.enabled} />
              <span className={data.enabled ? "text-green-400" : "text-slate-400"}>
                {data.enabled ? "Enabled" : "Disabled"}
              </span>
            </Row>

            {data.enabled && (
              <>
                <Row label="Scrape URL">
                  <span className="font-mono text-xs text-slate-300 truncate" title={data.scrape_url}>
                    {data.scrape_url}
                  </span>
                  <CopyButton text={data.scrape_url} />
                </Row>
                <Row label="Auth">
                  <span className="text-slate-300">None</span>
                </Row>
                <Row label="Cache TTL">
                  <span className="text-slate-300">{data.cache_ttl_seconds}s</span>
                </Row>
              </>
            )}
          </div>

          {data.enabled ? (
            <>
              <p className="flex items-start gap-1.5 text-xs text-amber-400">
                <AlertTriangleIcon className="h-3.5 w-3.5 shrink-0 mt-0.5" />
                <span>
                  Unauthenticated — restrict at your reverse proxy. See{" "}
                  <a
                    href={SECURITY_HARDENING_URL}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-blue-400 hover:underline"
                  >
                    Security hardening
                  </a>
                  .
                </span>
              </p>

              <CodeBlock title="prometheus.yml" code={buildPrometheusYml(data)} />
            </>
          ) : (
            <div className="space-y-3">
              <p className="text-sm text-slate-400">
                Add to <span className="font-mono text-slate-300">/etc/labdog/labdog.toml</span> and
                restart:
              </p>
              <CodeBlock title="labdog.toml" code={data.toml_snippet} />
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm text-slate-400">Or set the env var:</span>
                <code className="font-mono text-xs text-slate-300">{data.env_snippet}</code>
                <CopyButton text={data.env_snippet} />
              </div>
              <p className="text-xs text-slate-500">
                File-level on purpose: the endpoint is unauthenticated, so enabling it needs server
                access, not just a LabDog login.
              </p>
            </div>
          )}

          <p className="text-xs text-slate-500">
            Dashboard + alert rules ship in{" "}
            <a
              href={PROMETHEUS_EXAMPLES_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-400 hover:underline"
            >
              docs/examples/prometheus/
            </a>
            .
          </p>
        </div>
      )}
    </div>
  )
}
