<!--
Release PR (dev → main). Open with:
  gh pr create --base main --head dev --title "Release vX.Y.Z" --body-file ...
or in the browser append ?template=release.md to the compare URL.

For a normal feature/fix PR into dev, do NOT use this template.
-->

## Release vX.Y.Z

### Pre-merge checklist

Tick every box before merging. CI runs the full suite on this PR, but
the items below are the human-owned ones CI cannot verify.

- [ ] **`VERSION` bumped** — one line, no `v` prefix, valid semver
- [ ] **`CHANGELOG.md`** — `[Unreleased]` renamed to `[X.Y.Z] — YYYY-MM-DD`, a fresh empty `[Unreleased]` added above it, and the comparison-link refs at the bottom updated (`vPREV...vX.Y.Z` and `vX.Y.Z...HEAD`)
- [ ] **`docs/` updated for every user-visible feature in this release** — new modules, API surface, UI flows, settings. (This should already be true per-feature PR; this box is the backstop.)
- [ ] **`TODO.md`** — items completed this cycle removed
- [ ] **`BUGS.md`** — bugs fixed this cycle deleted (the fixing commit references the bug ID)
- [ ] **`plans/` is absent** — it must never ship on `dev` or `main`
- [ ] **CI is green on this PR** — `version-check`, lint, `backend-test`, `frontend-build-check`, `docs-build-check`, `build-test-image`, trivy

> **Do not hand-bump** `backend/pyproject.toml` or `frontend/package.json`.
> The `release-artifacts` job syncs both from `VERSION` on merge, then
> builds the artifacts, tags `vX.Y.Z`, and cuts the GitHub Release.

### Highlights

<!-- Bullet the headline changes; release notes are auto-generated from commits. -->
-
