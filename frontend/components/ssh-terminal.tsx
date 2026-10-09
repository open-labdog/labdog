"use client"

import { useEffect, useRef, useCallback, useState } from "react"
import { Terminal } from "@xterm/xterm"
import { FitAddon } from "@xterm/addon-fit"
import { WebLinksAddon } from "@xterm/addon-web-links"
import "@xterm/xterm/css/xterm.css"
import { useTerminalWebSocket } from "@/hooks/use-terminal-websocket"

interface SshTerminalProps {
  hostId: number
  hostname: string
  /** Changing it resizes the text in place; the session is kept. */
  fontSize?: number
}

/** A hidden (minimized) container measures 0×0, and fitting to it would
 *  tell the remote PTY it has a nonsense size. */
function hasSize(el: HTMLElement | null): boolean {
  return !!el && el.clientWidth > 0 && el.clientHeight > 0
}

export function SshTerminal({ hostId, hostname, fontSize = 14 }: SshTerminalProps) {
  const terminalRef = useRef<HTMLDivElement>(null)
  // Read when the terminal is created, so a size change does not recreate
  // it (and drop the session); the effect below applies later changes.
  const fontSizeRef = useRef(fontSize)
  const xtermRef = useRef<Terminal | null>(null)
  const fitAddonRef = useRef<FitAddon | null>(null)
  const [connectKey, setConnectKey] = useState(0)

  const onData = useCallback((data: Uint8Array) => {
    xtermRef.current?.write(data)
  }, [])

  const { state, closeReason, connect, sendData, sendResize, close } = useTerminalWebSocket({
    hostId,
    onData,
  })

  useEffect(() => {
    if (!terminalRef.current) return

    const term = new Terminal({
      cursorBlink: true,
      fontSize: fontSizeRef.current,
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

    if (hasSize(terminalRef.current)) fitAddon.fit()

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

    connect()

    const container = terminalRef.current
    const resizeObserver = new ResizeObserver(() => {
      if (!hasSize(container)) return
      fitAddon.fit()
      sendResize(term.cols, term.rows)
    })
    resizeObserver.observe(container)

    return () => {
      resizeObserver.disconnect()
      close()
      term.dispose()
      xtermRef.current = null
      fitAddonRef.current = null
    }
    // connect/sendData/sendResize/close are stable (connect changes only with
    // hostId), so this re-runs on reconnect (connectKey) and host switch —
    // never capturing a stale connect.
  }, [connectKey, connect, sendData, sendResize, close])

  useEffect(() => {
    fontSizeRef.current = fontSize
    const term = xtermRef.current
    if (!term || term.options.fontSize === fontSize) return
    term.options.fontSize = fontSize
    if (!hasSize(terminalRef.current)) return
    fitAddonRef.current?.fit()
    sendResize(term.cols, term.rows)
  }, [fontSize, sendResize])

  return (
    <div className="flex flex-col h-full">
      {state === "connecting" && (
        <div className="flex items-center justify-center p-4 text-sm text-text-3">
          Connecting to {hostname}...
        </div>
      )}
      {(state === "error" || state === "disconnected") && (
        <div className="flex items-center justify-center gap-3 p-4">
          <span className="text-sm text-text-3">
            {state === "error" ? `Connection failed: ${closeReason}` : "Session ended."}
          </span>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => {
              xtermRef.current?.dispose()
              xtermRef.current = null
              setConnectKey(k => k + 1)
            }}
          >
            Reconnect
          </button>
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
