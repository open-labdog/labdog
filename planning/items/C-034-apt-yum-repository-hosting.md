# C-034: APT/YUM repository hosting

| | |
|---|---|
| Edition | community |
| Source | roadmap, board item "APT/YUM repository hosting" |
| Tier / score | P3 / 2.0 |
| Stage | parked |
| Effort | L |
| Autonomy | H |
| Depends on | none |

## Goal
An operator can serve APT and YUM package repositories from the product, next to the package module that already points hosts at repositories.

## Why it is parked
This needs a product decision before any design. `CONTRIBUTING.md` (lines 19 to 25) says the product would rather integrate with adjacent tools than replace them, and running a repository server (storage, mirroring, signing keys, serving large files) is a different kind of service from pushing configuration over SSH. The board note is one line and no code exists.

## What would un-park it
- Dennis decides that repository hosting is in scope, and logs it in `DECISIONS.md`. Until then this item is blocked.
- A concrete need is named: mirroring upstream repositories for hosts with no internet access, or publishing in-house packages. The two lead to different designs.

## Context
- No code exists for this (searched the backend and both pack repositories for a repository server).
- The package module manages which repositories a host uses, not their content: `backend/app/packages/` (`models.py`, `merge.py`, `generator.py`, `collector.py`, `diff.py`), with repositories exported through `package_repo_specs_to_yaml` in `backend/app/gitops/serializer.py` (line 240).
- Alternatives that fit the stated scope: an action pack that installs and configures an existing tool (aptly, reprepro, Pulp, apt-cacher-ng) on a managed host, with the package module pointing hosts at it.

## Assumptions
- Assumption (not yet confirmed): if the answer is yes, the first version is an action pack that drives an existing repository tool, not a server built into the backend. Question for Dennis: in scope at all, and if so, mirror or publish?
- Assumption (not yet confirmed): signing keys for a hosted repository would be a new kind of secret and need their own design.
