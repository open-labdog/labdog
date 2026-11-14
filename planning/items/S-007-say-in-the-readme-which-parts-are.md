# S-007: Say in the README which parts are AGPL and which are commercial

| | |
|---|---|
| Edition | shared |
| Source | roadmap, open point of the board item "Split into a Community (AGPL) repository and an Enterprise repository" |
| Tier / score | P0 / 11.0 |
| Stage | 3 |
| Effort | S |
| Autonomy | B |
| Depends on | none |

## Goal

Someone reading the README knows that everything in this repository is AGPL, that a
separate commercial Enterprise edition exists and is not in this repository, and which
kinds of feature belong to which.

## Scope

In scope:
- Rewriting the "License" section of `README.md` and adding a short "Editions" section.
- The same statement, in two sentences, on the docs landing page.

Out of scope:
- Any licence text, licence change or new licence file. `LICENSE` is not touched.
- Prices, terms, contact or sales wording for the Enterprise edition.
- A feature comparison table that promises dates. Customer-facing Enterprise feature
  names may be listed with a no-commitment line ([D-18](../DECISIONS.md)).
- The contributor licence agreement (S-011) and the copyright line (S-012).

## Context

- `README.md`, section "License" (line 130): "AGPL-3.0-or-later. See LICENSE. The
  network-use clause means anyone running a modified version of LabDog as a service must
  make the source of their modifications available to its users. Private internal
  deployments without modifications carry no extra obligations." Line 134 has the
  copyright line. The README does not mention an Enterprise edition anywhere
  (`grep -n "Enterprise" README.md` prints nothing).
- The licence badge at line 11 says `AGPL-3.0-or-later`.
- `CLAUDE.md`, section "Extensions (plugins) and directory sign-in", already states the
  technical fact: "The Enterprise edition is a separate package that plugs into this
  one; Community ships no Enterprise code."
- The board item lists what stays in Community: running your own playbooks with a
  preview per server; drift detection and enforcement; Git integration, webhooks and
  config as code; scheduled actions; snapshot, apply, verify and rollback on Proxmox;
  basic rollback safety; the append-only audit log in the interface; local accounts with
  fixed roles (admin, operator, viewer); the AI assistant with default-deny rules,
  removable; the REST API and command-line tool; alert intake.
- `planning/PLAN.md` carries the public list of Enterprise feature titles under D-18.
  That list, and nothing else, is the source for Enterprise names in the README.
- `GET /api/version` returns `license: "AGPL-3.0-or-later"` and the Settings page shows
  it; that stays correct for Community.
- The docs landing page is `docs/README.md`.
- After C-051 the README says unbork where it said LabDog. Write this item on top of
  C-051, or use the new name directly if C-051 has not merged.

## Dependencies

- None on other items. It must be merged before C-002 opens the release PR.
- Dennis reviews the wording before merge (class B): it is a public statement about
  licensing.

## Implementation steps

1. Add a section "Editions" to `README.md`, directly above "License", with:
   - **unbork Community**: this repository, AGPL-3.0-or-later, complete and usable on its
     own. List the feature areas from the board item in one short bullet list, each
     checked against the current README feature list so that nothing is named that does
     not exist yet (for example the command-line tool: name it only if it has shipped).
   - **unbork Enterprise**: a separate package under a commercial licence, developed in
     a separate private repository. It installs next to Community and adds to it through
     a documented extension interface; it does not replace or modify Community code. Name
     the feature areas that already exist (single sign-on through a company directory,
     and role-based access control scoped to hosts and groups), then one line that
     further Enterprise features are planned with no dates or commitments, linking to
     `planning/PLAN.md`.
   - One sentence: no Community feature is removed or limited to make room for
     Enterprise, and the extension interface is part of the AGPL code.
2. Rewrite "License" to say:
   - Everything in this repository, including the extension interface, the docs and the
     packaging, is AGPL-3.0-or-later. Keep the existing explanation of the network-use
     clause, with the product name corrected.
   - The Enterprise edition is not covered by this licence and none of its code is here.
   - Keep the copyright line as it is.
3. Add two sentences with the same meaning under the introduction of `docs/README.md`,
   linking to the README section.
4. Check consistency: `grep -rn -i "enterprise" README.md docs/ CONTRIBUTING.md` and make
   sure no page contradicts the new section. Do not edit `CONTRIBUTING.md`'s licensing
   part, which belongs to S-011.
5. Send the diff to Dennis for the wording review. Apply his changes verbatim.

## Acceptance criteria

- `README.md` has an "Editions" section and a "License" section that together state:
  this repository is AGPL-3.0-or-later in full; Enterprise is separate, commercial and
  not in this repository.
- `grep -n "LabDog" README.md` finds nothing in those two sections.
- Every Community feature named in "Editions" exists in the release candidate.
- The sections contain no price, no date, no licence terms for Enterprise and no
  internal item id.
- `LICENSE` is byte-identical to before (`git diff --stat` shows no change to it).
- The docs site builds: `cd website && npm ci && npm run build`.
- Dennis has approved the wording, recorded as a review on the PR.

## Test plan

- `cd website && npm ci && npm run build` for links and Markdown.
- Read the rendered README on the PR page.
- No backend or frontend test is affected.

## Docs and changelog

- `README.md` and `docs/README.md` as above.
- `CHANGELOG.md`, `[Unreleased]`, under "Changed": "The README says which parts are AGPL
  (everything in this repository) and that the Enterprise edition is a separate
  commercial package."

## Risks and rollback

- A sentence that reads as a licence grant or a promise about Enterprise could bind the
  project. Keep to statements of fact, and let Dennis approve them.
- Listing a Community feature that later moves would break trust. List areas, not
  fine-grained features.
- Rollback: revert the commit.

## Assumptions

- [D-18](../DECISIONS.md): Enterprise feature titles may appear publicly with a
  no-commitment line; scores, specs and internal items stay private.
- Assumption (not yet confirmed): "commercial licence" is the right public wording for
  the Enterprise edition's terms. Question for Dennis: is there a contact address or
  page the README should link to, or none for now?
- Assumption (not yet confirmed): the extension interface stays AGPL like the rest of
  Community, and saying so is wanted.

## Definition of done

- [ ] "Editions" and "License" sections rewritten; `docs/README.md` updated.
- [ ] Docs build passes; `LICENSE` untouched.
- [ ] Wording approved by Dennis.
- [ ] `CHANGELOG.md` updated.
- [ ] `planning/roadmap.yaml`: S-007 set to done with its branch.
- [ ] One line appended to `planning/PROGRESS.md`.
