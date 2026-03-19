"use client"

import { useEffect, useRef, useCallback, useState } from "react"
import { Terminal } from "@xterm/xterm"
import { FitAddon } from "@xterm/addon-fit"
import { WebLinksAddon } from "@xterm/addon-web-links"
import "@xterm/xterm/css/xterm.css"
import { useTerminalWebSocket } from "@/hooks/use-terminal-websocket"
import { Button } from "@/components/ui/button"

interface SshTerminalProps {
  hostId: number
  hostname: string
}

export function SshTerminal({ hostId, hostname }: SshTerminalProps) {
  const terminalRef = useRef<HTMLDivElement>(null)
  const xtermRef = useRef<Terminal | null>(null)
  const fitAddonRef = useRef<FitAddon | null>(null)
  const [initialized, setInitialized] = useState(false)

  const onData = useCallback((data: Uint8Array) => {
    xtermRef.current?.write(data)
  }, [])

  const { state, closeReason, connect, sendData, sendResize, close } = useTerminalWebSocket({
    hostId,
    onData,
  })

  useEffect(() => {
    if (!terminalRef.current || initialized) return

    const term = new Terminal({
      cursorBlink: true,
      fontSize: 14,
      fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace",
      theme: {
        background: "#1a1b26",
        foreground: "#c0caf5",
        cursor: "#c0caf5",
        selectionBackground: "#33467c",
      },
    })

    const fitAddon = new FitAddon()
    const webLinksAddon = new WebLinksAddon()

    term.loadAddon(fitAddon)
    term.loadAddon(webLinksAddon)
    term.open(terminalRef.current)

    fitAddon.fit()

    term.onData((data) => {
      sendData(new TextEncoder().encode(data))
    })

    term.onBinary((data) => {
      const bytes = new Uint8Array(data.length)
      for (let i = 0; i < data.length; i++) bytes[i] = data.charCodeAt(i)
      sendData(bytes)
    })

    xtermRef.current = term
    fitAddonRef.current = fitAddon
    setInitialized(true)

    connect()

    const resizeObserver = new ResizeObserver(() => {
      fitAddon.fit()
      sendResize(term.cols, term.rows)
    })
    resizeObserver.observe(terminalRef.current)

    return () => {
      resizeObserver.disconnect()
      close()
      term.dispose()
      xtermRef.current = null
      fitAddonRef.current = null
    }
  }, [initialized, connect, sendData, sendResize, close])

  return (
    <div className="flex flex-col h-full">
      {state === "connecting" && (
        <div className="flex items-center justify-center p-4 text-slate-400 text-sm">
          Connecting to {hostname}...
        </div>
      )}
      {(state === "error" || state === "disconnected") && (
        <div className="flex items-center justify-center gap-3 p-4">
          <span className="text-slate-400 text-sm">
            {state === "error" ? `Connection failed: ${closeReason}` : "Session ended."}
          </span>
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              setInitialized(false)
              xtermRef.current?.dispose()
              xtermRef.current = null
            }}
          >
            Reconnect
          </Button>
        </div>
      )}
      <div
        ref={terminalRef}
        data-testid="ssh-terminal"
        className="flex-1 min-h-0"
        style={{ display: state === "connected" || state === "connecting" ? "block" : "none" }}
      />
    </div>
  )
}
