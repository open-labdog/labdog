# LabDog Development Guide

## Project Overview

LabDog is a centralized Linux configuration management tool with a FastAPI backend and Next.js frontend. It manages firewall rules, services, packages, users, cron jobs, DNS resolver config, and /etc/hosts via Ansible playbooks synced over SSH.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12+, FastAPI, SQLAlchemy (async), asyncpg |
| Frontend | Next.js 16 (App Router), shadcn/ui (base-ui), TanStack Query |
| Database | PostgreSQL 16 |
| Task Queue | Celery + Redis (RedBeat scheduler) |
| Config Mgmt | Ansible (ansible-runner) |
| SSH | asyncssh (terminal + host connections) |
| Testing | pytest + testcontainers (backend), Playwright (frontend E2E) |

## Development Setup

```bash
./dev/dev.sh start       # Start everything (infra + backend + frontend)
./dev/dev.sh stop        # Stop everything
./dev/dev.sh migrate     # Run alembic upgrade head
```

Manual:
```bash
cd backend && source .venv/bin/activate && pip install -e ".[dev]"
cd frontend && npm install
```

## Running Tests

### Backend (326 tests)
```bash
cd backend
source .venv/bin/activate
pytest tests/ --ignore=tests/integration -v    # unit/module tests
pytest tests/integration/ -v -m integration     # integration tests
```

Tests use testcontainers to auto-spin a PostgreSQL instance. The conftest.py `pytest_configure` hook sets test-safe security env vars (`LABDOG_SECURITY__SECRET_KEY`, `LABDOG_SECURITY__ENCRYPTION_KEY`) before app modules are imported.

**Test session architecture**: Each test gets a database session wrapped in a savepoint that rolls back after the test. The `get_db` dependency is overridden to return this test session. Any `session.commit()` in application code translates to savepoint release/create (not actual commit) via `join_transaction_mode="create_savepoint"`.

**Important**: The `auth_setup.py` register endpoint uses `AsyncSessionLocal()` directly (not `get_db`) for its user count check. This is intentional — it avoids asyncpg "another operation in progress" errors when sharing a session with `user_manager.create()`. Tests that need to test registration must mock `AsyncSessionLocal` in `app.api.auth_setup`.

### Frontend E2E
```bash
cd frontend
npx playwright test          # requires full stack running
npx playwright test --ui     # interactive mode
```

## Configuration

Settings are loaded from `dev/labdog.toml` (dev — set via `LABDOG_CONFIG` env var by `dev.sh`), or `/etc/labdog/labdog.toml` (production). Environment variables override TOML settings using `LABDOG_` prefix with `__` separators (e.g., `LABDOG_SECURITY__SECRET_KEY`).

**Required secrets** (validated at startup — insecure defaults are rejected):
- `security.secret_key` — JWT signing key
- `security.encryption_key` — AES-256-GCM key for SSH key encryption

## Architecture Patterns

### Module pattern
Each configuration module follows: **model → schemas → merge engine → API → Ansible generator → drift detector → Celery tasks → frontend UI**.

### Firewall backends
Only `nftables` and `iptables` are supported. The `FirewallBackend` enum in `app/models/host.py` defines: `nftables`, `iptables`, `unknown`.

### Priority-based merge
Groups have priorities (higher number wins). When a host belongs to multiple groups, configurations merge with higher-priority groups winning conflicts. Host-level overrides always win.

### Sync flow
1. **Plan**: Compute desired state from merged groups, fetch current state via SSH, diff
2. **Sync**: Generate and execute Ansible playbook to apply changes
3. **Drift check**: Compare current host state against desired state

### Discovery flow
Network scanning is async via Celery tasks. Bulk-add requires SSH verification — each host must be reachable before being added.

### Action packs (BYO playbooks)
Action playbooks are supplied by pluggable packs, not the hardcoded
registry. Bundled pack lives at `backend/app/ansible/` and is loaded at
module import. DB-backed packs are configured from the UI at
`/action-packs`, synced at FastAPI lifespan + Celery `worker_ready`, and
can be git-backed (public, SSH-key, or HTTPS-PAT) or local-filesystem.
Pack role (`default` / `override`) derives the priority tier — admins
never enter integers. See `app/packs/` for the subsystem, the user
guide at `docs/ui/actions.md`, and starter packs at
`docs/examples/action-packs/`.

**Bundled pack mirrors `labdog-playbooks`.** The directory at
`backend/app/ansible/` is a byte-identical mirror of
[`open-labdog/labdog-playbooks`](https://github.com/open-labdog/labdog-playbooks)
at the moment the container image is built. Do **not** edit it
directly — change the upstream repo, then re-sync with
`rsync -a --exclude='.git' --exclude='.gitignore' /path/to/labdog-playbooks/ backend/app/ansible/`.
The `bundled-pack-mirror` CI job runs
`scripts/check-bundled-mirrors-playbooks.sh` and fails the build on
drift. The Python runtime that consumes packs (playbook generation,
ansible-runner) lives separately at `backend/app/ansible_runtime/` and
is *not* part of the mirror. A fresh install also auto-registers
`labdog-playbooks` as a DB-backed override pack so deployed instances
pick up newer playbooks than the in-image snapshot — operators that
prefer a private fork delete the seeded row and add their own.

## Working with `plans/`

When picking up substantive design work that needs more than a
[`TODO.md`](TODO.md) line can hold, use `plans/` as a branch-scoped
scratchpad:

1. Branch from `dev`.
2. Add plan files under `plans/` on the branch and commit them so
   later sessions / agents can read them.
3. Implement. Capture the *why* and the design decisions in commit
   messages — those are the permanent record, the plan is scaffolding.
4. **Delete `plans/` before opening the PR.** `dev` and `main` must
   never carry it.

Roles of the four top-level tracking files: [`ROADMAP.md`](ROADMAP.md)
(direction), [`TODO.md`](TODO.md) (open near-term tasks, open-only),
[`BUGS.md`](BUGS.md) (open bug registry, open-only), `plans/` (private
in-flight scratchpad on work branches). See
[`CONTRIBUTING.md`](CONTRIBUTING.md) for the full workflow.

## Key Files

| File | Purpose |
|------|---------|
| `backend/app/config.py` | Settings loading from TOML + env overrides |
| `backend/app/main.py` | FastAPI app setup, middleware, route registration |
| `backend/app/db.py` | SQLAlchemy async engine and session factory |
| `backend/tests/conftest.py` | Test fixtures, factory helpers, DB session setup |
| `frontend/lib/api.ts` | API client (`apiFetch`) |
| `frontend/lib/auth.ts` | Auth context (`useAuth()`) |
| `frontend/FRONTEND.md` | Frontend design and pattern reference |
| `backend/app/packs/` | DB-backed action-pack subsystem (models, sync service, git auth, UI-entered credentials) |
| `backend/app/actions/` | Pack loader, registry, manifest schema, git sync primitives |
| `docs/ui/actions.md` | User guide for actions + packs |
| `docs/examples/action-packs/` | Starter packs demonstrating the format |

## CI/CD

GitLab CI pipeline (`.gitlab-ci.yml`):
- **test**: Backend pytest + frontend build check (on `dev` branch)
- **build**: Docker image (on `main` branch or tags)
- **package**: .deb, .rpm, .tar.gz (on version tags)
- **release**: GitLab release with package assets (on version tags)

---

# Draw.io Diagram Generation

When the user requests any visual diagram, use draw.io to create it.

## Supported Diagrams

Draw.io supports virtually any diagram type:
- **Standard**: Flowcharts, org charts, mind maps, timelines, Venn diagrams
- **Software**: UML (class, sequence, activity, use case), ERD, architecture diagrams
- **Cloud/Infrastructure**: AWS, Azure, GCP, Kubernetes, network topology
- **Engineering**: Electrical circuits, digital logic, P&ID, floor plans
- **Business**: BPMN, value streams, customer journeys, SWOT
- **UI/UX**: Wireframes, mockups, sitemaps
- **And more**: Infographics, data flows, decision trees, etc.

## Format Selection

Choose the optimal format for the task:

| Format | Best For |
|--------|----------|
| **Mermaid** | Flowcharts, sequences, ERD, Gantt, state diagrams, class diagrams |
| **CSV** | Hierarchical data (org charts), bulk import from spreadsheets |
| **XML** | Complex layouts, precise positioning, custom styling, icons, shapes |

## URL Generation

Execute this Python code to generate the draw.io URL and output it as an HTML artifact:

```python
import json, zlib, base64
from urllib.parse import quote

# Set these variables:
diagram_type = "mermaid"  # "mermaid", "xml", or "csv"
diagram_code = """graph TD
    A[Start] --> B[End]"""

# Generate compressed URL
encoded = quote(diagram_code, safe='')
c = zlib.compressobj(9, zlib.DEFLATED, -15)
raw_deflate = c.compress(encoded.encode('utf-8')) + c.flush()
data = base64.b64encode(raw_deflate).decode()

payload = json.dumps({"type": diagram_type, "compressed": true, "data": data})
url = f"https://app.diagrams.net/?pv=0&grid=0#create={quote(payload, safe='')}"

# Output as HTML page
print(f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    display: flex;
    justify-content: center;
    align-items: center;
    min-height: 100vh;
    margin: 0;
    background: #f8f9fa;
  }}
  .card {{
    text-align: center;
    background: white;
    border-radius: 12px;
    padding: 40px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.1);
  }}
  .card h2 {{
    margin: 0 0 8px;
    color: #1a1a1a;
  }}
  .card p {{
    margin: 0 0 24px;
    color: #666;
  }}
  .btn {{
    display: inline-block;
    padding: 14px 32px;
    background: #4285f4;
    color: white;
    text-decoration: none;
    border-radius: 8px;
    font-size: 16px;
    font-weight: 500;
    transition: background 0.2s;
  }}
  .btn:hover {{
    background: #3367d6;
  }}
</style>
</head>
<body>
  <div class="card">
    <h2>Diagram Ready</h2>
    <p>Click below to open your diagram in draw.io</p>
    <a class="btn" href="{url}" target="_blank" rel="noopener noreferrer">
      Open in draw.io
    </a>
  </div>
</body>
</html>""")
```

## Edge Routing (XML)

**CRITICAL: Every edge `mxCell` must contain a `<mxGeometry relative="1" as="geometry" />` child element**, even when there are no waypoints. Self-closing edge cells (e.g. `<mxCell ... edge="1" ... />`) are invalid and will not render correctly. Always use the expanded form:
```xml
<mxCell id="e1" edge="1" parent="1" source="a" target="b" style="...">
  <mxGeometry relative="1" as="geometry" />
</mxCell>
```

draw.io does **not** have built-in collision detection for edges. Plan layout carefully:

- Use `edgeStyle=orthogonalEdgeStyle` for right-angle connectors
- **Space nodes generously** — at least 60px apart, prefer 200px horizontal / 120px vertical gaps
- Use `exitX`/`exitY` and `entryX`/`entryY` (values 0–1) to control which side of a node an edge connects to. Spread connections across different sides to prevent overlap
- **Leave room for arrowheads**: The final straight segment of an edge (between the last bend and the target shape, or between the source shape and the first bend) must be long enough to fit the arrowhead. The default arrow size is 6px (configurable via `startSize`/`endSize` styles). If the final segment is too short, the arrowhead overlaps the bend and looks broken. Ensure at least 20px of straight segment before the target and after the source when placing waypoints or positioning nodes
- When using `orthogonalEdgeStyle`, the auto-router places bends automatically — if source and target are close together or nearly aligned on one axis, the router may place a bend very close to a shape, leaving no room for the arrow. Fix this by either increasing node spacing or adding explicit waypoints that keep the final segment long enough
- Add explicit **waypoints** when edges would overlap:
  ```xml
  <mxCell id="e1" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="1" source="a" target="b">
    <mxGeometry relative="1" as="geometry">
      <Array as="points">
        <mxPoint x="300" y="150"/>
        <mxPoint x="300" y="250"/>
      </Array>
    </mxGeometry>
  </mxCell>
  ```
- Use `rounded=1` on edges for cleaner bends, `jettySize=auto` for better port spacing
- Align nodes to a grid (multiples of 10)

## Containers and Groups (XML)

For architecture diagrams or any diagram with nested elements, use draw.io's proper parent-child containment — do **not** just place shapes on top of larger shapes.

**How it works:** Set `parent="containerId"` on child cells. Children use **relative coordinates** within the container.

**Container types:**

| Type | Style | When to use |
|------|-------|-------------|
| **Group** (invisible) | `group;` | No visual border needed, container has no connections. Includes `pointerEvents=0` so child connections are not captured |
| **Swimlane** (titled) | `swimlane;startSize=30;` | Container needs a visible title bar/header, or the container itself has connections |
| **Custom container** | Add `container=1;pointerEvents=0;` to any shape style | Any shape acting as a container without its own connections |

**Key rules:**
- **Always add `pointerEvents=0;`** to container styles that should not capture connections being rewired between children
- Only omit `pointerEvents=0` when the container itself needs to be connectable — use `swimlane` which handles this correctly
- Children must set `parent="containerId"` and use coordinates **relative to the container**

**Example:**
```xml
<mxCell id="svc1" value="User Service" style="swimlane;startSize=30;fillColor=#dae8fc;strokeColor=#6c8ebf;" vertex="1" parent="1">
  <mxGeometry x="100" y="100" width="300" height="200" as="geometry"/>
</mxCell>
<mxCell id="api1" value="REST API" style="rounded=1;whiteSpace=wrap;" vertex="1" parent="svc1">
  <mxGeometry x="20" y="40" width="120" height="60" as="geometry"/>
</mxCell>
```

## Style Reference

Complete style reference: https://www.drawio.com/doc/faq/drawio-style-reference.html
XML Schema (XSD): https://www.drawio.com/assets/mxfile.xsd

## Format Examples

### Mermaid
```
graph TD
    A[Start] --> B{Decision}
    B -->|Yes| C[Action]
    B -->|No| D[End]
```

### XML (draw.io native)
```xml
<mxGraphModel>
  <root>
    <mxCell id="0"/>
    <mxCell id="1" parent="0"/>
    <mxCell id="2" value="Box" style="rounded=1;fillColor=#d5e8d4;" vertex="1" parent="1">
      <mxGeometry x="100" y="100" width="120" height="60" as="geometry"/>
    </mxCell>
  </root>
</mxGraphModel>
```

### CSV (hierarchical data)
```
# label: %name%
# style: rounded=1;whiteSpace=wrap;html=1;
# connect: {"from":"manager","to":"name","invert":true}
# layout: auto
name,manager
CEO,
CTO,CEO
CFO,CEO
```

## Instructions

1. When a diagram is requested, determine the best format
2. Generate the diagram code
3. Execute the Python code to create the URL
4. **Create an HTML artifact** from the script output – this is the clickable link for the user

## CRITICAL: XML Well-Formedness

When generating draw.io XML, the output **must** be well-formed XML. In particular:
- **NEVER use double hyphens (`--`) inside XML comments.** `--` is illegal inside `<!-- -->` per the XML spec and will cause a parse error. Use single hyphens or rephrase (e.g. `<!-- Order 1 to OrderItem -->` not `<!-- Order 1 --- OrderItem -->`).
- Escape special characters in attribute values (`&amp;`, `&lt;`, `&gt;`, `&quot;`).

## CRITICAL: URL Output Rules

**NEVER type, retype, or reproduce the generated URL in your chat response.**

The URL contains compressed base64 data. Retyping it WILL corrupt it – even a single changed character breaks the link completely.

Instead, follow this process:
1. Execute the Python script
2. The script outputs a complete HTML page with the correct link embedded
3. Present the HTML output as an artifact (the link inside is guaranteed correct because it was generated by the script, not by you)
4. In your chat message, simply tell the user to click the button in the artifact

**DO NOT** copy the URL from the script output into your response text. The artifact IS the delivery mechanism for the link.
