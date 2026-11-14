# S-008: Rename the Claude Code provider labels

| | |
|---|---|
| Edition | shared |
| Source | roadmap (a licensing item on the other board), decision D-04 |
| Tier / score | P0 / 11.0 |
| Stage | 2 |
| Effort | S |
| Autonomy | A |
| Depends on | none |

## Goal

No provider type in the AI providers page, the docs or an API message is presented under the name "Claude Code". The two provider types keep working and keep their stored `provider_type` values.

## Scope

In scope:
- The two type labels and their help texts in the providers page.
- Operator-facing backend messages that name the provider as "Claude Code".
- The provider table and headings in `docs/ui/assistant.md`.

Out of scope:
- The stored values `claude_agent` and `claude_cli`, and any migration.
- The subscription token field and its help text (S-014).
- How the binary reaches the server (S-009).
- Sentences that correctly name Claude Code as the third-party program the operator installs and signs in to. Naming someone else's product is fine; labelling our integration with it is what changes.

## Context

- `frontend/app/(dashboard)/ai-providers/client-page.tsx:18-23`: `TYPE_LABEL` has `claude_agent: "Claude Code (agentic)"` and `claude_cli: "Claude Code (single-shot)"`. `TYPE_HELP` (lines 25-33) starts both texts with "Claude Code …" and says "Bundled in the LabDog container image". Line 450 (empty state) and line 517 (model placeholder) also say "Claude Code". The helper `isClaudeCode()` is an internal name.
- `frontend/lib/ai-presets.ts:84` is a comment only.
- Backend messages an operator sees: `backend/app/ai/providers/factory.py:157` ("runs through Claude Code"), `backend/app/ai/agent_sdk/probe.py:97-120` (Test button results such as "Claude Code {version} via the Agent SDK; authenticated"), `backend/app/ai/providers/claude_cli.py:99-106` (`NOT_FOUND_MESSAGE`).
- `docs/ui/assistant.md` uses "**Claude Code (agentic)**" (lines 575, 624), "**Claude Code CLI**" (lines 594, 629) and a table row "**Claude CLI**" (line 492). Ten lines in total mention "Claude Code".
- Pull request #297, merged to `dev`, edited `client-page.tsx` for the product name. Rebase before starting; line numbers here are from before it.

## Dependencies

- None. S-014 and S-009 edit the same file and the same docs page; land this one first or rebase the others on it.
- The Enterprise build copies this frontend, so it receives the new labels with no change of its own.

## Implementation steps

1. `client-page.tsx`: set `TYPE_LABEL.claude_agent` to "Claude agent" and `TYPE_LABEL.claude_cli` to "Claude single-shot". Rewrite both `TYPE_HELP` texts so the label is not repeated as "Claude Code": for example "Runs Claude as an agent through the Claude Agent SDK, using the Claude Code program on this server. Runs tools, so it can drive assistant sessions and scheduled checks." and "Calls Claude once per prompt through the Claude Code program on this server. Cannot run tools." Remove "Bundled in the LabDog container image" (S-009 replaces it with the install-on-first-use wording; leave no claim about bundling here).
2. Same file: reword the empty state at line 450 and the placeholder at line 517 so they do not offer "a Claude Code subscription token" or "Claude Code's own default model" as if it were a provider name. Keep the meaning.
3. Rename the internal helper `isClaudeCode` to `isClaudeProgramBacked` (or similar) only if it costs nothing; it is not user-visible.
4. Backend: in `factory.py:157`, `probe.py` and `NOT_FOUND_MESSAGE`, refer to the provider by its stored name (`provider.name`) or as "this provider", and to the program as "the Claude Code program" where the sentence is about the binary itself. Update tests that assert these strings (`grep -rn "Claude Code" backend/tests`).
5. `docs/ui/assistant.md`: rename the table row and the bold provider names to the new labels; keep "Claude Code" where the text is about installing or signing in to the program.
6. Add a Playwright assertion to the existing AI providers spec (or a new `frontend/e2e/ai-provider-labels.spec.ts`) that opens the type selector and checks no option text contains "Claude Code".

## Acceptance criteria

- The provider type selector lists "Claude agent" and "Claude single-shot" and no option containing "Claude Code".
- `grep -n "Claude Code (" frontend/ docs/ -r` returns nothing.
- Existing providers of both types still load, test and run; `GET /api/ai/providers` still returns `provider_type` values `claude_agent` and `claude_cli`.
- `docs/ui/assistant.md` uses the same two labels as the UI.

## Test plan

- `cd frontend && npx tsc --noEmit && npm run lint`
- `cd frontend && npx playwright test ai-provider` (needs the full stack: run on local Docker on `lin-manager-2`, or with `./dev/dev.sh start`).
- `cd backend && source .venv/bin/activate && pytest tests/ -q -k "provider or probe or claude"`
- `cd website && npm run build` for the docs page.
- No managed host is needed.

## Docs and changelog

- `docs/ui/assistant.md` as in step 5.
- `CHANGELOG.md` `[Unreleased]`, Changed: "The two provider types that use the Claude Code program are now labelled \"Claude agent\" and \"Claude single-shot\". Existing providers are unaffected."

## Risks and rollback

- Operators who know the old labels may not find the type. The help text names the program, and the docs list both old and new labels once.
- A test or e2e spec matching the old strings fails. Step 4 and step 6 cover them.
- Rollback: revert the PR. No data changes.

## Assumptions

- [D-04](../DECISIONS.md) covers the Claude Code items; the labels themselves are not decided there. Assumption (not yet confirmed): the labels are "Claude agent" and "Claude single-shot". The board suggested "Claude Agent" or "Powered by Claude". Question for Dennis: confirm the two strings.
- Assumption (not yet confirmed): referring to "the Claude Code program" in help text and docs, as the name of the tool the operator installs, is acceptable. Only labels and headings that present the integration under that name change.

## Definition of done

- [ ] Labels, help texts, backend messages and docs merged to `dev`.
- [ ] Playwright assertion added; frontend and backend checks green.
- [ ] Changelog line written.
- [ ] `planning/roadmap.yaml` status set to done; `planning/PROGRESS.md` line added.
