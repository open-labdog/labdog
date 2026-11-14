# S-012: Copyright holder line in LICENSE

| | |
|---|---|
| Edition | shared |
| Source | roadmap, "Licensing groundwork: commercial licence, copyright line, third-party notices, IP check" (the copyright line part); decision D-07 |
| Tier / score | P1 / 8.0 |
| Stage | 0 |
| Effort | S |
| Autonomy | A |
| Depends on | none |

## Goal
Anyone who opens `LICENSE` sees who holds the copyright: Dennis Tyresson, with contributors keeping the copyright in their own contributions. The README and the LICENSE say the same thing.

## Scope
In scope:
- One copyright notice at the top of `LICENSE` in this repository.
- Checking that `README.md` matches.

Out of scope:
- Per-file licence headers. `CONTRIBUTING.md` (lines 140 to 143) says they are not required, and that stays.
- Third-party notices and the source offer for the Docker image (a separate board item).
- The contributor agreement (S-011).
- The `LICENSE` in `unbork-playbooks`; see Assumptions.
- Any change to the licence itself. The project stays AGPL-3.0-or-later.

## Context
- `LICENSE` is the unmodified GNU AGPL version 3 text. Its only copyright lines are the FSF's own (line 4) and the template in the "How to Apply These Terms" appendix (line 633, `Copyright (C) <year>  <name of author>`). No holder for this project is named.
- `README.md` line 134 already says "Copyright © 2026 Dennis Tyresson and contributors."
- `packaging/nfpm.yaml` line 15 and `frontend/package.json` (`"license": "AGPL-3.0-or-later"`) carry the licence identifier only. They need no change.
- `GET /api/version` returns `license` as an identifier (see `CLAUDE.md`, "About / version surface"). No change.
- The AGPL text itself may not be modified ("changing it is not allowed", line 5). A notice placed above the licence text is the usual practice and does not change the text.

## Dependencies
- Decision [D-07](../DECISIONS.md): Dennis personally is the copyright holder.
- No other item. S-011 is independent, but its CONTRIBUTING text and this line must not contradict each other: both say contributors keep their copyright.

## Implementation steps
1. Edit `LICENSE`: insert two lines at the very top, before the existing first line, so the file begins:
   ```
   Copyright (C) 2026 Dennis Tyresson and contributors

                       GNU AFFERO GENERAL PUBLIC LICENSE
   ```
   That is the notice, one blank line, then the existing text byte for byte. Do not add the product name: the rename to unbork lands in stage 2, and a name here would need a second edit.
2. Confirm nothing else in the file changed: `git diff --stat LICENSE` shows 2 insertions and 0 deletions.
3. `README.md` line 134: keep it as is if it still reads "Copyright © 2026 Dennis Tyresson and contributors." If the wording has drifted, make it match the LICENSE line.
4. Do not touch the appendix template at the end of `LICENSE` (line 633 before the edit). It is part of the licence text.

## Acceptance criteria
- `head -1 LICENSE` prints `Copyright (C) 2026 Dennis Tyresson and contributors`.
- `tail -n +3 LICENSE | diff - <(git show HEAD~1:LICENSE)` prints nothing (the licence text is unchanged), run on the commit that makes the change.
- `grep -n "Copyright" README.md` names the same holder.
- After the PR is merged to `dev`, the repository page on GitHub still shows the licence as AGPL-3.0 (GitHub's detector ignores a leading copyright line; this is the check that it did).

## Test plan
- No code changes, so no test file is added.
- Commands for the criteria above, from the repository root:
  `head -1 LICENSE`
  `tail -n +3 LICENSE | diff - <(git show HEAD~1:LICENSE) && echo unchanged`
- Packaging is not affected, but `packaging-smoke` runs in CI on the PR anyway and must stay green.
- The GitHub licence detection check is done by looking at the repository page after merge. It needs no host.

## Docs and changelog
- No file under `docs/` changes.
- `CHANGELOG.md` `[Unreleased]`, under "Changed": "`LICENSE` now names the copyright holder (Dennis Tyresson and contributors). The licence is unchanged."

## Risks and rollback
- Risk: GitHub stops recognising the licence if the preamble is longer than a copyright line. Keep to the one line plus a blank line. If detection breaks anyway, move the notice to a new `COPYRIGHT` file, restore `LICENSE` to the verbatim text, and raise it with Dennis, because the stage behaviour asks for the name in `LICENSE`.
- Risk: the year or name is wrong. Both come from the README line that Dennis already published.
- Rollback: revert the commit.

## Assumptions
- [D-07](../DECISIONS.md) fixes the holder. The exact line, "Copyright (C) 2026 Dennis Tyresson and contributors", follows the README and the suggested wording in the decision write-up.
- Assumption (not yet confirmed): "and contributors" stays in the line. It is accurate under the contributor agreement in D-01, which is a licence and not an assignment, so contributors keep their copyright. Question for Dennis: keep "and contributors", or name Dennis only?
- Assumption (not yet confirmed): the year is 2026 only, as in the README, and is not turned into a range.
- Assumption (not yet confirmed): `unbork-playbooks/LICENSE` (also AGPL text with no holder) gets the same line in a separate PR in that repository. It is not part of this item until Dennis says so.

## Definition of done
- [ ] `LICENSE` starts with the notice; the licence text below it is unchanged.
- [ ] README matches.
- [ ] `CHANGELOG.md` `[Unreleased]` line added.
- [ ] GitHub licence detection checked after merge.
- [ ] `planning/roadmap.yaml`: S-012 `status: done`.
- [ ] One line appended to `planning/PROGRESS.md`.
