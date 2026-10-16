"use client"

import Link from "next/link"
import { Window } from "@/components/ld"
import { SshTerminal } from "@/components/ssh-terminal"

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
