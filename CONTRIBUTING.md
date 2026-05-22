# Contributing to LabDog

Thanks for the interest. LabDog is AGPL-3.0-or-later; contributions
land under the same license (inbound = outbound).

## Reporting issues

- **Security vulnerabilities** — see [SECURITY.md](SECURITY.md). Do
  not file them as public issues.
- **Bugs** — open a GitHub issue with reproduction steps, the
  version/commit you saw it on, and the relevant logs. If you can
  share the exact SQL state + API response, even better.
- **Feature ideas** — open an issue first so we can discuss scope
  before you invest significant time. See [`TODO.md`](TODO.md) for
  what's already on the roadmap and [`BUGS.md`](BUGS.md) for the
  open bug registry.

## Dev setup

Everything you need is in [`docs/README.md`](docs/README.md) and
[`CLAUDE.md`](CLAUDE.md). Short version:

```bash
./dev/dev.sh start     # postgres + redis + backend + frontend
./dev/dev.sh migrate   # alembic upgrade head
./dev/dev.sh stop
```

The backend uses Python 3.12+; the frontend uses Node 20+. Tests
spin up Postgres via testcontainers, so you need Docker available.

## Running tests

```bash
# Backend — runs in ~40s
cd backend && source .venv/bin/activate
pytest tests/ --ignore=tests/integration -q

# Backend integration — requires Docker
pytest tests/integration -m integration

# Frontend type-check + lint
cd frontend
npx tsc --noEmit
npm run lint

# Frontend E2E (Playwright) — requires the full stack running
npx playwright test
```

CI runs all of the above on every PR; expect it to fail if you skip
them locally.

## Code style

- **Python** — `ruff check` + `ruff format` (CI gate). Type hints
  on public functions; `from __future__ import annotations` at the
  top of every new module. `Mapped[...]` SQLAlchemy columns, not
  the legacy `Column(...)` style.
- **TypeScript** — strict mode already enabled. `npx tsc --noEmit`
  must be clean. Prefer function components, TanStack Query for
  fetching, `useApiMutation` for writes.
- **Docs** — Markdown wrapped at ~72 characters where practical.
  Tables and headings readable in plain text terminals.

## Commit messages

Conventional Commits with a scope, matching existing history:

```
feat(actions): Pack-supplied verify playbook hook
fix(hosts): Defensive load so one bad row doesn't blank the UI
docs(plans): Add pre-release checklist
refactor(action-packs): Consolidate git config onto GitRepository
```

Body explains **why**, not what. If the diff is substantial, include
the decision points and what was considered and rejected.

## Pull requests

- Branch from `dev`. `main` is for releases only.
- One logical change per PR. "Consolidate X + fix Y + rename Z"
  gets split.
- Update or add tests for behavioural changes. New backend code
  without tests won't merge.
- Update `docs/` when you change user-visible behaviour. A comment
  on the PR is not a substitute for updating the user guide.
- If the change touches anything in [`TODO.md`](TODO.md) or fixes a
  bug listed in [`BUGS.md`](BUGS.md) — tick the item (`[x]`), note
  the fix and commit hash, or un-check it with an explanation.

### Working with `plans/` — branch-scoped scratchpad

For substantive design work that needs more than a TODO entry can
hold, use a temporary `plans/` directory on the work branch. The
workflow:

1. **Branch from `dev`** — `feat/<topic>`, `fix/<bug-id>`, etc.
2. **Drop plan files into `plans/`** on that branch and commit them.
   Other sessions, collaborators, or agents picking up the branch
   need to read them.
3. **Implement.** Each commit message captures the *why* and the
   decisions taken — including ones from the plan that aren't
   obvious from the diff. The commit log is the permanent record;
   the plan is scaffolding.
4. **Delete `plans/` before opening the PR.** `dev` and `main` must
   never carry it.
5. **Merge.** Reviewers read commits, not plan files.

Roles of the four top-level tracking files:

| File | Purpose |
|---|---|
| [`ROADMAP.md`](ROADMAP.md) | High-altitude direction — in design, ideas, out of scope. |
| [`TODO.md`](TODO.md) | Open near-term tasks. Open-only — finished items are deleted, not ticked. |
| [`BUGS.md`](BUGS.md) | Open bug registry. Open-only — fixed bugs are deleted, with a commit referencing the bug ID. |
| `plans/` (work branches) | Private scratchpad for one piece of in-flight work. Created and deleted within a single branch's lifetime. |

## Licensing & copyright

You retain copyright in your contributions. By submitting a PR you
agree your contribution is licensed under AGPL-3.0-or-later — same
as the rest of the project. We don't use a CLA; GitHub's default
"inbound = outbound" model applies.

Per-file AGPL headers are **not** required. The repository-level
[LICENSE](LICENSE) covers the whole tree. Don't add headers to new
files unless the file is a standalone utility that might be copied
out of the repo on its own.

## Code of conduct

Be decent. Disagree with ideas, not people. Harassment in issues,
PRs, or project spaces gets you blocked. If something's off, raise
it with the maintainers via GitHub's private vulnerability
reporting (Security → Advisories → Report a vulnerability) or by
opening a confidential discussion.

## Release process

Maintainer-only. Releases are triggered by **merging a release PR
into `main`**; there's no separate tag-push step. The flow:

1. On `dev`, edit the [`VERSION`](VERSION) file at the repo root to
   the new version (e.g. `0.2.0`). One line, no `v` prefix.
2. Update [`CHANGELOG.md`](CHANGELOG.md) on `dev`: rename
   `[Unreleased]` to `[X.Y.Z] — YYYY-MM-DD`, add a fresh empty
   `[Unreleased]` block above it, and update the comparison link
   refs at the bottom. The `softprops/action-gh-release` step
   currently uses `generate_release_notes: true` (auto-derives from
   commits), but a curated CHANGELOG is the canonical history.
3. Open a PR `dev` → `main`. The `version-check` job blocks the
   merge unless `VERSION` is bumped relative to `main`, is valid
   semver, and the resulting `vX.Y.Z` tag does not already exist
   upstream.
4. Merge the PR. The merge to `main` triggers `release-artifacts`,
   which:
   - syncs `backend/pyproject.toml` and `frontend/package.json`
     from `VERSION` (so `/api/version` and the package filenames
     stay in lockstep — **do not hand-bump** these files),
   - runs `./packaging/build.sh` to produce `.tar.gz` / `.deb` /
     `.rpm` / `SHA256SUMS`,
   - tags the merge commit `vX.Y.Z` and pushes the tag,
   - creates a GitHub Release at that tag with the four files
     attached and auto-generated release notes.

The job is idempotent on tag presence: if `vX.Y.Z` already exists
(e.g. you re-ran a previous merge's workflow), it skips with a
notice instead of double-releasing.

To smoke-test the packaging path without cutting a real release,
run `./packaging/build.sh` locally — there is no CI dry-run mode.
