# Barricade Documentation

Guides and reference material for running Barricade. For project-level
orientation (features, install, quick-start, API surface) see the
[top-level README](../README.md).

## Contents

| Section | What it covers |
|---|---|
| [examples/gitops/](./examples/gitops/README.md) | End-to-end GitOps guide — webhook setup, YAML schema reference for every module, missing-section semantics, idempotency rules, error taxonomy, mutation-lock behaviour, multi-group repo layouts, break-glass recovery. |
| [examples/gitops/minimal.yaml](./examples/gitops/minimal.yaml) | Smallest valid group YAML — a starting template. |
| [examples/gitops/web-servers.yaml](./examples/gitops/web-servers.yaml) | Realistic web-tier example covering all seven modules. |
| [examples/gitops/database.yaml](./examples/gitops/database.yaml) | Realistic PostgreSQL-tier example with a declared apt repository, per-user cron backups, and different resolver backend. |
| [examples/gitops/modules/](./examples/gitops/modules/) | One focused YAML per module (firewall, services, packages, hosts-entries, cron-jobs, resolver, users) with every field annotated and edge cases demonstrated. |
| [examples/precedence/](./examples/precedence/README.md) | How Barricade merges group-level and host-level configurations when a host belongs to multiple groups. Worked examples for every module. |

## Where to start

- **Setting up GitOps for the first time?** → [examples/gitops/README.md](./examples/gitops/README.md)
- **Looking for a specific YAML field?** → the matching file in [examples/gitops/modules/](./examples/gitops/modules/)
- **Trying to reason about multi-group hosts?** → [examples/precedence/README.md](./examples/precedence/README.md)

## Authoritative sources

The YAML examples in this tree parse cleanly against the live Pydantic
schema; the schema itself lives in
[`backend/app/gitops/schema.py`](../backend/app/gitops/schema.py) and is the
source of truth. Per-module handlers are in
[`backend/app/gitops/importers/`](../backend/app/gitops/importers/).
