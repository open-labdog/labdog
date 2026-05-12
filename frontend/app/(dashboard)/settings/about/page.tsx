"use client"

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { CopyIcon, CheckIcon } from "lucide-react"
import { apiFetch } from "@/lib/api"
import { type VersionInfo } from "@/lib/types"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"

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

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)

  function handleCopy() {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }).catch(() => {
      // clipboard API unavailable (non-HTTPS or permissions denied) — silently ignore
    })
  }

  return (
    <Button
      variant="ghost"
      size="icon-xs"
      onClick={handleCopy}
      aria-label="Copy to clipboard"
      className="shrink-0"
    >
      {copied ? (
        <CheckIcon className="w-3 h-3 text-green-400" />
      ) : (
        <CopyIcon className="w-3 h-3" />
      )}
    </Button>
  )
}

function InfoRow({
  label,
  children,
}: {
  label: string
  children: React.ReactNode
}) {
  return (
    <div className="flex items-start justify-between gap-8 py-3 border-b border-slate-700 last:border-0">
      <span className="text-sm text-slate-400 shrink-0 w-28">{label}</span>
      <span className="text-sm text-slate-200 text-right flex items-center gap-2">
        {children}
      </span>
    </div>
  )
}

export default function AboutPage() {
  const { data, isLoading, error } = useQuery<VersionInfo>({
    queryKey: ["version"],
    queryFn: () => apiFetch<VersionInfo>("/api/version"),
    staleTime: Infinity,
    retry: 1,
  })

  const supportLine = data ? buildSupportLine(data) : null

  return (
    <div className="space-y-6">
      <Breadcrumb
        items={[
          { label: "Settings", href: "/settings" },
          { label: "About" },
        ]}
      />

      <div>
        <h1 className="text-2xl font-bold text-white">
          {data ? `LabDog v${data.version}` : "About LabDog"}
        </h1>
        <p className="text-slate-400 text-sm mt-1">
          Build and license information for this LabDog instance.
        </p>
      </div>

      {isLoading && (
        <div className="text-slate-400 py-8 text-center">Loading&hellip;</div>
      )}

      {error && (
        <div className="text-red-400 py-8 text-center">
          Failed to load version information.
        </div>
      )}

      {data && (
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-white">Build Info</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="divide-y divide-slate-700">
                <InfoRow label="Version">
                  <span className="font-mono text-white">{data.version}</span>
                </InfoRow>

                <InfoRow label="Commit">
                  {data.commit_sha && data.commit_sha_short ? (
                    <a
                      href={`${data.repo_url}/commit/${data.commit_sha}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="font-mono text-blue-400 hover:underline"
                    >
                      {data.commit_sha_short}
                    </a>
                  ) : (
                    <span className="text-slate-500 italic">dev build</span>
                  )}
                </InfoRow>

                <InfoRow label="Built">
                  <span>{formatBuildDate(data.build_date)}</span>
                </InfoRow>

                <InfoRow label="License">
                  <a
                    href={`${data.repo_url}/blob/main/LICENSE`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-blue-400 hover:underline"
                  >
                    {data.license}
                  </a>
                </InfoRow>

                <InfoRow label="Source">
                  <a
                    href={data.repo_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-blue-400 hover:underline break-all"
                  >
                    {data.repo_url}
                  </a>
                </InfoRow>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-white">Support</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-slate-400 text-sm mb-3">
                Copy this line when filing a bug report or opening a support ticket.
              </p>
              <div className="flex items-center gap-2 rounded-md bg-slate-800 border border-slate-700 px-3 py-2">
                <code className="font-mono text-sm text-slate-200 flex-1">
                  {supportLine}
                </code>
                <CopyButton text={supportLine ?? ""} />
              </div>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  )
}
