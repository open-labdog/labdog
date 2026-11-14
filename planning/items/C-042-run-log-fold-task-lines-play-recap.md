# C-042: Run log: fold task lines, PLAY RECAP as a table

| | |
|---|---|
| Edition | community |
| Source | roadmap, board item "Run log: fold task lines and show PLAY RECAP as a table" |
| Tier / score | P3 / 2.5 |
| Stage | parked |
| Effort | M |
| Autonomy | A |
| Depends on | none |

## Goal
In the run log an operator can collapse a task's lines under its `TASK` header, and sees `PLAY RECAP` as a per-host table for large group runs, while a copy of the log is still exactly the text Ansible printed.

## Why it is parked
It is polish. The run log is already coloured, searchable, wraps, and jumps to the first failure (shipped in 1.0.0). Nothing depends on it.

## What would un-park it
- Free capacity in a stage that already touches the run log or the window component.
- PR-06 (a frontend unit-test harness, see `../PROPOSALS.md`) being accepted would make the parser changes cheap to test.

## Context
- Parser: `frontend/lib/ansible-log.ts`. `parseAnsibleLog` (line 60) returns `LogLine[]` with a `LineKind` per line. It already recognises task headers (`TASK` regex, line 48), the recap header (`RECAP`, line 49) and recap rows (`RECAP_ROW`, line 50, coloured in place at lines 87 to 94).
- Renderer: `frontend/components/ansible-log.tsx`, used by `frontend/components/action-run-detail.tsx`. The log is one `<pre>`, which is what keeps copied text whole.
- Window chrome: `frontend/components/ld/window.tsx` (resize, maximize, minimize, text size).
- The two constraints from the board: a fold needs a way to keep the copied text whole, and the recap table has to sit outside the `<pre>`.

## Assumptions
- Assumption (not yet confirmed): folding hides lines with CSS or a copy handler that writes the full text, so select-all and copy still give the whole log. Question for Dennis: should a copy of a folded log include the folded lines? Assumed yes.
- Assumption (not yet confirmed): the recap table is an extra view above or below the log, and the recap text stays in the `<pre>` as well.
- Assumption (not yet confirmed): the same component serves sync logs, so both gain the change.
