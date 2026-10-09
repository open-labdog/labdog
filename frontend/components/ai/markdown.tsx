"use client"

import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"

import { CodeBlock } from "@/components/ld"

interface Props {
  children: string
  className?: string
}

/**
 * Renders model-authored markdown.
 *
 * Reports arrive as markdown — headings, bold, bullets, tables — and were
 * previously printed verbatim, so an operator read `**Overall: healthy**`
 * with the asterisks in place.
 *
 * **Raw HTML is deliberately not enabled.** react-markdown ignores embedded
 * HTML unless `rehype-raw` is added, and it must not be. This text is
 * written by a model whose context includes command output from managed
 * hosts, so a crafted string in a log file is an input to what renders
 * here. Markdown alone cannot inject script; markdown plus raw HTML can.
 * The same reasoning applies to `urlTransform`, left at its default, which
 * drops `javascript:` and `data:` schemes from links.
 *
 * Verified against this version rather than assumed: `<script>`, `<iframe>`
 * and `<img onerror=…>` all come out as escaped text, and a `javascript:`
 * link renders with an empty href.
 *
 * Images are the one case markdown alone *can* abuse, so they are refused
 * below. `![](https://elsewhere/p.gif)` renders a real request — and a
 * preload hint — to a URL the model chose, which is a beacon out of the
 * operator's browser and a channel for smuggling what it learned into the
 * query string. A health report has no need to show a picture.
 *
 * Styling is explicit per element rather than via a typography plugin:
 * LabDog does not carry one. Everything reads the theme tokens, so a
 * report is legible in both themes.
 */
export function Markdown({ children, className }: Props) {
  return (
    <div className={`min-w-0 text-[12.5px] leading-[1.6] text-text-2 ${className ?? ""}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => <h1 className="mb-2 mt-4 text-[14px] font-semibold text-text first:mt-0">{children}</h1>,
          h2: ({ children }) => <h2 className="mb-2 mt-4 text-[13px] font-semibold text-text first:mt-0">{children}</h2>,
          h3: ({ children }) => <h3 className="mb-1 mt-3 text-[12.5px] font-semibold text-text first:mt-0">{children}</h3>,
          p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
          strong: ({ children }) => <strong className="font-semibold text-text">{children}</strong>,
          em: ({ children }) => <em className="italic">{children}</em>,
          ul: ({ children }) => <ul className="mb-2 list-disc space-y-1 pl-5 last:mb-0">{children}</ul>,
          ol: ({ children }) => <ol className="mb-2 list-decimal space-y-1 pl-5 last:mb-0">{children}</ol>,
          li: ({ children }) => <li>{children}</li>,
          code: ({ className: lang, children }) => {
            // react-markdown marks fenced blocks with a language class and
            // leaves inline spans bare; only the latter should be styled as
            // a chip, since the block is handled by `pre`.
            const isBlock = typeof lang === "string" && lang.includes("language-")
            if (isBlock) return <code>{children}</code>
            return <code className="mono rounded-[3px] bg-surface-3 px-1 py-px text-[11px] text-text">{children}</code>
          },
          // Command output and config snippets are routinely wider than the
          // pane. Scroll inside the block so the page itself never does.
          pre: ({ children }) => (
            <CodeBlock wrap={false} className="mb-2 last:mb-0">
              {children}
            </CodeBlock>
          ),
          // Never fetch. Shows what the model meant to display, without
          // the browser reaching out to whatever URL it named.
          img: ({ alt }) => <span className="italic text-text-3">[image omitted{alt ? `: ${alt}` : ""}]</span>,
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noopener noreferrer" className="text-ld-accent underline underline-offset-2">
              {children}
            </a>
          ),
          blockquote: ({ children }) => <blockquote className="mb-2 border-l-2 border-line-strong pl-3 text-text-3 last:mb-0">{children}</blockquote>,
          hr: () => <hr className="my-3 border-line" />,
          table: ({ children }) => (
            <div className="mb-2 overflow-x-auto last:mb-0">
              <table className="w-full border-collapse text-[11.5px]">{children}</table>
            </div>
          ),
          th: ({ children }) => <th className="border border-line bg-surface-2 px-2 py-1 text-left font-semibold text-text">{children}</th>,
          td: ({ children }) => <td className="border border-line px-2 py-1 align-top">{children}</td>,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  )
}
