# LabDog docs site

Docusaurus wrapper around the [`docs/`](../docs) tree. The docs content
lives at the repo root — this directory only holds the tooling (config,
theme overrides, build pipeline).

## Local development

```bash
cd website
npm ci          # install dependencies
npm start       # dev server at http://localhost:3000 with hot reload
```

## Production build

```bash
cd website
npm run build   # static output in website/build/
npm run serve   # preview the built site locally
```

`npm run build` fails on broken internal links
(`onBrokenLinks: 'throw'`), so it doubles as a link-validity check and
runs in CI as `docs-build-check`.

## How content is wired

`docusaurus.config.ts` points `docs.path` at `../docs`, so markdown
files in the repo-root `docs/` tree are the site's content. The
sidebar is auto-generated from the folder structure
([`sidebars.ts`](./sidebars.ts)). Adding a new markdown file anywhere
under `../docs/` adds it to the nav on the next build.

YAML examples under `../docs/examples/gitops/` are copied into the
build by Docusaurus's markdown loader (as hashed assets in
`build/assets/files/`) and linked from rendered pages automatically.
No manual copy step.

## Deployment

- **GitHub Pages:** the `docs-deploy` job in
  [`../.github/workflows/ci.yml`](../.github/workflows/ci.yml) builds
  this directory and publishes to GitHub Pages on every push. The
  `docs-build-check` job runs on every PR as a link-validity gate.
- **GitLab Pages (legacy):** the `pages` job in
  [`../.gitlab-ci.yml`](../.gitlab-ci.yml) is kept while the GitLab
  pipeline lingers; will be removed alongside `.gitlab-ci.yml` once
  the migration window closes (see `TODO.md`).

## Updating the docs

Edit the markdown under [`../docs/`](../docs). No website changes
needed unless you're adjusting the theme, nav, or adding plugins.
