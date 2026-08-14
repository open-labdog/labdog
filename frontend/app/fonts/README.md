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
| `dm-sans.woff2` | DM Sans | 400–700 (variable) | `--font-sans` |
| `jetbrains-mono.woff2` | JetBrains Mono | 400–500 (variable) | `--font-mono` |

Both are the **variable** builds of the **latin** subset, which is what
`next/font/google` was fetching before. One file covers the whole weight
range, so this is two files and ~68 kB rather than six static weights.

Nothing outside `app/layout.tsx` refers to a font by name — the rest of
the app reads the `--font-sans` / `--font-mono` CSS variables, and
Tailwind's `font-sans` / `font-mono` resolve through them. Changing
either typeface means replacing a file and editing one `localFont()`
call.

## Licensing

Both are SIL Open Font License 1.1, which permits redistribution
including bundling with software. The full texts are next to the fonts as
`OFL-DM-Sans.txt` and `OFL-JetBrains-Mono.txt`; keep them with the files
if you replace or move them, and check the licence of anything you swap
in.

## Updating them

Take the URLs from the Google Fonts CSS API rather than guessing, so the
subset and axis ranges match what was there before:

```bash
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
curl -sS -A "$UA" "https://fonts.googleapis.com/css2?family=DM+Sans:wght@400..700&display=swap"
```

The response has one `@font-face` per subset. Take the `src: url(...)`
from the block commented `/* latin */` — the `latin-ext` block above it
is a different, larger file. Then verify what you downloaded is actually
a font before committing it, since a failed request will happily write an
HTML error page to a `.woff2` path:

```bash
file dm-sans.woff2   # => Web Open Font Format (Version 2), ...
```
