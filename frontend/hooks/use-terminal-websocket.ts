import { useRef, useCallback, useEffect, useState } from "react"

type ConnectionState = "connecting" | "connected" | "disconnected" | "error"

interface UseTerminalWebSocketOptions {
  hostId: number
  onData: (data: Uint8Array) => void
  onDisconnect?: (code: number, reason: string) => void
}

export function useTerminalWebSocket({ hostId, onData, onDisconnect }: UseTerminalWebSocketOptions) {
  const wsRef = useRef<WebSocket | null>(null)
  const [state, setState] = useState<ConnectionState>("connecting")
  const [closeReason, setCloseReason] = useState<string>("")

  const connect = useCallback(() => {
    const apiUrl = process.env.NEXT_PUBLIC_API_URL || `${window.location.protocol}//${window.location.host}`
    const wsUrl = apiUrl.replace(/^http/, "ws") + `/api/ssh-terminal/ws/${hostId}`

    setState("connecting")
    const ws = new WebSocket(wsUrl)
    ws.binaryType = "arraybuffer"
    wsRef.current = ws

    ws.onopen = () => setState("connected")

    ws.onmessage = (event) => {
      if (event.data instanceof ArrayBuffer) {
        onData(new Uint8Array(event.data))
      } else if (typeof event.data === "string") {
        // Control message (pong, etc.) - ignore for now
      }
    }

    ws.onclose = (event) => {
      wsRef.current = null
      setCloseReason(event.reason || `Closed (${event.code})`)
      setState(event.code === 1000 ? "disconnected" : "error")
      onDisconnect?.(event.code, event.reason)
    }

    ws.onerror = () => {
      setState("error")
    }
  }, [hostId, onData, onDisconnect])

  const sendData = useCallback((data: string | Uint8Array) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      if (typeof data === "string") {
        wsRef.current.send(new TextEncoder().encode(data))
      } else {
        wsRef.current.send(data)
      }
    }
  }, [])

  const sendResize = useCallback((cols: number, rows: number) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "resize", cols, rows }))
    }
  }, [])

  const close = useCallback(() => {
    wsRef.current?.close(1000)
    wsRef.current = null
  }, [])

  useEffect(() => {
    return () => {
      wsRef.current?.close(1000)
      wsRef.current = null
    }
  }, [])

  return { state, closeReason, connect, sendData, sendResize, close }
}
