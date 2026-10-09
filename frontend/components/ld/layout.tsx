"use client"

import type { CSSProperties, ReactNode } from "react"
import { Fragment } from "react"
import Link from "next/link"

/* ── layout ─────────────────────────────────────────────────────── */

export function Panel({
  title,
  meta,
  actions,
  children,
  pad = 0,
  flex,
  style,
  scroll,
  footer,
  className,
}: {
  title?: ReactNode
  meta?: ReactNode
  actions?: ReactNode
  children?: ReactNode
  pad?: number | string
  flex?: number
  style?: CSSProperties
  scroll?: boolean
  footer?: ReactNode
  className?: string
}) {
  return (
    <section
      className={`flex min-h-0 min-w-0 flex-col overflow-hidden rounded-r-lg border border-line bg-surface ${className ?? ""}`}
      style={{ flex, flexShrink: flex ? 1 : 0, ...style }}
    >
      {title && (
        <header className="flex shrink-0 items-center gap-2.5 border-b border-line bg-surface-2 px-[11px] py-2">
          <span className="tt text-text-2">{title}</span>
          {meta && <span className="mono num text-[10.5px] text-text-faint">{meta}</span>}
          {actions && <div className="ml-auto flex items-center gap-1.5">{actions}</div>}
        </header>
      )}
      <div className={`flex min-h-0 flex-1 flex-col ${scroll ? "scroll" : ""}`} style={{ padding: pad }}>
        {children}
      </div>
      {footer && <footer className="shrink-0 border-t border-line bg-surface-2 px-[11px] py-[7px]">{footer}</footer>}
    </section>
  )
}

export function Split({
  children,
  gap = 12,
  cols = "1fr 1fr",
  style,
}: {
  children: ReactNode
  gap?: number
  cols?: string
  style?: CSSProperties
}) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: cols, gap, minHeight: 0, minWidth: 0, ...style }}>{children}</div>
  )
}

export interface Crumb {
  label: string
  onClick?: () => void
  href?: string
}

export function PageHead({
  crumbs = [],
  title,
  sub,
  actions,
  children,
}: {
  crumbs?: Crumb[]
  title: ReactNode
  sub?: ReactNode
  actions?: ReactNode
  children?: ReactNode
}) {
  return (
    <div className="flex shrink-0 flex-col gap-2.5 border-b border-line bg-surface px-4 pb-3 pt-3.5">
      <div className="flex flex-wrap items-start gap-3.5">
        {/* A basis, not just flex-1: below it the actions wrap under the
            title instead of squeezing the subtitle into a column. */}
        <div className="min-w-0 flex-[1_1_260px]">
          {/* A <nav> so assistive tech and tests can find the trail; it
              names the parents only — the current page is the title. */}
          {crumbs.length > 0 && (
            <nav aria-label="breadcrumb" className="mb-1 flex items-center gap-1.5">
              {crumbs.map((c, i) => (
                <Fragment key={i}>
                  {i > 0 && <span className="text-[10px] text-text-faint">/</span>}
                  {c.href ? (
                    <Link className="tt hover:text-text-2 hover:no-underline" href={c.href}>
                      {c.label}
                    </Link>
                  ) : c.onClick ? (
                    <button type="button" className="tt border-0 bg-transparent p-0 hover:text-text-2" onClick={c.onClick}>
                      {c.label}
                    </button>
                  ) : (
                    <span className="tt">{c.label}</span>
                  )}
                </Fragment>
              ))}
            </nav>
          )}
          <h1 className="m-0 flex flex-wrap items-center gap-2.5 text-[19px] font-semibold tracking-[-0.015em]">{title}</h1>
          {sub && <div className="mt-1 text-[12.5px] text-text-2">{sub}</div>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-[7px]">{actions}</div>}
      </div>
      {children}
    </div>
  )
}

export interface TabDef {
  k: string
  label: ReactNode
}

export function Tabs({
  tabs,
  value,
  onChange,
  counts = {},
}: {
  tabs: (string | TabDef)[]
  value: string
  onChange: (k: string) => void
  counts?: Record<string, number | string | undefined>
}) {
  return (
    <div className="scroll flex shrink-0 gap-0.5 overflow-x-auto overflow-y-hidden" role="tablist">
      {tabs.map((t) => {
        const k = typeof t === "string" ? t : t.k
        const label = typeof t === "string" ? t : t.label
        const on = k === value
        return (
          <button
            key={k}
            role="tab"
            aria-selected={on}
            type="button"
            onClick={() => onChange(k)}
            className="flex items-center gap-1.5 whitespace-nowrap border-0 border-b-2 bg-transparent px-2.5 pb-2 pt-1.5 text-[12.5px]"
            style={{
              borderBottomColor: on ? "var(--accent)" : "transparent",
              color: on ? "var(--text)" : "var(--text-3)",
              fontWeight: on ? 600 : 500,
            }}
          >
            {label}
            {counts[k] != null && (
              <span className="mono num text-[10px]" style={{ color: on ? "var(--accent)" : "var(--text-faint)" }}>
                {counts[k]}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}

export interface SegOption {
  k: string
  label: ReactNode
  title?: string
}

export function Seg({
  options,
  value,
  onChange,
  sm,
}: {
  options: (string | SegOption)[]
  value: string
  onChange: (k: string) => void
  sm?: boolean
}) {
  return (
    <div className="inline-flex gap-0.5 rounded-r border border-line bg-surface-3 p-0.5">
      {options.map((o) => {
        const k = typeof o === "string" ? o : o.k
        const label = typeof o === "string" ? o : o.label
        const title = typeof o === "string" ? undefined : o.title
        const on = k === value
        return (
          <button
            key={k}
            type="button"
            onClick={() => onChange(k)}
            title={title}
            className="whitespace-nowrap rounded-[3px] border-0"
            style={{
              padding: sm ? "2px 7px" : "4px 9px",
              fontSize: sm ? 11 : 11.5,
              fontWeight: on ? 600 : 500,
              background: on ? "var(--surface)" : "transparent",
              color: on ? "var(--text)" : "var(--text-3)",
              boxShadow: on ? "var(--shadow)" : "none",
            }}
          >
            {label}
          </button>
        )
      })}
    </div>
  )
}
