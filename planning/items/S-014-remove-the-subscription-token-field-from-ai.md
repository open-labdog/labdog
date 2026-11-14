# S-014: Remove the subscription token field from AI providers

| | |
|---|---|
| Edition | shared |
| Source | decision D-04 |
| Tier / score | P0 / 10.0 |
| Stage | 2 |
| Effort | S |
| Autonomy | B |
| Depends on | none |

## Goal

The AI provider form no longer asks for, accepts or explains a Claude subscription token. The two provider types that use the Claude Code program work with the operator's own sign-in on the server, and the docs say how to do that sign-in so that it survives a restart.

## Scope

In scope:
- The token field, its hints and its help paragraphs in the provider form.
- The API refusing a credential for the two program-backed provider types.
- Making the operator's sign-in persistent (a fixed config directory on the state volume).
- A visible notice and a "remove stored token" control for providers that already hold a token.
- `docs/ui/assistant.md`.

Out of scope:
- API-key fields of the `anthropic` and `openai_compat` types. Unchanged.
- Deleting stored tokens automatically. This plan never deletes operator data.
- Labels (S-008) and where the binary comes from (S-009).

## Context

- Form: `frontend/app/(dashboard)/ai-providers/client-page.tsx`. The field at line 531 is labelled "subscription token" when the type is program-backed; the placeholder at line 543 starts "sk-ant-oat01-… from `claude setup-token`" and says a blank value uses the host's own login; help paragraphs at lines 552-566 explain `claude setup-token` and credential precedence; `expiryNote` (lines 115-127) warns that the token expired and says to run `claude setup-token` again; line 450 offers "a Claude Code subscription token". `frontend/lib/types.ts:1175-1178` carries `uses_subscription` and `credential_expires_at`.
- API: `backend/app/api/ai.py`: `_check_subscription_token` (line 103) and `SUBSCRIPTION_TOKEN_PREFIX`; `create_provider` stores `payload.api_key` for any type (line 180); `update_provider` handles `api_key` at lines 215-241, where an empty string clears it and sets `credential_set_at` to NULL. Routes declare `require("ai.use")`.
- Use of the stored token: `backend/app/ai/providers/factory.py:194` passes it to `ClaudeCLIProvider(oauth_token=…)`; `backend/app/ai/agent_sdk/runner.py:652` and `probe.py:77` pass it to `build_sdk_env`. `build_cli_env` (`backend/app/ai/providers/claude_cli.py:109`) and `build_sdk_env` (`backend/app/ai/agent_sdk/environment.py:83`) set `CLAUDE_CODE_OAUTH_TOKEN` and `CLAUDE_CONFIG_DIR` only when a token is present. With no token they leave the environment alone, so the program reads its sign-in from the service user's home directory.
- That home directory is not on a volume in the image, and the systemd unit has `ProtectHome=true`, so a sign-in made the obvious way is lost on container replacement and invisible on a package install.
- `credential_expiry` (`backend/app/ai/schemas.py:142`) derives a one-year expiry for these types. Tests: `backend/tests/ai/test_cli_auth.py`, `test_agent_sdk_env.py`, `test_credential_expiry.py`.
- `docs/ui/assistant.md:494-668` documents the token flow in detail ("Using the Claude CLI with a subscription", "Paste the token into the provider's Subscription token field", the precedence note).

## Dependencies

- [D-04](../DECISIONS.md).
- S-008 and S-009 edit the same form and docs page. Order in the stage is S-008, this item, S-009.
- C-049 names the state directory `/var/lib/unbork/claude-cli`; until it lands the path is `DEFAULT_CONFIG_DIR` as defined today.

## Implementation steps

1. `client-page.tsx`: for the two program-backed types, do not render the credential field, its hint or the `claude setup-token` paragraphs. In their place show a short text: this type uses the Claude Code sign-in of the service account on this server, with a link to the docs section from step 6. Remove the token wording at line 450 and the "token expired" branch of `expiryNote` for these types.
2. Same file: when an existing provider of these types has `has_api_key` true, show a notice "This provider still holds a subscription token saved by an earlier version. It is used until you remove it." and a "Remove stored token" button that sends `PATCH /api/ai/providers/{id}` with `api_key: ""`.
3. `backend/app/api/ai.py`: in `create_provider` and `update_provider`, reject a non-empty `api_key` for types in `SUBSCRIPTION_BACKED_TYPES` with 422 and the message "This provider type takes no credential. Sign in to Claude Code on the server instead." Keep accepting the empty string (clear). Delete `_check_subscription_token` and `SUBSCRIPTION_TOKEN_PREFIX`. No new route; the existing ones keep `require("ai.use")`.
4. Persistent sign-in: in `build_cli_env` and `build_sdk_env`, when there is no token, set `CLAUDE_CONFIG_DIR` to the state directory unless the service environment already defines it. Do not blank `ANTHROPIC_API_KEY` in this case (unchanged behaviour). Call `ensure_state_dir` before both. Update `test_cli_auth.py` and `test_agent_sdk_env.py`: the no-token case now pins the config directory and still leaves credential variables alone; an operator-set `CLAUDE_CONFIG_DIR` wins.
5. Keep the code path that injects a stored token, so an upgraded install keeps working until its operator removes the token. Mark it in a comment as legacy with a pointer to this item. Keep `credential_expires_at` for such rows only.
6. `docs/ui/assistant.md`: replace the token sections with "Signing in": for Docker, `docker exec -it -u unbork <container> env CLAUDE_CONFIG_DIR=/var/lib/unbork/claude-cli claude` and the program's own login command; for a package install, the same with `sudo -u unbork`. State that the directory must be on a volume, that the sign-in is the operator's own and for their own instance, and how to remove a token saved by an earlier version. Remove the "Why LabDog controls the CLI's credentials" note except for the part about `ANTHROPIC_API_KEY` outranking a sign-in, which is still true.
7. Add `backend/tests/ai/test_provider_credentials.py`: create and update with a token for both types return 422; clearing returns 200 and nulls `encrypted_api_key`; `anthropic` still requires a key.

## Acceptance criteria

- Creating or editing a "Claude agent" or "Claude single-shot" provider shows no credential input. `grep -n "setup-token" frontend/ -r` returns nothing.
- `POST /api/ai/providers` with `provider_type: "claude_agent"` and a non-empty `api_key` returns 422. The same for `PATCH`.
- A provider that already holds a token still passes Test after the upgrade, shows the notice, and passes Test with the server sign-in after the token is removed.
- After signing in once with the documented command, replacing the container (with the state volume kept) leaves the provider's Test green.
- `docs/ui/assistant.md` has no instruction to paste a token.

## Test plan

- `cd backend && source .venv/bin/activate && pytest tests/ai/test_provider_credentials.py tests/ai/test_cli_auth.py tests/ai/test_agent_sdk_env.py tests/ai/test_credential_expiry.py -q`, then the full suite before the stage review.
- `cd frontend && npx tsc --noEmit && npm run lint && npx playwright test ai-provider`
- The sign-in and container-replacement check needs a real Claude account and runs by hand on local Docker on `lin-manager-2`. It needs Dennis, or a session he has signed in. No managed host is involved.

## Docs and changelog

- `docs/ui/assistant.md` (step 6); `docs/production-deploy.md` and `docs/backup-restore.md` where they mention the token or the `claude-cli` volume; `docs/upgrade.md`: one paragraph for installs that use a stored token.
- `CHANGELOG.md` `[Unreleased]`, Removed: "The subscription token field on AI providers. The Claude agent and Claude single-shot types now use the Claude Code sign-in of the service account on the server. A token saved by an earlier version keeps working until you remove it."

## Risks and rollback

- An operator removes the stored token before signing in, and the provider tests red. The notice says to sign in first, and the Test button reports the program's own "not signed in" error.
- Pinning `CLAUDE_CONFIG_DIR` hides a sign-in an operator made earlier in the service user's home directory. Mitigation: if the state directory holds no credentials and the home directory does, do not pin (check for the program's credentials file in step 4), and note it in `docs/upgrade.md`.
- Rollback: revert the PR. Stored tokens were never touched.

## Assumptions

- [D-04](../DECISIONS.md): the field is removed and the operator's own sign-in is the only route. The decision row says "stage 1"; the plan and `stages/stage-2.md` place this item in stage 2. This spec follows the stage file.
- Assumption (not yet confirmed): tokens saved by an earlier version keep working until the operator removes them. Question for Dennis: should a later release stop honouring them, and if so which one?
- Assumption (not yet confirmed): the sign-in is kept in the state directory by pinning `CLAUDE_CONFIG_DIR`, because a home-directory sign-in does not survive a container replacement and is not visible under `ProtectHome=true`.
- Assumption (not yet confirmed): the exact login command is taken from the Claude Code documentation at implementation time, not from this spec.

## Definition of done

- [ ] Form, API check, persistent sign-in and tests merged to `dev`.
- [ ] Docs and changelog updated.
- [ ] Manual sign-in check on `lin-manager-2` recorded in `planning/PROGRESS.md`.
- [ ] `planning/roadmap.yaml` status set to done.
