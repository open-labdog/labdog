"use client"

import Link from "next/link"
import { Panel } from "@/components/ld"
import { SshTerminal } from "@/components/ssh-terminal"

export function TerminalTab({ hostId, hostname }: { hostId: number; hostname: string }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col p-3.5">
      <Panel
        title={`ssh · ${hostname}`}
        meta="xterm.js"
        actions={<Link href={`/hosts/${hostId}/terminal`} className="tt text-ld-accent hover:no-underline">open full page →</Link>}
        style={{ background: "var(--bg)" }}
        flex={1}
      >
        <SshTerminal hostId={hostId} hostname={hostname} />
      </Panel>
    </div>
  )
}
