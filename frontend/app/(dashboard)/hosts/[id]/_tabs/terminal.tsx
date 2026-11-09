"use client"

import dynamic from "next/dynamic"
import Link from "next/link"
import { Window } from "@/components/ld"

// xterm.js is the largest chunk in the export (84 KB gzipped). Loaded
// here, only the Terminal tab and page pay for it, not every host page.
const SshTerminal = dynamic(
  () => import("@/components/ssh-terminal").then((m) => m.SshTerminal),
  {
    ssr: false,
    loading: () => <div className="p-3.5 text-xs text-text-3">Loading terminal…</div>,
  },
)

export function TerminalTab({ hostId, hostname }: { hostId: number; hostname: string }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col p-3.5">
      {/* No escRestores: Esc belongs to the shell (vim, less), not the window. */}
      <Window
        storageKey="terminal"
        title={`ssh · ${hostname}`}
        meta="xterm.js"
        defaultHeight="fill"
        defaultFontSize={14}
        fontRange={[10, 24]}
        testId="terminal-window"
        actions={<Link href={`/hosts/${hostId}/terminal`} className="tt text-ld-accent hover:no-underline">open full page →</Link>}
      >
        {({ fontSize }) => <SshTerminal hostId={hostId} hostname={hostname} fontSize={fontSize} />}
      </Window>
    </div>
  )
}
