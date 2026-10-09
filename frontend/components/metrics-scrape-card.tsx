"use client"

import { useQuery } from "@tanstack/react-query"
import { apiFetch, ApiError } from "@/lib/api"
import { Banner, CodeBlock, Copy, Dot, Facts } from "@/components/ld"
import type { MetricsStatus } from "@/lib/types"

const SECURITY_HARDENING_URL =
  "https://open-labdog.github.io/labdog/security-hardening#exposing-metrics"
const PROMETHEUS_EXAMPLES_URL =
  "https://github.com/open-labdog/labdog/tree/main/docs/examples/prometheus"

/** Builds a copy-paste Grafana Alloy `prometheus.scrape` block from the
 *  resolved scrape URL. Alloy is LabDog's house agent — the bundled
 *  alloy-install action already deploys it to managed hosts. The
 *  exposition format is standard Prometheus text, so any compatible
 *  scraper works — point it at the same URL.
 *
 *  Purely client-side — the backend only reports status, not a rendered
 *  config. Falls back to a placeholder host if scrape_url isn't a
 *  parseable absolute URL. */
function buildAlloyConfig(status: MetricsStatus): string {
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
    'prometheus.scrape "labdog" {',
    `  targets         = [{ __address__ = "${host}" }]`,
    '  job_name        = "labdog"',
    `  scheme          = "${scheme}"`,
    `  metrics_path    = "${status.path}"`,
    '  scrape_interval = "30s"',
    "",
    "  // Point this at the remote_write component you already have.",
    "  forward_to = [prometheus.remote_write.default.receiver]",
    "}",
  ].join("\n")
}

const ext = "text-ld-accent hover:underline"

/** Outbound Prometheus scrape status — the body of the "prometheus
 *  export" panel under Settings › System and on the Grafana screen. Reads
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
  const notYetAvailable = error instanceof ApiError && error.status === 404

  if (isLoading) return <div className="text-xs text-text-3">Loading…</div>
  if (error && notYetAvailable) {
    return (
      <div className="text-xs text-text-3">
        Prometheus scrape status isn&apos;t available yet — the metrics export API may not be deployed on this instance.
      </div>
    )
  }
  if (error || !data) return <div className="text-xs text-danger">Failed to load Prometheus scrape status.</div>

  return (
    <div className="flex flex-col gap-3">
      <Facts
        min={150}
        items={[
          {
            k: "status",
            v: (
              <span className="inline-flex items-center gap-1.5" style={{ color: data.enabled ? "var(--ok)" : "var(--text-3)" }}>
                <Dot tone={data.enabled ? "ok" : "idle"} />
                {data.enabled ? "enabled" : "disabled"}
              </span>
            ),
          },
          ...(data.enabled
            ? [
                {
                  k: "scrape url",
                  mono: true,
                  span: 2,
                  title: data.scrape_url,
                  v: (
                    <span className="inline-flex max-w-full items-center gap-1">
                      <span className="trunc">{data.scrape_url}</span>
                      <Copy text={data.scrape_url} className="btn btn-sm btn-ghost -my-1" />
                    </span>
                  ),
                },
                { k: "auth", v: "none" },
                { k: "cache ttl", v: `${data.cache_ttl_seconds}s`, mono: true },
              ]
            : []),
        ]}
      />

      {data.enabled ? (
        <>
          <Banner tone="warn">
            Unauthenticated — restrict at your reverse proxy. See{" "}
            <a href={SECURITY_HARDENING_URL} target="_blank" rel="noopener noreferrer" className={ext}>
              Security hardening
            </a>
            .
          </Banner>
          <CodeBlock title="config.alloy" actions={<Copy text={buildAlloyConfig(data)} />} wrap={false}>
            {buildAlloyConfig(data)}
          </CodeBlock>
        </>
      ) : (
        <div className="flex flex-col gap-2">
          <div className="text-xs text-text-2">
            Add to <span className="mono text-text">/etc/labdog/labdog.toml</span> and restart:
          </div>
          <CodeBlock title="labdog.toml" actions={<Copy text={data.toml_snippet} />} wrap={false}>
            {data.toml_snippet}
          </CodeBlock>
          <div className="flex flex-wrap items-center gap-2 text-xs text-text-2">
            <span>Or set the env var:</span>
            <span className="mono text-[11px] text-text">{data.env_snippet}</span>
            <Copy text={data.env_snippet} />
          </div>
          <div className="text-[11px] text-text-3">
            File-level on purpose: the endpoint is unauthenticated, so enabling it needs server access, not just a LabDog login.
          </div>
        </div>
      )}

      <div className="text-[11px] text-text-3">
        Dashboard + alert rules ship in{" "}
        <a href={PROMETHEUS_EXAMPLES_URL} target="_blank" rel="noopener noreferrer" className={ext}>
          docs/examples/prometheus/
        </a>
        .
      </div>
    </div>
  )
}
