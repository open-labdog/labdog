"use client"

import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from "react"
import { useLocalPref } from "@/lib/local-pref"

/** What a window remembers in this browser. `height: null` is the default size. */
export interface WindowPref {
  height: number | null
  fontSize: number
  minimized: boolean
  wrap: boolean
}

export interface WindowState {
  fontSize: number
  /** Minimized: the body is in the DOM but not displayed, so it has no size. */
  hidden: boolean
  maximized: boolean
  pref: WindowPref
  update: (patch: Partial<WindowPref>) => void
}

const MIN_HEIGHT = 120

/**
 * A log or a terminal the viewer can size: the CodeBlock header (tag, mono
 * title, meta, the caller's actions) plus text size, minimize and maximize,
 * over a body whose height a handle on its free edge drags. Height, text
 * size, minimized and wrap are remembered per browser under
 * `labdog.window.<storageKey>`. `rememberHeight={false}` keeps a dragged
 * height for this page only, for a window whose right default depends on
 * what is on the page (the run log sits under however many hosts the run
 * has).
 *
 * Minimizing hides the body rather than unmounting it, so a terminal keeps
 * its session. Maximizing fills the browser window, above the shell and
 * below modals. `escRestores` lets Esc leave it — right for a log, wrong
 * for a terminal, where Esc belongs to the program running in it.
 *
 * `defaultHeight="fill"` takes the space the parent's flex column offers,
 * but never less than the minimum height, until the viewer drags the
 * handle; a number is a height in pixels.
 *
 * `anchor` is the edge that stays put. A top-anchored window (the
 * terminal) has its handle at the bottom and grows downwards; a
 * bottom-anchored one (the run log, docked under the host table) has it at
 * the top and grows upwards, over whatever is above it in the column. Either
 * way it never grows past its parent's box, so the handle cannot be dragged
 * out of reach.
 */
export function Window({
  title,
  tag,
  meta,
  actions,
  storageKey,
  defaultHeight,
  defaultFontSize,
  fontRange = [9, 18],
  escRestores = false,
  rememberHeight = true,
  anchor = "top",
  className,
  testId,
  children,
}: {
  title?: ReactNode
  tag?: ReactNode
  meta?: ReactNode
  actions?: ReactNode | ((s: WindowState) => ReactNode)
  storageKey: string
  defaultHeight: number | "fill"
  defaultFontSize: number
  fontRange?: [number, number]
  escRestores?: boolean
  rememberHeight?: boolean
  anchor?: "top" | "bottom"
  className?: string
  testId?: string
  children: (s: WindowState) => ReactNode
}) {
  const [pref, update] = useLocalPref<WindowPref>(`labdog.window.${storageKey}`, {
    height: null,
    fontSize: defaultFontSize,
    minimized: false,
    wrap: true,
  })
  const [maximized, setMaximized] = useState(false)
  // The height while a drag is in progress; written to the pref on release
  // rather than to storage on every pointer move.
  const [dragHeight, setDragHeight] = useState<number | null>(null)
  const [pageHeight, setPageHeight] = useState<number | null>(null)
  const savedHeight = rememberHeight ? pref.height : pageHeight
  const setHeight = (h: number | null) => (rememberHeight ? update({ height: h }) : setPageHeight(h))
  const boxRef = useRef<HTMLDivElement>(null)
  const drag = useRef<{ y: number; h: number; max: number } | null>(null)

  const [minFont, maxFont] = fontRange
  const fontSize = Math.min(maxFont, Math.max(minFont, pref.fontSize))
  const hidden = pref.minimized && !maximized
  const state: WindowState = { fontSize, hidden, maximized, pref, update }

  useEffect(() => {
    if (!maximized || !escRestores) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented) setMaximized(false)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [maximized, escRestores])

  /** The tallest the window can be without leaving its parent's content
   *  box: from the anchored edge to the parent's opposite edge. */
  const maxHeight = (): number => {
    const box = boxRef.current
    const parent = box?.parentElement
    if (!box || !parent) return window.innerHeight
    const b = box.getBoundingClientRect()
    const p = parent.getBoundingClientRect()
    const cs = getComputedStyle(parent)
    return anchor === "bottom"
      ? b.bottom - (p.top + parseFloat(cs.paddingTop) + parseFloat(cs.borderTopWidth))
      : p.bottom - parseFloat(cs.paddingBottom) - parseFloat(cs.borderBottomWidth) - b.top
  }
  const clampHeight = (h: number, max: number) => Math.round(Math.min(Math.max(h, MIN_HEIGHT), Math.max(MIN_HEIGHT, max)))

  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (!boxRef.current) return
    e.preventDefault()
    e.currentTarget.setPointerCapture(e.pointerId)
    drag.current = { y: e.clientY, h: boxRef.current.getBoundingClientRect().height, max: maxHeight() }
  }
  const onPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (!drag.current) return
    const dy = e.clientY - drag.current.y
    setDragHeight(clampHeight(drag.current.h + (anchor === "bottom" ? -dy : dy), drag.current.max))
  }
  const onPointerUp = () => {
    if (drag.current && dragHeight !== null) setHeight(dragHeight)
    drag.current = null
    setDragHeight(null)
  }

  const height = dragHeight ?? savedHeight
  const sizing = maximized
    ? "fixed inset-0 z-[80] rounded-none"
    : hidden
      ? "shrink-0"
      : height === null && defaultHeight === "fill"
        ? "min-h-0 flex-1"
        : "shrink-0"
  // maxHeight guards a remembered height on a smaller window than it was set on.
  const style = maximized || hidden
    ? undefined
    : height === null && defaultHeight === "fill"
      ? { minHeight: MIN_HEIGHT, maxHeight: "100%" }
      : { height: height ?? defaultHeight, maxHeight: "100%" }
  const ctl = "btn btn-sm btn-ghost mono"
  const handle = !maximized && !hidden && (
    <div
      role="separator"
      aria-orientation="horizontal"
      aria-label="Drag to resize; double-click for the default size"
      title="drag to resize · double-click to reset"
      className={`h-[7px] shrink-0 cursor-ns-resize border-line bg-surface-2 hover:bg-surface-3 ${anchor === "top" ? "border-t" : "border-b"}`}
      style={{ touchAction: "none" }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onDoubleClick={() => setHeight(null)}
    />
  )

  return (
    <div
      ref={boxRef}
      data-testid={testId}
      data-maximized={maximized || undefined}
      data-minimized={hidden || undefined}
      // mt-auto keeps a bottom-anchored window on the bottom edge when there
      // is nothing above it to take up the space.
      className={`flex min-w-0 flex-col overflow-hidden rounded-r border border-line bg-surface ${sizing} ${anchor === "bottom" && !maximized ? "mt-auto" : ""} ${className ?? ""}`}
      style={style}
    >
      {anchor === "bottom" && handle}
      <div className={`flex shrink-0 items-center gap-2 bg-surface-2 px-2.5 py-1.5 ${hidden ? "" : "border-b border-line"}`}>
        {tag}
        {title != null && <span className="mono trunc text-[11.5px] text-text">{title}</span>}
        {meta != null && <span className="mono num text-[10.5px] text-text-faint">{meta}</span>}
        {hidden && <span className="tt text-text-faint">minimized</span>}
        <div className="ml-auto flex items-center gap-1.5">
          {!hidden && (typeof actions === "function" ? actions(state) : actions)}
          <div className="flex items-center" role="group" aria-label="window">
            {!hidden && (
              <>
                <button type="button" className={ctl} style={{ padding: "2px 5px" }} aria-label="Smaller text" title="smaller text" disabled={fontSize <= minFont} onClick={() => update({ fontSize: fontSize - 1 })}>
                  A−
                </button>
                <button type="button" className={ctl} style={{ padding: "2px 5px" }} aria-label="Larger text" title="larger text" disabled={fontSize >= maxFont} onClick={() => update({ fontSize: fontSize + 1 })}>
                  A+
                </button>
              </>
            )}
            {!maximized && (
              <button type="button" className={ctl} style={{ padding: "2px 5px" }} aria-label={hidden ? "Restore" : "Minimize"} title={hidden ? "restore" : "minimize"} onClick={() => update({ minimized: !pref.minimized })}>
                {hidden ? "▴" : "▾"}
              </button>
            )}
            <button
              type="button"
              className={ctl}
              style={{ padding: "2px 5px" }}
              aria-label={maximized ? "Exit maximized" : "Maximize"}
              title={maximized ? (escRestores ? "restore size — esc" : "restore size") : "maximize"}
              onClick={() => setMaximized((m) => !m)}
            >
              {maximized ? "⤡" : "⤢"}
            </button>
          </div>
        </div>
      </div>
      <div className="flex min-h-0 flex-1 flex-col" style={{ display: hidden ? "none" : undefined }}>
        {children(state)}
      </div>
      {anchor === "top" && handle}
    </div>
  )
}
