"use client"

import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { type VersionInfo } from "@/lib/types"
import { CodeBlock, Copy, Facts } from "@/components/ld"

function formatBuildDate(iso: string | null): string {
  if (!iso) return "—"
  return new Intl.DateTimeFormat("en-GB", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
    timeZoneName: "short",
  }).format(new Date(iso))
}

function buildSupportLine(info: VersionInfo): string {
  const sha = info.commit_sha_short ?? "dev"
  const date = info.build_date ? info.build_date.slice(0, 10) : "dev"
  return `LabDog ${info.version} (${sha}, ${date})`
}

/**
 * Build and licence facts for this instance — the body of the "about"
 * panel under Settings › System. `/settings/about` redirects there.
 */
export default function AboutPage() {
  const { data, isLoading, error } = useQuery<VersionInfo>({
    queryKey: ["version"],
    queryFn: () => apiFetch<VersionInfo>("/api/version"),
    staleTime: Infinity,
    retry: 1,
  })

  if (isLoading) return <div className="text-xs text-text-3">Loading…</div>
  if (error || !data) return <div className="text-xs text-danger">Failed to load version information.</div>

  const supportLine = buildSupportLine(data)
  const ext = "text-ld-accent hover:underline"

  return (
    <div className="flex flex-col gap-3">
      <Facts
        min={150}
        items={[
          { k: "version", v: data.version, mono: true },
          {
            k: "commit",
            mono: true,
            v:
              data.commit_sha && data.commit_sha_short ? (
                <a href={`${data.repo_url}/commit/${data.commit_sha}`} target="_blank" rel="noopener noreferrer" className={ext}>
                  {data.commit_sha_short}
                </a>
              ) : (
                <span className="italic text-text-faint">dev build</span>
              ),
          },
          { k: "built", v: formatBuildDate(data.build_date), mono: true },
          {
            k: "license",
            v: (
              <a href={`${data.repo_url}/blob/main/LICENSE`} target="_blank" rel="noopener noreferrer" className={ext}>
                {data.license}
              </a>
            ),
          },
          {
            k: "source",
            mono: true,
            span: 2,
            v: (
              <a href={data.repo_url} target="_blank" rel="noopener noreferrer" className={ext}>
                {data.repo_url}
              </a>
            ),
          },
        ]}
      />
      <CodeBlock title="support line" tag={<span className="tt">for bug reports</span>} actions={<Copy text={supportLine} />} maxH={80}>
        {supportLine}
      </CodeBlock>
    </div>
  )
}
