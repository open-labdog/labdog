# LabDog Frontend

Next.js 16 web UI for LabDog — centralized Linux configuration management.

## Stack

- Next.js 16 (App Router), built as a static export the backend serves
- React 19
- The LabDog kit (`components/ld`) + Tailwind CSS v4 over the design tokens in `app/globals.css`
- TanStack Query (data fetching)
- React Hook Form + Zod (form validation)
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
npx playwright test          # starts `next dev`; needs the backend on :8000
npx playwright test --ui     # interactive test runner
```

18 E2E spec files covering auth and session expiry, the overview, hosts, groups, firewall rules, sync, scheduled actions, the Git repository wizard, the SSH terminal, the audit log, and UX patterns (breadcrumbs, command palette, confirm dialog, mobile, search, toasts). In CI the static export is served by the backend itself, as a real install runs.

## Screens

The navigation is an icon rail of four zones, each with a pane of
destinations, plus Settings — see [FRONTEND.md](FRONTEND.md#shell-icon-rail--contextual-pane).

### Auth (no shell)
| Route | Description |
|-------|-------------|
| `/login` | Sign in |
| `/register` | First-user registration |

### Overview
| Route | Description |
|-------|-------------|
| `/overview` | Summary, Pending, Fleet state, Activity, Upcoming (`?view=`) |

### Fleet
| Route | Description |
|-------|-------------|
| `/hosts` | Hosts list |
| `/hosts/new` | Add a host |
| `/hosts/[id]` | Host page — Overview · Config · Metrics · Terminal · Activity |
| `/hosts/[id]/terminal` | Full-page SSH terminal |
| `/hosts/[id]/actions/runs/[runId]` | An action run against the host |
| `/groups` | Groups list, in priority order |
| `/groups/new` | Create a group |
| `/groups/[id]` | Group page — Overview · Config (the eight module editors) · Members · Activity |
| `/groups/[id]/actions/runs/[runId]` | An action run against the group |
| `/discovery` | Pending approval · Scan schedules · Scan now (`?tab=`) |

### Operations
| Route | Description |
|-------|-------------|
| `/plans` | Plan → review → apply a sync |
| `/drift` | Drift findings |
| `/actions` | Library · Packs · Schedules (`?tab=`) |
| `/actions/runs/[runId]` | A fleet-wide action run |
| `/runs` | Every sync, action and scheduled run |
| `/audit` | Audit log |

### Assistant
| Route | Description |
|-------|-------------|
| `/assistant` | Assistant sessions |
| `/alerts` | Alerts received from Grafana / Alertmanager |

### Settings
| Route | Description |
|-------|-------------|
| `/settings` | Integrations · AI · Access · Fleet defaults · System (`?section=`) |
| `/hypervisors` | Proxmox nodes |
| `/grafana` | Grafana / Mimir / Loki instances |
| `/git-repos`, `/git-repos/new`, `/git-repos/[id]` | Git repositories and the connect wizard |
| `/ai-providers` | AI providers |
| `/ssh-keys` | SSH keys |
| `/users` | Users (superuser only) |

Older URLs — `/dashboard`, `/hosts/discover`, `/hosts/pending`, `/schedules`, `/action-packs`, `/groups/[id]/rules` and the other per-module pages — redirect to their new home.

## Build

```bash
npm run build    # static export to out/, served by the backend
```

See the [root README](../README.md) for full project documentation and [FRONTEND.md](FRONTEND.md) for design patterns and conventions.
