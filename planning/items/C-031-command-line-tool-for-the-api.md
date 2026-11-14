# C-031: Command-line tool for the API

| | |
|---|---|
| Edition | community |
| Source | roadmap, board item "REST API tokens and a command-line tool" (the CLI half) |
| Tier / score | P3 / 4.0 |
| Stage | parked |
| Effort | M |
| Autonomy | B |
| Depends on | C-030 |

## Goal
An operator or a CI job can drive the API from a shell (list hosts, start a sync or an action run, wait for the result) with an API token, without a browser session.

## Why it is parked
The tool has nothing to authenticate with until C-030 (REST API tokens) ships: today the API accepts only the cookie session with a CSRF token. The board rates the CLI P3, and the token is the part every integration needs.

## What would un-park it
- C-030 is merged, so a non-cookie credential exists.
- A concrete use is named (a CI pipeline, a scheduled script), which decides the first commands.

## Context
- `labdog-lint/` is the only command-line tool today: package `labdog_lint` (`cli.py`, `schema.py`, `validators.py`, `formatter.py`), entry point `labdog-lint = "labdog_lint.cli:main"` in `labdog-lint/pyproject.toml`. It validates GitOps YAML offline and never calls the API.
- `backend/app/auth/` has no token or bearer authentication (searched for `api_token`, `ApiToken`, `Bearer`).
- Every route declares a permission through `require()` (`backend/app/auth/permissions.py`), so a token carries its owner's role and the CLI needs no permission logic of its own.
- Long operations are asynchronous: `POST /api/sync/hosts/{id}/bulk` and `POST /api/actions/runs` return a job to poll, so a `--wait` flag has to poll.
- FastAPI serves an OpenAPI document, which could generate the client.

## Assumptions
- Assumption (not yet confirmed): the CLI is a new package beside `labdog-lint/`, not an extension of it, because the linter must stay usable offline with no server. Question for Dennis: one tool with subcommands, or two?
- Assumption (not yet confirmed): the name follows the rename ([D-02](../DECISIONS.md)), so the command is `unbork`.
- Assumption (not yet confirmed): the first version covers read commands plus sync and action runs, not every endpoint.
