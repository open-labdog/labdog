# S-009: Stop bundling the Claude Code binary in the Docker image

| | |
|---|---|
| Edition | shared |
| Source | roadmap (a licensing item on the other board), decision D-04 |
| Tier / score | P0 / 6.5 |
| Stage | 2 |
| Effort | M |
| Autonomy | B |
| Depends on | none |

## Goal

The published Docker image contains no Claude Code binary. When an operator turns on a provider that needs it, unbork fetches the pinned build onto the state volume; if that fails, the provider reads as unavailable with the reason, and nothing else is affected.

## Scope

In scope:
- Removing the binary and the `claude` symlink from the image.
- A first-use installer, its status on the provider, and the two backends using the installed path.
- A CI check that the image holds no such binary.
- Docs and the image-size notes.

Out of scope:
- Making the whole assistant removable (C-026, which builds on this).
- Labels (S-008) and credentials (S-014).
- Package installs (`.deb`, `.rpm`, tarball). They never shipped the binary; the operator installs it system-wide as today.
- Third-party notices (S-010).

## Context

- `Dockerfile:54-55` (builder stage) runs `uv export --frozen --no-emit-project --extra agent` and installs the result. The `agent` extra is `claude-agent-sdk>=0.2.134` (`backend/pyproject.toml:62-72`), whose wheel carries a Claude Code binary of about 131 MB at `claude_agent_sdk/_bundled/claude`.
- `Dockerfile:85-112` documents that choice and symlinks the bundled binary to `/usr/local/bin/claude`, then runs `claude --version` as a build check. `Dockerfile:72-74` creates `/var/lib/labdog/claude-cli`.
- `backend/uv.lock:472-486` pins `claude-agent-sdk` 0.2.134 with the URL and sha256 of each platform wheel (manylinux x86_64 and aarch64 among them).
- Agent backend: `backend/app/ai/agent_sdk/__init__.py` (`sdk_available`, `UNAVAILABLE_MESSAGE`), `probe.py` (`bundled_cli_version` reads `claude_agent_sdk._cli_version.__cli_version__`; `test_connection` builds `ClaudeAgentOptions` at line 77), `runner.py:630-655` (options for a session), `backend/app/tasks/ai_task.py:77-80` (raises `AIDisabledError(UNAVAILABLE_MESSAGE)`).
- Single-shot backend: `backend/app/ai/providers/claude_cli.py`: `CLI_BINARY = "claude"` resolved from `PATH` (`shutil.which` at line 307), `NOT_FOUND_MESSAGE` says "the official image bundles it".
- The Test button is `POST /api/ai/providers/{provider_id}/test` (`backend/app/api/ai.py:277`, `require("ai.use")`) and runs the probe inside the API process.
- Docs that describe bundling: `docs/ui/assistant.md:499-502,605-631`, `docs/production-deploy.md:103` (the `claude-cli` volume), `TYPE_HELP` in `frontend/app/(dashboard)/ai-providers/client-page.tsx:30`.
- CI builds the image in `build-test-image` and scans it in `trivy-scan` (`.github/workflows/ci.yml:1012`).

## Dependencies

- [D-04](../DECISIONS.md).
- Outbound HTTPS from the server to `files.pythonhosted.org` at first use.
- A real Claude sign-in for the final manual check (Dennis), on local Docker on `lin-manager-2`.
- C-049 renames the state root; write paths through `DEFAULT_CONFIG_DIR`'s parent so the rename needs no second edit here.

## Implementation steps

1. `Dockerfile`: keep `--extra agent` (the SDK's Python code stays), then in the builder stage delete `claude_agent_sdk/_bundled/claude` after `uv pip install`, so the binary never reaches a layer of the final image. Delete lines 85-112 (comment, symlink, `--version` check) and replace them with a three-line comment pointing at this mechanism. Create `<state>/claude-code` owned by the service user.
2. Pin file: in the builder stage, generate `app/ai/claude_code_pin.json` from `uv.lock` with a small script `backend/scripts/claude_code_pin.py` (reads the lock with `tomllib`, writes `{sdk_version, wheels: {x86_64: {url, sha256}, aarch64: {url, sha256}}}`). Copy it into the final image. Commit a copy generated the same way so source installs and tests have it, and add a test that it matches `uv.lock`.
3. New module `backend/app/ai/claude_code.py`:
   - `binary_path() -> Path | None`: `<state>/claude-code/<sdk_version>/claude` if present and executable; else `shutil.which("claude")` (an operator-installed program is honoured); else `None`.
   - `install() -> Path`: take an exclusive `fcntl` lock on `<state>/claude-code/.lock`; return early if present; download the wheel for `platform.machine()` with `httpx` (streamed, proxy settings honoured, 10 minute cap) to a temp file in the same directory; verify sha256 against the pin; extract only `claude_agent_sdk/_bundled/claude` with `zipfile`; `chmod 0755`; run `claude --version`; rename into place atomically; remove directories of other versions.
   - `status() -> {"state": "ready"|"missing"|"installing"|"failed", "detail": str}` from the directory, the lock and a `last_error` file.
   - Every failure raises `ClaudeCodeUnavailable("Provider unavailable: …")` with the cause (no network, hash mismatch, unsupported architecture, read-only volume).
4. Celery task `app.tasks.ai_runtime.install_claude_code` routed to `long_running` (add to the include list and the route map in `backend/app/tasks/__init__.py`; `tests/test_task_routing.py` must still pass). It calls `install()` and records the error text on failure.
5. Triggers: `create_provider` and `update_provider` enqueue the task when a program-backed provider is created or enabled and `status()` is `missing` or `failed`. `test_provider` returns the result "Installing the Claude Code program; try again in a few minutes" when the state is `missing` or `installing` (and enqueues), the stored error when `failed`, and runs the probe when `ready`. No new route.
6. Use the path: pass `cli_path=str(binary_path())` in both `ClaudeAgentOptions` constructions (confirm the option exists in the pinned SDK; otherwise prepend the directory to `PATH` in `options.env`). In `ClaudeCLIProvider`, replace the bare `CLI_BINARY` in argv with the resolved path. When `binary_path()` is `None`, `ai_task.py` and the single-shot provider raise the unavailable error and a worker calls `install()` lazily once, so a split deployment whose worker container has its own volume also gets the program.
7. `AIProviderResponse` gains `runtime: {state, detail} | None` (null for API-key types). The providers page shows "installing", "ready" or "unavailable: <detail>" next to the type, and `TYPE_HELP` says the program is downloaded on first use. Rewrite `UNAVAILABLE_MESSAGE` and `NOT_FOUND_MESSAGE` accordingly.
8. CI: after `build-test-image`, add a step that runs the image with `sh -c 'find / -xdev -type f \( -name claude -o -name "claude-*" \) -size +1M 2>/dev/null'` and fails on any output, and a step that fails if `command -v claude` succeeds.
9. Tests: `backend/tests/ai/test_claude_code_install.py` with a local HTTP fixture serving a tiny fake wheel: success, hash mismatch, network error, concurrent calls install once, unsupported architecture, operator binary on `PATH` is used without download. Update `test_agent_sdk_probe.py`, `test_agent_sdk_runner.py` and `test_cli_auth.py` for the path and messages.

## Acceptance criteria

- The CI step from step 8 passes: no file named `claude` larger than 1 MB exists in the image and `claude` is not on `PATH`.
- The compressed image is at least 100 MB smaller than the previous build (record both sizes in the PR).
- On a fresh container with a state volume, creating a "Claude agent" provider leads, without operator action, to `runtime.state == "ready"`, and Test then reaches the sign-in check.
- With outbound access to PyPI blocked, the provider shows "unavailable" with a reason naming the download, the Test button returns that text, sessions on that provider fail with the same text, and syncs, actions and other providers work.
- A wheel whose hash does not match the pin is never executed.
- Restarting the container does not download again when the volume was kept.

## Test plan

- `cd backend && source .venv/bin/activate && pytest tests/ai/test_claude_code_install.py tests/ai/test_agent_sdk_probe.py tests/ai/test_agent_sdk_runner.py tests/ai/test_cli_auth.py tests/test_task_routing.py -q`, then the full suite before the stage review.
- `cd frontend && npx tsc --noEmit && npm run lint`
- `docker build -t unbork:s009 .` and the two commands from step 8, on `lin-manager-2`.
- On `lin-manager-2`: run the image with a state volume, create a provider, watch the state reach ready; repeat with `--network none` on the app container's egress (or a firewall rule) for the failure case. A full prompt needs the sign-in from S-014 and Dennis's account. No managed host is needed.

## Docs and changelog

- `docs/ui/assistant.md`: replace "Running LabDog in a container" bundling text with first-use install, the volume requirement, the network requirement, and the offline route (put the program on `PATH` yourself). `docs/production-deploy.md`: the `claude-cli` volume note and firewall egress. `docs/upgrade.md`: after upgrading, the program is fetched once. `docs/trivy-rationale.md` if it mentions the binary.
- `CHANGELOG.md` `[Unreleased]`, Changed: "The Docker image no longer contains the Claude Code program. It is downloaded to the state volume the first time a provider that needs it is turned on. The image is smaller, and a server without outbound access must provide the program itself."

## Risks and rollback

- An upgraded install with a working provider is briefly unavailable until the download finishes. The lazy install in step 6 and the upgrade note cover it; the session error says what is happening.
- No state volume: the program is downloaded again after each container replacement. Documented.
- The SDK may refuse a `cli_path` whose version differs from its pin. The installer always fetches the build from the same wheel version, so they match by construction.
- Rollback: revert the PR; the next image bundles the binary again. Files already on the volume are harmless.

## Assumptions

- [D-04](../DECISIONS.md): installed on first use, not shipped in the image.
- Assumption (not yet confirmed): the program is taken from the pinned `claude-agent-sdk` wheel on PyPI, verified against the hash in `uv.lock`. This keeps the SDK and the program in step, which the current Dockerfile comment treats as a requirement. Question for Dennis: is that source acceptable, or must it be Anthropic's own installer or apt repository?
- Assumption (not yet confirmed): the SDK's Python code may stay in the image. Check the licence file inside the wheel during implementation and record it for S-010; if it does not allow redistribution, drop `--extra agent` from the image and install the whole wheel into the volume instead.
- Assumption (not yet confirmed): "turning the provider on" means creating or enabling a provider of either program-backed type.

## Definition of done

- [ ] Dockerfile, installer, task, UI status and tests merged to `dev`.
- [ ] CI check for the absent binary in place and green.
- [ ] Docs and changelog updated; image sizes recorded.
- [ ] Manual check on `lin-manager-2` recorded in `planning/PROGRESS.md`.
- [ ] `planning/roadmap.yaml` status set to done.
