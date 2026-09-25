# Vendored fonts

These are served from the repo instead of fetched from Google at build
time.

`next/font/google` downloads the font while compiling. That made every
production build depend on reaching `fonts.gstatic.com`, and when it
could not, Turbopack failed with a dozen errors reading

```
Module not found: Can't resolve '@vercel/turbopack-next/internal/font/google/font'
```

which name neither the network nor the font. CI hit exactly that, on a
pull request whose entire diff was a git SHA in a text file. A build that
fails for reasons unrelated to the change is worse than a slow one — it
teaches you to re-run without reading the log.

## What is here

| File | Family | Weights | Used as |
| --- | --- | --- | --- |
| `ibm-plex-sans.woff2` | IBM Plex Sans | 400–700 (variable) | `--font-sans` |
| `ibm-plex-mono-400.woff2` | IBM Plex Mono | 400 | `--font-mono` |
| `ibm-plex-mono-500.woff2` | IBM Plex Mono | 500 | `--font-mono` |
| `ibm-plex-mono-600.woff2` | IBM Plex Mono | 600 | `--font-mono` |

All four are the **latin** subset. Plex Sans ships as a variable build
on Google Fonts so one file covers the whole weight range; Plex Mono
does not, so it is three static weights — the three the UI actually
uses (body, emphasis, and the uppercase mono section labels). Together
they are ~91 kB.

Nothing outside `app/layout.tsx` refers to a font by name — the rest of
the app reads the `--font-sans` / `--font-mono` CSS variables, and
Tailwind's `font-sans` / `font-mono` resolve through them. Changing
either typeface means replacing a file and editing one `localFont()`
call.

## Licensing

IBM Plex is SIL Open Font License 1.1, which permits redistribution
including bundling with software. The full text is next to the fonts as
`OFL-IBM-Plex.txt`; keep it with the files if you replace or move them,
and check the licence of anything you swap in.

## Updating them

Take the URLs from the Google Fonts CSS API rather than guessing, so the
subset and axis ranges match what was there before:

```bash
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
curl -sS -A "$UA" "https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400..700&display=swap"
curl -sS -A "$UA" "https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&display=swap"
```

The response has one `@font-face` per subset (and, for Mono, per
weight). Take the `src: url(...)` from the block commented `/* latin */`
— the `latin-ext` block above it is a different, larger file. Then
verify what you downloaded is actually a font before committing it,
since a failed request will happily write an HTML error page to a
`.woff2` path:

```bash
file ibm-plex-sans.woff2   # => Web Open Font Format (Version 2), ...
```
