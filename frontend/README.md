# Barricade Frontend

Next.js 16 web UI for Barricade firewall management.

## Stack

- Next.js 16 (App Router)
- React 19
- shadcn/ui + Tailwind CSS v4
- TanStack Query (data fetching)
- Playwright (E2E testing)

## Development

```bash
npm install
npm run dev      # http://localhost:3000
```

Requires the backend API running at `http://localhost:8000` (configurable via `NEXT_PUBLIC_API_URL`).

## Testing

```bash
npx playwright install --with-deps
npx playwright test          # requires full Docker stack running
npx playwright test --ui     # interactive test runner
```

## Pages

| Route | Description |
|-------|-------------|
| `/login` | Login page |
| `/register` | Registration page |
| `/dashboard` | Overview dashboard |
| `/groups` | Host group management (supports category view toggle with collapsible sections) |
| `/groups/[id]` | Group detail |
| `/groups/[id]/rules` | Rule management per group |
| `/groups/[id]/sync` | Sync with plan preview |
| `/hosts` | Host management |
| `/hosts/[id]` | Host detail and drift status |
| `/hosts/[id]/terminal` | Full-page SSH terminal (web shell) |
| `/ssh-keys` | SSH key management |
| `/audit` | Audit log viewer |

## Build

```bash
npm run build    # production build (standalone output)
```

See the [root README](../README.md) for full project documentation.
