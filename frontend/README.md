# LabDog Frontend

Next.js 16 web UI for LabDog — centralized Linux configuration management.

## Stack

- Next.js 16 (App Router)
- React 19
- shadcn/ui (base-ui variant) + Tailwind CSS v4
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
npx playwright test          # requires full Docker stack running
npx playwright test --ui     # interactive test runner
```

15 E2E spec files covering auth, dashboard, groups, hosts, rules, SSH terminal, sync, audit, and UX patterns.

## Pages

### Auth (no sidebar)
| Route | Description |
|-------|-------------|
| `/login` | Login page |
| `/register` | First-user registration page |

### Dashboard (with sidebar)
| Route | Description |
|-------|-------------|
| `/dashboard` | Overview dashboard |
| `/groups` | Host group management (supports category view toggle) |
| `/groups/new` | Create new group |
| `/groups/[id]` | Group detail (overview tab) |
| `/groups/[id]/rules` | Firewall rule management per group |
| `/groups/[id]/services` | Service management per group |
| `/groups/[id]/hosts-entries` | /etc/hosts entries per group |
| `/groups/[id]/packages` | Package management per group |
| `/groups/[id]/users` | Linux user management per group |
| `/groups/[id]/cron-jobs` | Cron job management per group |
| `/groups/[id]/resolver` | DNS resolver config per group |
| `/groups/[id]/sync` | Sync with plan preview |
| `/groups/[id]/workflow` | Proxmox workflow execution |
| `/hosts` | Host management |
| `/hosts/new` | Add host manually |
| `/hosts/discover` | Network discovery and bulk-add |
| `/hosts/[id]` | Host detail, drift status, module overrides |
| `/hosts/[id]/terminal` | Full-page SSH terminal (web shell) |
| `/hypervisors` | Proxmox hypervisor management |
| `/ssh-keys` | SSH key management |
| `/git-repos` | GitOps repository connections |
| `/schedules` | Cron schedule management |
| `/users` | User management (superuser only) |
| `/settings` | Application settings |
| `/audit` | Audit log viewer |

## Build

```bash
npm run build    # production build (standalone output)
```

See the [root README](../README.md) for full project documentation and [FRONTEND.md](FRONTEND.md) for design patterns and conventions.
