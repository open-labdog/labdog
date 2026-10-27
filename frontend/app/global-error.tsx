"use client"

/**
 * The last-resort boundary: the root layout itself failed, so neither
 * globals.css nor the fonts can be relied on. Everything is inline, in
 * the dark theme's token values (globals.css `--bg`, `--surface`,
 * `--border-strong`, `--text`, `--text-2`, `--danger`, `--danger-soft`,
 * `--accent-fill`) so it still looks like LabDog.
 */
export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          background: "#0b0d10",
          color: "#e9edf1",
          fontFamily: "system-ui, -apple-system, sans-serif",
          fontSize: 13,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          minHeight: "100vh",
          padding: 24,
          boxSizing: "border-box",
        }}
      >
        <div
          style={{
            width: "100%",
            maxWidth: 440,
            background: "#12151a",
            border: "1px solid #323a45",
            borderRadius: 8,
            padding: 24,
            display: "flex",
            flexDirection: "column",
            gap: 14,
          }}
        >
          <h1 style={{ margin: 0, fontSize: 15, fontWeight: 600 }}>Something went wrong</h1>
          <p
            role="alert"
            style={{
              margin: 0,
              padding: "9px 14px",
              fontSize: 11.5,
              lineHeight: 1.45,
              borderRadius: 5,
              border: "1px solid oklch(0.66 0.17 25)",
              background: "oklch(0.30 0.08 25)",
            }}
          >
            {error.message || "A critical error occurred."}
          </p>
          <p style={{ margin: 0, color: "#a6afba", fontSize: 12 }}>LabDog could not render at all. Reloading usually brings it back.</p>
          <div>
            <button
              type="button"
              onClick={reset}
              style={{
                background: "oklch(0.52 0.15 250)",
                color: "#fff",
                border: "1px solid oklch(0.52 0.15 250)",
                padding: "5px 10px",
                borderRadius: 5,
                cursor: "pointer",
                fontSize: 12,
                fontWeight: 600,
                fontFamily: "inherit",
              }}
            >
              Reload
            </button>
          </div>
        </div>
      </body>
    </html>
  )
}
