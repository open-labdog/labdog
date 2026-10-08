"use client"

import type { CSSProperties, ReactNode, RefObject } from "react"
import { tint, toneVar } from "@/components/ld"
import type { LineKind, LogLine } from "@/lib/ansible-log"

const DANGER = toneVar("danger")
const WARN = toneVar("warn")

const KIND_STYLE: Partial<Record<LineKind, CSSProperties>> = {
  play: { color: toneVar("accent"), fontWeight: 600 },
  "recap-head": { color: toneVar("accent"), fontWeight: 600 },
  task: { color: "var(--text)", fontWeight: 600 },
  section: { color: "var(--text)", fontWeight: 600 },
  ok: { color: toneVar("ok") },
  changed: { color: WARN },
  rescued: { color: WARN },
  ignored: { color: WARN },
  warning: { color: WARN },
  skipping: { color: "var(--text-3)" },
  included: { color: "var(--text-3)" },
  failed: { color: DANGER },
  fatal: { color: DANGER },
  unreachable: { color: DANGER },
  error: { color: DANGER },
  "labdog-failed": { color: DANGER },
  labdog: { color: toneVar("accent") },
}

/** A recap count's colour: what it counts, and only when it is not zero. */
function countColour(key: string, n: number): string {
  if (n === 0) return "var(--text-faint)"
  if (key === "failed" || key === "unreachable") return DANGER
  if (key === "changed" || key === "rescued" || key === "ignored") return WARN
  if (key === "ok") return toneVar("ok")
  return "var(--text-3)"
}

/** Where the search query occurs: [line index, offset] in reading order.
 *  The renderer numbers its marks in the same order. */
export function findMatches(lines: LogLine[], query: string): Array<[number, number]> {
  const q = query.toLowerCase()
  if (!q) return []
  const out: Array<[number, number]> = []
  lines.forEach((l, i) => {
    const t = l.text.toLowerCase()
    for (let at = t.indexOf(q); at !== -1; at = t.indexOf(q, at + q.length)) out.push([i, at])
  })
  return out
}

function structured(line: LogLine): ReactNode {
  if (line.kind === "recap-row") {
    const colon = line.text.indexOf(":")
    const counts = line.text.slice(colon + 1)
    return (
      <>
        <span style={{ color: "var(--text)", fontWeight: 600 }}>{line.text.slice(0, colon)}</span>:
        {counts.split(/(\w+=\d+)/).map((part, i) => {
          const m = /^(\w+)=(\d+)$/.exec(part)
          return m ? (
            <span key={i} style={{ color: countColour(m[1], Number(m[2])), fontWeight: Number(m[2]) > 0 && m[1] !== "ok" && m[1] !== "skipped" ? 600 : undefined }}>
              {part}
            </span>
          ) : (
            part
          )
        })}
      </>
    )
  }
  if (line.kind === "play" || line.kind === "task" || line.kind === "recap-head") {
    // Ansible pads headers with stars to 80 columns; they are noise to read.
    const m = / \*+$/.exec(line.text)
    if (m) {
      return (
        <>
          {line.text.slice(0, m.index)}
          <span style={{ color: "var(--text-faint)", fontWeight: 400 }}>{m[0]}</span>
        </>
      )
    }
  }
  return line.text
}

/**
 * An Ansible log, coloured by what each line says: results in the status
 * tones, task and play headers set apart, LabDog's own step lines in the
 * accent, the recap's non-zero counts in their colours.
 *
 * The text is untouched — every line is a span followed by a newline text
 * node — so selecting and copying the log gives back exactly what Ansible
 * printed.
 *
 * With a search `query`, matches are `<mark data-match={n}>`, numbered in
 * the order `findMatches` returns them, and `current` is highlighted
 * harder. Each line carries `data-line` for jumping to it.
 */
export function AnsibleLog({
  lines,
  fontSize,
  wrap,
  query,
  current,
  preRef,
}: {
  lines: LogLine[]
  fontSize: number
  wrap: boolean
  query: string
  current: number
  preRef: RefObject<HTMLPreElement | null>
}) {
  const q = query.toLowerCase()
  let n = 0

  const marked = (text: string): ReactNode => {
    const lower = text.toLowerCase()
    const parts: ReactNode[] = []
    let from = 0
    for (let at = lower.indexOf(q); at !== -1; at = lower.indexOf(q, at + q.length)) {
      if (at > from) parts.push(text.slice(from, at))
      const idx = n++
      parts.push(
        <mark key={at} data-match={idx} style={{ ...tint(idx === current ? "warn" : "hold"), borderRadius: 2, outline: idx === current ? `1px solid ${WARN}` : undefined }}>
          {text.slice(at, at + q.length)}
        </mark>,
      )
      from = at + q.length
    }
    if (from < text.length) parts.push(text.slice(from))
    return parts
  }

  return (
    <pre
      ref={preRef}
      data-testid="run-log"
      className="mono scroll m-0 min-h-0 flex-1 px-2.5 py-2 leading-[1.75] text-text-2"
      // Positioned, so a line's offsetTop is measured from the log's top.
      style={{ position: "relative", fontSize, whiteSpace: wrap ? "pre-wrap" : "pre", wordBreak: wrap ? "break-word" : undefined }}
    >
      {lines.map((line, i) => (
        <span key={i}>
          <span data-line={i} data-kind={line.kind} style={{ ...KIND_STYLE[line.kind], opacity: line.cont ? 0.82 : undefined }}>
            {q ? marked(line.text) : structured(line)}
          </span>
          {i < lines.length - 1 ? "\n" : null}
        </span>
      ))}
    </pre>
  )
}
