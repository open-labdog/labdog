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
  before you invest significant time. See
  [`plans/TODO.md`](plans/TODO.md) for what's already on the
  roadmap.

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
- If the change touches anything in [`plans/TODO.md`](plans/TODO.md)
  — complete the item or un-check it with an explanation.

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
it with the maintainer privately
(dennis.tyresson@norce.io).

## Release process

Maintainer-only. Short form:

1. Update [`CHANGELOG.md`](CHANGELOG.md): rename `[Unreleased]` to
   `[X.Y.Z] — YYYY-MM-DD`, add a fresh empty `[Unreleased]` block
   above it, and update the comparison link refs at the bottom.
   This is what populates the GitHub Release body — without it the
   release page renders blank.
2. Commit the changelog on `main`. **Do not hand-bump
   `backend/pyproject.toml` or `frontend/package.json`** — the
   `release-artifacts` CI job rewrites both files from the tag
   before building, so `labdog --version` and the .deb/.rpm
   filenames stay in lockstep automatically.
3. Tag: `git tag vX.Y.Z && git push origin vX.Y.Z`.
4. The `release-artifacts` workflow job syncs the version files,
   builds `.tar.gz`, `.deb`, `.rpm`, and `SHA256SUMS` via
   `./packaging/build.sh`, and attaches them to the auto-created
   Release via `softprops/action-gh-release`.
5. To smoke-test the packaging path without cutting a real
   release, run the workflow manually (**Actions → CI → Run
   workflow**). The same job builds artifacts with a dev-flavoured
   version (`0.0.0-dev.<short-sha>`) and uploads them to the
   workflow-run artifacts for download, but skips both the
   version-sync and release-create steps.
