# Changelog

All notable changes to LabDog are documented in this file.

The format follows [Keep a Changelog]; LabDog follows
[Semantic Versioning].

## [Unreleased]

### Security

- **Four smaller exposures closed.** None was a hole on its own; together they
  were the difference between an install that tells an unauthenticated caller
  nothing and one that hands over a route map and a certificate inventory.

  - `/docs`, `/redoc` and `/openapi.json` are no longer served. They needed no
    authentication and listed every route, parameter and schema. Set
    `server.expose_docs = true` to bring them back; the dev config does.
  - `POST /api/ai/providers/{id}/test` no longer returns the text of an
    unexpected exception. A client library raising on an auth failure can
    quote the request it sent, credentials included, and that response goes to
    the browser. The detail is logged instead.
  - `/metrics` no longer labels CA-certificate expiry with the certificate's
    SHA-256 fingerprint. The endpoint is unauthenticated and everything else
    it emits is an aggregate count; the name is what an alert needs.
  - **The terminal WebSocket now authenticates before completing the
    handshake, checks `Origin`, and — the one that was a live gap — honours
    the session generation.** A token invalidated by logging out or changing a
    password still opened a terminal, because the WebSocket path did not go
    through the check added for that. A rejected handshake now surfaces in the
    browser as a plain connection failure rather than a coded close.

- **Firewall sync to an iptables-backend host was failing outright, and had
  been for as long as the dual-stack teardown has existed.** The teardown
  script carries a prose comment containing the word `isn't`. Ansible parses a
  shell task given as a bare string by tokenising it shell-style, and that
  apostrophe read as an unclosed quote — so the whole playbook was rejected
  before a single task ran. The YAML was valid, so nothing in the test suite
  noticed; only Ansible objected, and only at run time.

  Found while verifying the `/tmp` change above against a real host. The two
  teardown scripts are now passed as a mapping, which skips that parsing
  entirely, and a test runs every generated task through Ansible's own
  splitter so this class of failure cannot come back quietly.

- **Firewall rollback state no longer lives at guessable paths on the managed
  host.** The deadman's switch — the 60-second automatic revert that saves you
  when a new ruleset cuts off SSH — kept its backups and its revert PID at
  fixed names under `/tmp`, then ran `kill $(cat …)` as root against one of
  them. Any local user on a managed host could pre-create or symlink those
  names and choose what root read back: which ruleset gets restored when the
  switch fires, or which process gets signalled.

  Both playbooks now create a private root-owned directory per run and thread
  its path through, so nothing is predictable and the whole directory is
  removed afterwards. The switch itself is unchanged, including the ordering
  that keeps the backups available until the revert has been cancelled.

- **The login rate limit now throttles the attacker instead of the whole
  install.** It keyed on the client address alone, and behind a reverse proxy
  with `server.trusted_proxies` unset — the default — every request resolves
  to the proxy, so one shared 5/minute bucket covered everybody. Five bad
  passwords from anywhere locked all users out, and an attacker was throttled
  no harder than someone with a typo.

  Attempts are now counted per account: once for the pairing of client
  address and account, and once for the account across all addresses, so both
  a single source guessing one password and a distributed attempt on one
  account are caught. Nothing is keyed on the address alone any more.

  `server.trusted_proxies` accepts CIDR entries, which the shipped config's
  comment already claimed and the code did not do — useful when the proxy
  runs in a container and its address is not known when the config is
  written. If a proxy is forwarding client addresses and `trusted_proxies` is
  empty, LabDog now says so in the log once at startup rather than silently
  discarding them.

- **Startup now refuses to run with a signing key that can be guessed, and
  checks the encryption key before it is needed.** Validation rejected exactly
  two literal placeholder strings and nothing else, so a six-character HS256
  key passed — and forging the auth cookie is forging any account, superuser
  included.

  `security.secret_key` must now be at least 32 characters (`openssl rand
  -base64 32`, which the docs already tell you to run, gives 44).
  `security.encryption_key` is decoded at startup instead of failing hours
  later during the first host sync. `security.allowed_origins = ["*"]` is
  refused outright: LabDog always sends credentials, so a wildcard makes
  Starlette reflect whatever `Origin` the request carried — any site a
  logged-in user visits could drive the API as them.

  **`CHANGE_ME` — the placeholder shipped in `packaging/etc/labdog.toml` —
  was not on the rejected list.** A `.deb` or `.rpm` install that skipped the
  "generate your secrets" step therefore ran with a nine-character signing
  key that is published in this repository. If that describes your install,
  treat every account as compromised: generate real secrets, and note that
  every session is invalidated when `secret_key` changes.

  Running LabDog over plain HTTP on a trusted LAN still works. That case now
  logs a warning about `cookie_secure`, rather than refusing to start.

- **Logging out and changing a password now actually revoke the session.**
  The auth cookie is a stateless JWT: logging out cleared it in the browser
  and did nothing else, so a cookie somebody had already copied stayed valid
  for the rest of `security.session_lifetime_seconds` — 24 hours by default.
  Changing a password did not help, which made "change your password"
  useless as a response to a suspected compromise, the one situation it
  exists for. Neither did an administrator resetting one.

  Sessions now carry a generation number that is checked on every request
  and bumped on logout, on a password change and on an admin password reset.

  Three things to expect:

  - **Everyone is logged out once when you upgrade.** Existing cookies carry
    no generation and are rejected. Accepting them would leave exactly the
    sessions this exists to revoke working until they expired on their own.
  - **Logging out signs you out everywhere**, not just in the browser you
    clicked it in. This is the documented trade-off of the cheap fix:
    per-session revocation would need a second datastore in the request path
    that fails open when it is unreachable.
  - **Changing your password signs you out everywhere too**, including the
    browser you changed it in, so you will be asked to log in again
    immediately afterwards.

- **The password policy now applies everywhere a password is set.** The
  12-character rule lived on the registration form alone, so it held on the
  one path nobody needs to use. `PATCH /api/users/me` accepts a `password`
  field and had no validator — any user could set a one-character password
  on their own account, no administrator involved — and the admin
  create-user and reset-password endpoints hashed whatever string they were
  given.

  The rule itself is unchanged; tightening it would lock out existing
  accounts at their next password change, which is a separate decision. Two
  things were added that cannot lock anyone out: a 128-character ceiling (the
  hashing cost is paid on every login attempt, on input an unauthenticated
  caller chooses) and a refusal to set the password equal to the account's
  own email address.

- **The webhook secret is encrypted at rest and no longer returned by the
  API.** It was stored in plain text under a comment calling it "not a
  credential" — it is the HMAC key every inbound push webhook is verified
  against, so anyone who could read it could forge a push and make LabDog
  import configuration from a commit of their choosing. Any authenticated
  user could read it, because `GET /api/git-repos` returned it verbatim. It
  was also the only secret outside an `encrypted_*` column.

  Existing secrets are encrypted in place on upgrade, so configured webhooks
  keep working — nothing to re-enter. The API now reports
  `has_webhook_secret` instead, and the UI shows whether one is set rather
  than offering to copy it. To rotate, type a new one; leaving the field
  blank keeps the existing secret.

- **The git HTTPS token no longer appears on the `git` command line.** It was
  embedded in the clone URL, so it sat in `/proc/<pid>/cmdline` — which is
  world-readable, meaning any local account could read the token off a
  running clone — and was written into `.git/config` until a `set_url` two
  lines later scrubbed it. It now travels as an `Authorization` header
  configured through `GIT_CONFIG_*` environment variables, which are readable
  only by the process owner and root. The pack sync path already kept the
  token out of the URL but passed it via `git -c`, which is argv too; both
  paths now use the same mechanism.

- **Ansible runs and git syncs now verify the host they connect to.** LabDog
  already recorded each managed host's SSH key on first contact and refused a
  changed one — but only on its own asyncssh connections. The Ansible path,
  which is what pushes root-level configuration, set
  `StrictHostKeyChecking=accept-new` with no known-hosts file and re-accepted
  whatever key was presented on every run. A man-in-the-middle the web
  terminal refused was accepted by the pipeline that matters more.

  Playbook runs now verify against the key LabDog recorded for that host. A
  host with no recorded key yet still accepts on first contact — refusing
  would make a brand-new host unmanageable — and the key is recorded during
  the preflight connection, so the unverified window is one connection wide.
  Clear a key with `POST /api/hosts/{id}/trust-host-key` after a legitimate
  reinstall.

- **Git syncs over SSH are verified too, and the repository's host key is now
  recorded.** Both git paths combined `accept-new` with
  `UserKnownHostsFile=/dev/null`, which reads like trust-on-first-use and is
  in fact unconditional acceptance: every invocation started from an empty
  file, so there was never a first use and never a mismatch. Anyone able to
  intercept the connection to a pack repository could serve arbitrary
  playbooks, which LabDog then runs against the fleet as root.

  The first sync after upgrading records the server's key (unchanged trust
  posture, once) and every sync after that is verified against it. **If your
  git server is legitimately re-keyed, syncs will start failing with a
  host-key mismatch** — that is the point; clear the recorded key with
  `POST /api/git-repos/{id}/trust-host-key`. Changing a repository's URL to a
  different server clears it automatically.

- **SSH terminal transcripts no longer capture what you type at a password
  prompt.** Everything typed at a `sudo` prompt, a `mysql -p`, an `openssl`
  passphrase, or any pasted key or token was stored in plain text and
  served by the audit API. The host hiding its own echo never protected it —
  the keystrokes cross the WebSocket either way.

  A line answering a password prompt is now recorded as
  `[password input suppressed]`, the body of a pasted private key as
  `[private key input suppressed]`, and every row passes through the
  redactor on its way to the database. The *fact* that a secret was entered
  is still recorded; only the value is discarded.

  Existing transcripts are not rewritten. They still hold whatever was
  captured before this release — `logging.audit_retention_days` governs how
  long, and that setting now actually takes effect (see below). Consider
  whether the stored history warrants clearing.

- **Action packs can no longer ship code that runs on the LabDog host
  without being told to.** A pack is a git repository you point LabDog at,
  and ansible-core gives a repository several ways to execute on the
  *controller* rather than on a managed host: plugin directories it imports
  Python from (`action_plugins/`, `library/`, `filter_plugins/`, …), and
  plays that set `connection: local` or target `localhost`. Nothing
  inspected any of it, so adding a pack was equivalent to granting code
  execution as the `labdog` user.

  Packs containing either are now refused, and the refusal names what it
  found. A new **"Allow content that runs on the LabDog host"** setting per
  pack accepts it deliberately; flipping it is recorded in the audit log.

  **Existing packs are unaffected** — they are marked trusted on upgrade,
  because a migration cannot audit pack content and silently breaking a
  working pack is worse than preserving the status quo behind a flag you
  can now see. Packs added from here on default to untrusted.

  This is not a privilege boundary: LabDog's model is flat, so any user can
  set the flag. It makes accepting controller-side code a deliberate,
  audited act rather than a side effect of adding a repository.

### Fixed

- **Group merge results are deterministic.** Every module — firewall, cron,
  packages, services, users, `/etc/hosts` entries, CA certs, resolver —
  resolves a host's effective configuration by walking the groups it belongs
  to from highest priority down and letting the first match win. The walk was
  ordered by priority alone, so two groups sharing a priority left the winner
  to whatever order the database happened to return, and a host in both could
  get a different answer on one sync than on the last with nobody having
  changed anything. Nothing surfaced it either: drift is computed as a set
  comparison, so a re-ordering is not drift.

  `host_groups.priority` is unique now. The API has always answered 409 on a
  duplicate, but with a check that two simultaneous requests could both pass;
  the database enforces it. **On upgrade, existing duplicates are renumbered**
  — the group that was created first keeps its priority and the rest drop to
  the next free value down, which preserves the order they already had. Every
  change is logged by the migration. A racing create or update now answers 409
  instead of 500.

  The same ordering was missing one level down, for the rules *inside* a
  group. Firewall rules are the visible case: `priority` defaults to 0 for
  every rule, so the first-match order of the emitted nftables ruleset was
  whatever the query returned, and editing an unrelated rule could silently
  reshuffle it. Rules are now read highest priority first, ties broken by
  creation order, in every module.

- **Deleting a host no longer destroys its action transcripts.** The previous
  release stopped `DELETE /api/hosts/{id}` from failing and kept the run row
  itself, with a record of what it had targeted. It did not keep the per-host
  rows underneath, and those are where `output` lives — the actual transcript
  of what ran. So the surviving run said what was targeted and whether it
  failed, and no longer said what happened, usually at the exact moment
  someone was removing a host *because* something went wrong.

  Those rows now survive the delete. Each one keeps the hostname as it stood
  when the run was dispatched, so it still has a name to show once there is
  nothing left to look up, and the run detail page marks it "(deleted)".
  Reading one back is now addressed by the row rather than by host id — the
  old `/api/actions/runs/{id}/hosts/{host_id}/output` route cannot reach a
  run whose host is gone, so `/api/actions/runs/{id}/host-runs/{host_run_id}/output`
  was added and the UI uses it for every row. The old route still works for
  hosts that exist.

  A run whose host is deleted while it is in flight now finishes as failed
  with "Host was deleted before this run started" instead of raising into the
  generic error handler.

- **A host could show as drifted seconds after a clean sync.** The seven
  periodic drift sweeps took no per-host lock. A sweep that landed while a
  sync was applying that host's configuration read a half-applied state and
  wrote `out_of_sync` over the status the sync was maintaining — and nothing
  distinguished that stale verdict from a real one, so the usual reaction was
  to sync again and watch it happen again.

  Every sweep now goes through one shared driver that asks the same
  "is this host claimed?" question syncs and action runs ask, skips a claimed
  host for that tick, and — for the case a lock alone cannot cover — re-checks
  after the collection and discards the verdict if an operation claimed the
  host while the SSH round trip was in flight. A skipped host is checked on
  the next interval; the operation holding it leaves the status correct on its
  way out either way.

  Six of the seven also held a single transaction open across the whole
  sweep and committed once at the end, so a worker restart mid-sweep threw
  away every host's result and one host's failed statement took every host
  after it with it. Each host now gets its own transaction, and an
  unanticipated failure is logged with its traceback instead of being
  swallowed.

- **A run page opened under a host showed the wrong id in its links.**
  `/hosts/7/actions/runs/12/` rendered with *both* route parameters set to
  `12`, so the host id was missing from the page data and the back-link and
  breadcrumb pointed at the run as though it were the host. The pre-rendered
  page bakes in one placeholder per dynamic segment and only one value was
  being substituted. Affected the two nested routes,
  `hosts/[id]/actions/runs/[runId]` and `groups/[id]/actions/runs/[runId]`.
  The page always fetched the right data — the client router resolves the
  real URL — so this was confusing rather than harmful.

- **A sync deferred behind a busy host no longer reapplies every module.**
  Asking to reapply just the firewall on a host that was already syncing
  queued the request — and when the queue drained, the queued job had lost
  its module list and applied all seven: packages reinstalled, services
  restarted, `/etc/hosts` rewritten. `sync_jobs` had nowhere to record which
  modules were asked for, so the re-dispatch reconstructed "all of them"
  from a row that only said "bulk".

  The job now records its module list, and `GET /api/sync/jobs` reports it.
  A repeat request while one is in flight also stops claiming the filter is
  unknown and names what the queued job will actually do.

  Jobs queued before upgrading have no recorded list and still mean every
  module, which is what they were going to do anyway.

- **Daily maintenance jobs now actually fire.** Fifteen periodic schedules
  were registered when their module was first imported, and every
  registration reset the job's next-due time to a full interval away. Both
  the API and the worker import those modules, so every restart pushed the
  daily jobs back another day: on a deployment that restarts more often than
  once a day, audit-log pruning, SSH-transcript pruning and AI snapshot
  retention **never ran at all**. Six of the fifteen also swallowed their
  own failures silently.

  Registration now happens once, in the process that actually runs the
  scheduler, and only rewrites an entry when the schedule genuinely changed
  — so a restart no longer moves a job that was due in ten minutes.

  On upgrade, expect the pruners to run for the first time. If your install
  has been up for a while, the first audit-log prune may delete a lot at
  once; check `logging.audit_retention_days` before restarting if that
  matters to you.

- **A referenced host changing its IP now actually flags its dependants.**
  When a host referenced by an `/etc/hosts` entry changed address, LabDog
  raised the drift flag on the dependent hosts — under a module name nothing
  reads. The status those hosts displayed still said in sync, no drift was
  reported, no sync was offered, and their `/etc/hosts` kept pointing at the
  old address. The firewall half of the same code path used the right name,
  so half the feature worked.

  Upgrading repairs the existing rows rather than discarding them: a host
  whose stale row recorded drift is marked out of sync on the row that is
  actually read, so signals raised while the bug was live are delivered
  now rather than lost. Expect some hosts to show `/etc/hosts` as out of
  sync after upgrading — that is the backlog surfacing, and a sync or the
  next drift check clears it.

- **Values LabDog writes into config files can no longer add lines to them.**
  Three fields were interpolated into generated files with nothing stopping
  them from ending the line they sat on:

  A `/etc/hosts` entry's **comment** had no validation at all, so a comment
  containing a newline appended a second, working `/etc/hosts` line — a
  package-mirror redirect on every host in the group, written by LabDog
  itself and invisible in a UI that shows the comment on one line. A managed
  **host's hostname** reaches the same file through host references and was
  likewise unchecked. And a **sudo rule** was written into
  `/etc/sudoers.d/<user>`, where a newline produced a two-line drop-in
  granting passwordless root to an account LabDog does not manage —
  `visudo -cf` validated it happily, because it is correct sudoers syntax.

  All three are now rejected, at the schema and again where the file is
  written, since entries also arrive through the GitOps YAML importer and
  older rows predate the validators. `ssh_port` is bounded to a real port
  range while nearby.

  An existing hosts entry or sudo rule containing a newline will now be
  refused on edit; the rendered files stay safe either way.

- **A hung host no longer stalls every host behind it.** LabDog bounded how
  long it would wait to *reach* a host, but not how long a command could take
  once connected. A host that answered SSH and then hung — a wedged
  `nft list ruleset`, a stuck NFS mount, a process in D state — held the
  caller indefinitely. The drift sweep and state collection walk hosts one at
  a time, so a single unresponsive host silently stopped every host after it
  from being checked at all, on every tick.

  Every command now carries a deadline, bound at the connection rather than
  at each call site, so it covers the thirty-odd places that needed it and
  anything added later. The new **SSH command timeout** setting (default 60s)
  controls it; the connect timeout is unchanged and still separate.

- **The container healthcheck now checks something.** `/health` returned
  `{"status": "ok"}` unconditionally — it never touched the database, Redis,
  or the Celery workers. If a worker died the container stayed healthy
  forever while nothing ran: no syncs, no drift checks, no scheduled actions,
  and no signal that anything was wrong.

  There are now two endpoints. `/health` and `/health/live` stay constant —
  "is the process answering" is what a restart policy should act on.
  `/health/ready` checks the database, Redis and the workers, and returns 503
  naming the component that failed. The image healthcheck points at it.

  If you run LabDog outside the provided image and monitor `/health`, nothing
  changes; point at `/health/ready` to get the stronger check.

- **Built-in actions actually run.** `_builtin.collect_state`,
  `_builtin.drift_check`, `_builtin.sync` and `_builtin.ai_task` never
  executed their work — three separate defects, stacked, each hiding the
  next. Found by running six of them against a live instance.

  First, every built-in deferred behind its own parent run. The run
  orchestrator marks the parent "running" before dispatching, and the
  built-in's busy-check counted that as another operation holding the
  host. The deferral was permanent: it is only cleared when some *other*
  operation on that host finishes, and there was none.

  With that cleared, two of them crashed on `asyncio.run() cannot be
  called from a running event loop` — they invoked another Celery task
  in-process, and that task's body starts its own event loop inside the
  one already running.

  With that fixed, `_builtin.sync` deferred against itself again, this
  time through its own per-host row, and reported `succeeded` having
  synced nothing.

  All three are fixed. A scheduled sync or drift check now claims its
  host, does the work, and reports what actually happened. Nothing needs
  reconfiguring, but if you have scheduled actions using these built-ins,
  expect them to start doing something on the next tick — check that
  their schedules and targets are still what you want before upgrading.

  One related reporting gap remains (tracked as BUG-81): a `_builtin.sync`
  that is genuinely deferred behind another operation still finishes as
  `succeeded` rather than saying it was queued.

- **Action runs no longer deadlock the fleet when several schedules share a
  cron minute.** The run orchestrator dispatches one task per host and then
  blocks until they finish — but it was published to the same queue and pool
  as those children, four slots wide by default. Four schedules on `0 3 * * *`
  took every slot, no child could get one, and nothing moved until the
  orchestrator's twelve-hour limit fired and finalised four runs as `partial`
  with zero hosts touched.

  LabDog now runs two Celery workers: `work`, which does everything it did
  before, and `orchestrator`, which runs only the run orchestrator. The pool
  an orchestrator waits on is never the pool it occupies, so the starvation
  is impossible rather than merely unlikely. Exceeding
  `celery.orchestrator_concurrency` (default 4) queues runs instead.

  Operationally: the process now has two Celery children instead of one, so
  expect roughly double the worker memory. Nothing needs reconfiguring — no
  deployment sets `-Q` itself — but a container with a tight memory limit may
  need it raised.

- **A host or group with action-run history can be deleted again.**
  `DELETE /api/hosts/{id}` returned 500 for any host that had ever been the
  target of an ad-hoc action run, with no way to remove it short of manual
  SQL. `action_runs` links to its target through `ON DELETE SET NULL`
  foreign keys, under a constraint that forbade all of them being NULL —
  which is exactly what `SET NULL` produced when the run's only target was
  the host being deleted.

  Runs now describe their own target: `target_kind` (`host`, `group` or
  `fleet`) and `target_label`, the hostname or group name as it stood when
  the run was dispatched. Both survive the delete, so the run history stays
  readable — the action-run page shows the label with "(deleted)" beside it.
  Cascading the runs away instead would have destroyed the audit trail at
  the moment an operator most needs it, since a run's output is often the
  only record of what was done to a host that is being removed *because*
  something went wrong.

  Note that per-host output is a separate table and is still cascaded away
  with the host (tracked as BUG-77): the parent run survives and says what
  was targeted and how it ended, but the per-host transcript for the deleted
  host does not.

- **Ten settings that never did anything now take effect.** Every setting
  read from a Celery task or an SSH code path silently fell back to its
  hardcoded default. The synchronous reader built its own connection URL
  with two `.replace()` calls, the second undoing the first, leaving a
  `postgresql://` URL that needs a driver LabDog does not depend on — and a
  blanket `except Exception` turned the resulting import error into "use the
  default". Nothing warmed the shared cache in a worker either, so it could
  not cover for it.

  **Read this before upgrading.** These settings start being honoured on the
  next restart, and some of them have never been observed to do anything, so
  a value set long ago may not be a value anyone still wants:

  | Setting | Was always | Now |
  |---|---|---|
  | `ansible.playbook_timeout` | 300s | your value (3 call sites) |
  | `ssh.connect_timeout` | 10s | your value (2 call sites) |
  | `ssh.idle_timeout_seconds` | 1800s | your value |
  | `discovery.max_concurrent` | 100 | your value |
  | `logging.audit_retention_days` | 90 | your value |
  | `actions.preflight_enabled` | always on | your value |
  | `ai.wall_clock_seconds` | 900s | your value |

  Check **Settings** — or `SELECT key, value FROM app_settings` — and
  confirm the stored values are what you want before restarting. Lowering
  `ansible.playbook_timeout` below a slow playbook's real runtime will now
  actually kill it; `logging.audit_retention_days` will now actually delete
  audit rows (`0` means keep forever, and is honoured as such).

  Synchronous readers now consult a process cache that is refreshed from
  whatever database session the surrounding code already has, so no code
  path opens its own connection or blocks an event loop to read a setting.

## [0.9.0] — 2026-08-24

The AI release. LabDog can now hand an investigation to a language model,
let it change hosts under supervision you choose, and use it to decide
whether a destructive action left a host healthy. **Every part of it is
off by default** and stays off until you set `ai.enabled` and configure a
provider.

### Added

- **The prompt behind an alert investigation is editable.** The wording an
  investigation starts from is now the `ai.alert_mission_template` setting
  rather than a constant in the source. The built-in text asks a
  deliberately generic question because it has to work for an alert LabDog
  has never seen; an operator knows which alerts on their estate are noisy,
  which exporter lies during a backup, and what an answer should always
  mention. That knowledge previously had nowhere to go.

  Placeholders (`{alertname}`, `{severity}`, `{status}`, `{starts_at}`,
  `{labels}`, `{annotations}`) are validated when the template is saved,
  naming both the placeholder it did not recognise and the ones available.
  The alternative is a `KeyError` raised hours later inside a Celery task,
  leaving an alert uninvestigated with nothing on screen to say why. A
  stored template that a later release breaks falls back to the built-in
  wording rather than stranding the alert.

  Settings gained a `text` value type for this: multiline values render as
  a full-width text area with a character count and a **Reset to default**
  button, because the default is a paragraph nobody retypes from memory.

- **A session now says why it stopped.** A run cut short by a cap finished
  with a green `succeeded` badge, identical to one that reached its own
  conclusion — the reason existed only in the Celery task's return value
  and a sentence at the bottom of the report, neither of which is
  reachable from the UI. Sessions carry a `stopped_reason`, shown as
  "Stopped early: token budget (10000)" beside the status and as a "cut
  short" marker in the session list.

  Deliberately not folded into `error_message`: a capped run is not an
  error. It did what it was asked until the budget it was given ran out,
  and filing it as a failure is as misleading as calling it a clean
  success.

- **A running session can be stopped, and an alert says what came of
  its investigation.** The Assistant header gained a **Stop** button —
  it interrupts the model mid-turn, keeps the transcript and whatever was
  already established, and books the tokens spent. It works while a
  session is parked on an approval too: nothing is running then, but the
  session is not over either, and abandoning it is a reasonable answer to
  a request you do not want to grant.

  The alerts page previously said only whether an investigation had been
  *started*, which stopped being the useful question the moment one had.
  A row now carries the session's status — Investigating, Investigated,
  Investigation failed — and quotes the opening of its conclusion, so the
  answer is readable without opening anything. **View investigation**
  opens that session rather than the session list.

- **AI assistant.** A chat page at `/assistant` where you describe what
  you want checked and watch the model work through LabDog's own tools.
  It cannot run anything LabDog does not offer it: each command is parsed
  by a **default-deny classifier**, bounded by a per-session **host
  allowlist**, redacted for credentials before it enters the transcript,
  and recorded.

  **Four provider backends.** OpenAI-compatible endpoints (Ollama, vLLM,
  OpenRouter, OpenAI), the Anthropic Messages API, the Claude Code CLI,
  and the Claude Agent SDK. The last two authenticate a **Claude
  subscription** rather than metered API credit — the SDK backend can run
  tools, the CLI backend is single-shot and serves reports and verify
  verdicts. Credentials are encrypted at rest with the same AES-256-GCM
  key as every other secret and participate in key rotation.

  **Three autonomy levels.** Read-only (the default) refuses anything
  that would change a host. Approval-required pauses on a change and
  waits for you. Full-auto acts unattended. A **denylist applies at every
  level** — `rm -rf /`, `mkfs`, writing to block devices, piping a
  download into a shell, flushing the firewall — with no setting that
  permits them.

  **Spend is bounded and enforced, not merely reported.** Daily and
  monthly budgets, per-provider monthly caps, and per-session limits on
  iterations, commands, tokens, and wall-clock time. Budgets are checked
  before a session starts *and* between steps, so a long run that crosses
  a limit stops rather than finishing on credit. Costs are recorded in
  whichever currency you configure; LabDog never converts between them.

- **Scheduled AI checks.** Two built-in actions — **AI check (per host)**
  and **AI check (whole group)** — appear in the normal action list, so
  they inherit cron scheduling, run history, cancellation, and the
  per-host queue from machinery that already existed. The group variant
  puts every member in scope at once, which is what "compare these" and
  "which one is the odd one out" need; per-host sessions cannot see each
  other.

  A **per-session tool allowlist** is the main cost control: a nightly
  log sweep restricted to `query_loki` cannot open an SSH session at all,
  and cannot spend what an unbounded `journalctl` would. Each tool call
  records how much text it returned, so which tools consume your budget
  is observable rather than assumed.

- **Approval-gated changes.** At the approval level, a command that would
  change a host does not run. The session **stops entirely** — no worker
  held, no host lock held — so a check scheduled at 3am can wait until
  morning without wedging every sync queued behind it. Approving runs
  exactly what you approved; LabDog executes it from the stored record
  rather than asking the model again. Rejecting takes a note that is
  passed back, because a rejection with a reason usually produces a
  better suggestion than a bare no.

  The approval card shows the command, **why LabDog classified it as a
  change** (from parsing, not from the model's claim), and the model's
  own stated purpose — kept visually separate because it is a claim, not
  a verdict. Undecided requests expire after
  `ai.approval_expiry_hours` and the session finishes with a report.

- **Snapshot before an AI change.** Hosts mapped to a Proxmox VM get a
  snapshot before the assistant modifies them, at both the approval and
  full-auto levels. They are named `labdog-ai-<session>-<timestamp>`,
  distinct from an action pack's `labdog-<run>-<timestamp>`, and are
  **deliberately not deleted on success** — the point of them is that you
  can undo the change after reading what it did. A retention sweep
  removes them after `ai.snapshot_retention_days`.

- **AI verification of destructive actions.** A manifest can set
  `ai_verify_prompt` to ask, in words, whether an action left the host
  healthy. LabDog collects the evidence itself — managed services and
  packages, load, disk, recent error-priority journal entries — renders
  it into the prompt, and asks one question. **The model gets no tools**:
  everything it judges was gathered before the session started, so a
  verdict that can restore a snapshot cannot also go reaching into the
  host it is deciding about.

  The verdict is **PASS, FAIL, or INCONCLUSIVE**. `ai_verify_fail_closed`
  decides only what INCONCLUSIVE resolves to — a stated PASS or FAIL is
  honoured either way — and defaults to open, which is the behaviour
  every existing manifest was written against.

  Readings that could not be taken are marked `UNAVAILABLE` rather than
  left blank, so a check that failed cannot be mistaken for a healthy
  one. Each verdict runs as a real session, visible under **Assistant**
  with its transcript, counted against your budget, and linked from the
  action run.

- **Alert intake, and investigation on arrival.** LabDog can receive
  alerts from a Grafana contact point (`POST /api/webhooks/grafana-alerts`,
  gated on a shared token in `[alerts] webhook_token`) and poll an
  Alertmanager API as a fallback, deduplicating against each other. The
  webhook is the path that works everywhere; the poller reads *Mimir's*
  Alertmanager, so it only sees rules evaluated by Mimir's ruler —
  Grafana-managed rules go to Grafana's own Alertmanager and are
  invisible to it. It defaults to off for that reason.

  Dedup is on **(fingerprint, start time)**, not fingerprint alone.
  Alertmanager's fingerprint hashes the label set, so the same rule
  firing for the same host yields the same fingerprint every time it ever
  fires; keying on it alone would fold next month's outage into this
  month's row.

  An eligible alert starts a **read-only** investigation, scoped to the
  host LabDog resolved from the labels — or to no host, when it cannot,
  because putting an investigation on the wrong machine is worse than
  putting it on none. Eligibility is a policy: intake on, alert firing,
  not already investigated, severity meeting
  `ai.auto_investigate_min_severity`, AI enabled, and budget available.

  **Whichever gate stopped it is recorded on the alert and shown in the
  UI.** "Nothing happened" has six causes and each has a different fix, so
  the row says which one applied rather than leaving an operator to
  reconstruct it from logs. A severity LabDog does not recognise — `sev1`,
  `P1` — meets no threshold and says so; treating it as critical would be
  a guess, and only one kind of guess spends money unattended.

  New page at `/alerts`, four `ai.*` settings, and
  [`docs/ui/alerts.md`](docs/ui/alerts.md). All of it off by default.

### Changed

- **`/metrics`, the audit log, and the settings page** gained AI
  coverage: `ai.*` settings are documented in
  [`docs/ui/settings.md`](docs/ui/settings.md), every executed AI command
  writes an `AuditLog` row against its host, and every *attempt* —
  including blocked, refused, and parked ones — writes a tool-call record
  visible in the session transcript.
- **Fonts are served from the repo** instead of fetched from Google at
  build time. `next/font/google` downloaded them while compiling, which
  made every production build depend on reaching `fonts.gstatic.com` and
  produced failures naming neither the network nor the font.

### Fixed

- **A run stopped by a cap or by Stop recorded no usage at all.** Tokens
  reach the ledger only when the SDK's terminal `ResultMessage` arrives,
  and interrupting the exchange means it never does — so the runs that hit
  a limit, the expensive ones, were the only ones missing from the usage
  panel. Seen the first time the token cap fired in production: the session
  stopped on its budget having spent real tokens and reported zero, and the
  daily ledger had no row for the day. The live estimate is now booked
  instead, flagged `cost_unknown` because it is an approximation rather
  than the CLI's own aggregate.

- **An alert row could show `## Summary` instead of a conclusion.** The
  alerts page quotes the opening of the investigation's report, taken as
  its first paragraph — but reports that begin with a markdown heading put
  the heading there instead of the verdict. Headings are now skipped.

- **"View investigation" went to the Assistant page but selected nothing.**
  The alerts page has linked to `/assistant?session=<id>` since alert
  intake shipped, and nothing ever read the parameter — so the button
  navigated and left the operator on a list of sessions to guess from. The
  parameter is now honoured.

- **Sessions in the Assistant list had no timestamp**, so runs of the same
  thing were indistinguishable: an alert investigation is titled after its
  alert, and four firings of one rule produced four identical rows. Each
  now shows when it started — absolute local time to the minute, with the
  relative age beside it and the exact ISO value on hover, in the list and
  in the open transcript alike, so a run can be lined up against a Grafana
  panel or a journal.

  Relative time alone was tried first and was not enough: three
  investigations run the same evening all read "23h ago", which is the
  question the timestamp existed to answer.

- **Eight background tasks were published to a queue nothing consumed**
  and so never ran (BUG-58). The worker consumes `default,long_running`,
  but Celery's built-in default queue is named `celery`, and
  `task_default_queue` was never set — so any task no `task_routes`
  pattern matched went to `celery` and stayed there. The broker accepted
  it, `send_task` returned an id, and the work silently never happened.

  Six of the eight were on RedBeat timers, firing into the dead queue for
  the life of the deployment: both stale-run sweepers, all three
  retention pruners, and approval expiry. **Two settings you can see and
  change in the UI therefore did nothing** — `logging.audit_retention_days`
  and `ai.snapshot_retention_days` — so audit logs, SSH transcripts and
  AI snapshots were never pruned. Alert auto-investigation was the
  seventh, which is how this was found: an alert recorded correctly and
  then no session ever started.

  Fixed by naming the default queue rather than adding the eight missing
  route patterns. `task_routes` is a routing *override*, not a manifest,
  and treating it as the complete list is what stranded these in the
  first place — the next task added without an entry would have vanished
  the same way.

  After upgrading, the pruners run on their next tick and delete
  everything already past its retention window in one pass. On an
  instance that has been running a while that backlog is however much
  accumulated since install, so check `logging.audit_retention_days` and
  `ai.snapshot_retention_days` are set to what you actually want *before*
  restarting — they have not been enforced until now, and the first run
  is not reversible.

- **`ai.max_tokens_total` did nothing on the Claude Agent SDK backend**
  (BUG-59). Token usage was folded into the session only when the SDK's
  `ResultMessage` arrived — its *terminal* message — so for the whole run
  the counters the cap tests against sat at zero. The check ran every
  turn, compared 0 against the limit, and never fired; the real figure
  landed when there was nothing left to stop. Measured on a production
  session: **111,857 tokens spent against a 10,000 cap.**

  The runner now sums the per-response `usage` each assistant message
  carries, giving the cap a live figure to test. That estimate gates the
  run only — `ResultMessage` remains the sole source for the session's
  token columns, the cost ledger and the usage panel, because the two
  come from different sources and booking both would double count.

  This mattered more than it looks. On a subscription provider every
  price is zero, so `ai.budget_daily` and `ai.budget_monthly` can never
  trigger, and the token cap was the only bound on how much one session
  could spend. Sessions remain bounded by `ai.max_iterations`,
  `ai.max_commands` and `ai.wall_clock_seconds`; there is still **no
  limit on how many sessions may run**, which matters when an alert storm
  can start one per alert.

- **Webhooks are reachable again** (BUG-56). Every endpoint under the
  webhooks router returned `403 CSRF token missing or invalid` before
  its handler ran, for the entire life of the CSRF middleware. The
  double-submit cookie is issued to a logged-in browser, and Grafana,
  GitHub, GitLab and Gitea have no session and no cookie jar — so the
  check could never pass and never protected anything. It is a defence
  against a browser being tricked into a state change; a request that
  carries its own bearer token or HMAC signature has nothing to be
  tricked out of. The router is now exempted by prefix, so a webhook
  added later is not silently broken the same way.

  The test suite did not catch this because it caused it: the shared
  client auto-attaches `X-CSRF-Token` to every mutating request,
  satisfying on the sender's behalf the one condition no real sender
  can meet. An `external_client` fixture with no cookie jar now exists
  for anything authenticated from the request itself.

  **The webhook routes have moved under `/api`** — `/api/webhooks/github`,
  `/api/webhooks/gitlab`, `/api/webhooks/gitea` — matching every other
  route and the URLs the documentation already gave. Normally breaking;
  in practice nothing working breaks, because none of them worked. The
  URLs shown on the **Git Repositories** page update themselves, but any
  webhook configured at the old path must be repointed.

- **An English pass no longer rolls a host back.** The AI verify step
  searched its whole reply for the substrings `PASS` and `FAIL`, so
  *"Everything looks fine; nothing failed."* — how a model answers "is
  this host healthy?" when the answer is yes — matched `FAIL` inside
  *failed* and reverted the host. The verdict is now read from the first
  line only, anchored and word-bounded.
- **An unreadable journal is no longer reported as a quiet one**
  (BUG-54). `journalctl` run by an unprivileged user prints only that
  user's own entries and **exits 0**, so LabDog — which connects as an
  ordinary user on most hosts — recorded no errors on a host that had
  them, and the verify step told the operator the journal "was
  successfully read" and passed. LabDog now reads as root, or through
  `sudo -n`, and reports `UNAVAILABLE` with a reason when it can do
  neither.
- **Preview (dry-run) works again** (BUG-53). The run dialog carried the
  flag inside `parameters`, which the API validates against the action's
  manifest with `extra="forbid"`, so every preview was rejected with
  *"Extra inputs are not permitted"* before reaching the code that
  consumes it. The server now sets the flag itself from the request
  field, which had existed and been read nowhere.
- **A dry run no longer buys an AI verdict.** Neither the snapshot nor
  the verify gate consults `dry_run`, so repairing Preview would have
  made previewing a destructive action open a billed session judging a
  host that check mode deliberately left unchanged.
- **A change is less likely to be rolled back by its own log noise.** The
  evidence window is the window the action ran in, so it contains
  whatever the action itself logged — a restarted service, a package
  manager replacing files. The verify prompt now says so and asks for the
  host's state as it stands, while keeping "still failing now" a failure.
- **Verdicts explain what they judged.** The command classifier seeded
  its result with a placeholder that only greater severity could
  displace, so every allowed command reported an empty segment and "no
  command segments found" as its reason.
- **Subscription tokens warn before they expire.** `claude setup-token`
  mints a one-year credential, and an unattended session cannot recover
  once it lapses; provider rows now record when the credential was
  written and show the expiry, amber inside 30 days.

### Known limitations

- **A token limit overshoots by one turn.** Usage is only known once a
  turn completes, so the turn that crosses the limit has already been
  paid for; the cap stops the next one. A 10,000-token limit stopping at
  ~11,700 is expected. Treat the number as "stop somewhere past here"
  rather than a hard ceiling — a tighter bound needs per-token streaming
  accounting the backends do not expose.

- **AI verify judges a window that includes the action's own work.**
  Prompt wording mitigates this and the shipped default accounts for it,
  but narrowing the window to before the action started is the real fix
  and is not built. Read the reasoning, not just the verdict.
- **Any signed-in user can start a session and approve its changes.**
  There is no separate AI permission and no second-person approval — see
  [`docs/security-hardening.md`](docs/security-hardening.md).
- **The classifier parses commands rather than executing them
  symbolically**, so a sufficiently creative shell construction could be
  classified wrongly. Read-only is the default for that reason.

## [0.8.0] — 2026-08-04

### Added

- **Dashboard charts and activity feeds.** The Fleet Overview page gains two
  charts and two feeds below the existing summary cards:

  - **Sync Success Rate** — success share per day over the last 7 days, drawn
    from LabDog's full sync-job history, so it has data immediately.
  - **Drift Trend** — drift checks that found a host out of sync, per day.
  - **Recent Activity** — the last 10 audit events, inline instead of only
    behind the `/audit` route.
  - **Recent Scheduled Runs** — individual runs from schedules, with a
    **Grouped** toggle that collapses them to one row per schedule with an
    expandable status strip.

  The host table is now capped at the **top 10**, with a selector offering
  eight orderings (needs-attention, recently synced, stalest sync, longest
  since check, recently drifted, never checked, errors only, newest).

  Drift history required new storage: LabDog only kept each host's *current*
  drift state, overwritten on every check, so there was nothing to plot. Every
  drift check now records an append-only sample, written in the same
  transaction as the existing status update. It is **forward-only** — no
  backfill, because fabricating history would misrepresent it — so the chart
  shows a "collecting history" state until checks start running. That same
  storage is what the metrics exporter below reads, so both surfaces report
  the same numbers.

- **Prometheus metrics export.** A new `GET /metrics` endpoint serves LabDog's
  own state in Prometheus text exposition format, so an existing Prometheus +
  Grafana stack can scrape it directly instead of bolting on another tool. It
  covers both **fleet state** (hosts by sync status, per-module state, desired-
  state rule counts, staleness ages, CA certificate expiry, discovery backlog)
  and **LabDog self-health** (sync and action outcomes, duration histograms,
  queue depth, stuck jobs, scheduler and action-pack health).

  **Disabled by default.** Enable with `[metrics] enabled = true` (or
  `LABDOG_METRICS__ENABLED=true`); the endpoint returns 404 while disabled.
  When enabled it is **unauthenticated** — Prometheus cannot use LabDog's
  cookie session auth, so the endpoint is deliberately open-and-opt-in rather
  than half-gated, and is meant to be restricted at the reverse proxy. It is a
  config-file setting rather than a UI toggle on purpose: the in-app settings
  API is available to any signed-in user, so a UI toggle would let any
  authenticated session publish fleet state to the network.

  The **Integrations → Grafana** page now shows both directions explicitly —
  *Metrics in* (the existing Mimir/Loki backends LabDog queries for host
  CPU/memory/disk) and *Metrics out* (a new card with the scrape URL, status,
  and a copy-paste Grafana Alloy scrape snippet).

  Ships a ready-made Grafana dashboard, 14 example alerting rules, and a scrape
  config in `docs/examples/prometheus/`, documented in
  [`docs/metrics-export.md`](docs/metrics-export.md).

  Notable design points: values are aggregated from PostgreSQL per scrape and
  cached briefly, so counters are DB-derived and every worker returns identical
  numbers (in-process counters would be wrong under multiple uvicorn workers);
  there is deliberately **no per-host label** anywhere, since per-host telemetry
  already belongs to the Alloy → Mimir path; and the renderer is dependency-free
  (`prometheus-client` is a dev-only dependency used to validate output in
  tests).

  One observability gap this closes: `labdog_drift_checks_total` exposes the
  drift-check `result` verbatim, including `error`. A drift check that *fails to
  run* is not the same as one that *finds drift*, but the in-app drift trend
  counts only confirmed drift — so a check erroring 100% of the time was
  previously indistinguishable from a healthy one.

### Fixed

- Hovering a dashboard chart no longer washes the whole panel out. Recharts'
  default tooltip cursor is a near-white rectangle sized to the hovered
  category band, which on a dark panel with a single day bucket covered the
  entire plot area. Both charts were affected.

### Security

- `cryptography` floor raised to `>=50` (CVE-2026-69247) and `gitpython` to
  `>=3.1.57` (GHSA-3f7w-8rr8-f37f).
- `shadcn` moved from production dependencies to devDependencies. It is a
  component-scaffolding CLI, never imported at runtime, and it was pulling
  `hono` and `ip-address` — and their ReDoS and SSRF advisories — into the
  production dependency tree.

### Changed

- Repository history was rewritten to drop accidentally committed local
  artifacts and stale references. **Every commit SHA before this release
  changed**, and the repository is roughly 75% smaller as a result. Existing
  clones should be re-cloned rather than pulled.

## [0.7.0] — 2026-07-24

### Added

- **Live streaming of per-host action output.** A host-targeted action run
  (e.g. `linux-upgrade`) now streams its Ansible output to the run view
  task-by-task as it happens, instead of appearing as a single block only
  after the whole playbook finishes. A heartbeat keeps a long-blocking task
  (e.g. a multi-minute apt upgrade) visibly alive rather than looking frozen.
- **Self-healing for orphaned action runs.** A periodic sweeper (every 5 min,
  plus once on worker start) reconciles `ActionRun` / `ActionHostRun` rows left
  stuck in `running` / `queued` by a dead worker (OOM, SIGKILL at a time limit,
  container restart). Without it the scheduler skipped the affected schedule and
  the host queue deferred every new op forever. Rows are only reaped once past
  their action's own deadline (ansible timeout + verify + envelope grace), so a
  legitimately slow run is never swept — ansible's own timeout always fires
  first on a live worker.
- **Fast-fail preflight for dead hosts.** Each per-host action first runs a
  bounded SSH liveness probe; a genuinely unreachable host fails in ~25 s with a
  clear `host unreachable (preflight)` error instead of tying up a worker for the
  full playbook timeout. Opt out via the new `actions.preflight_enabled` setting
  — see [Settings › Actions](docs/ui/settings.md).

### Fixed

- **The run view stayed stuck on the pre-run step-log after a live-watched run
  finished.** The action run-detail page now always loads the complete persisted
  log from the DB once a run reaches a terminal state, even when a live SSE
  session had already delivered partial output. Previously the pane was left
  showing only `[preflight]` / `[snapshot]` with none of the task output or
  PLAY RECAP until a hard reload.

### Changed

- **Bundled playbooks updated** (`LABDOG_PLAYBOOKS_REF` → `1f1fcdc`), which
  carries the `linux-upgrade` fix where `apt-get update` failures were silently
  swallowed (`failed_when: false` plus a dead abort guard). The daily upgrade now
  actually refreshes the package index and applies pending updates, and surfaces
  a real failure instead of reporting a no-op as success.

### Security

- **GitPython 3.1.50 → 3.1.52** — clears the HIGH command-injection /
  environment-variable-exfiltration advisories flagged by both `pip-audit` and
  Trivy (GHSA-2f96-g7mh-g2hx, GHSA-956x-8gvw-wg5v, GHSA-v396-v7q4-x2qj,
  GHSA-rwj8-pgh3-r573).
- **Frontend advisories cleared** — `next` bumped to the patched 16.2.11, with
  patched transitive versions pinned via npm `overrides` (`sharp`, `postcss`,
  `fast-uri`, `js-yaml`, and a scoped `brace-expansion` that avoids breaking
  classic `minimatch`).
- **`.trivyignore` is now actually applied** in the `trivy-scan` CI job. The job
  never checked out the repo or passed the ignore file, so the list silently had
  no effect (and its documented purpose — a safety net for when `ignore-unfixed`
  is flipped — could never work).

## [0.6.3] — 2026-07-04

### Fixed

- **Firewall rules referencing a LabDog host diffed forever as remove +
  re-add.** A rule whose source/destination is another registered host is
  resolved to that host's `/32` (or `/128`) CIDR before diffing, but keeps its
  host-reference FK so the effective-rules view can still show the host name.
  The diff engine's match key included that FK, while the state parsed back
  over SSH carries only the CIDR — so the two never matched and the Sync
  preview showed a spurious delete + add for the same address (and drift never
  converged). The diff match key now keys on the resolved CIDR alone; the merge
  key still keeps the FK (it runs before resolution, where two unresolved host
  refs must stay distinct).
- **Effective Rules showed the resolved IP inline for host-referenced rules.**
  A source or destination that targets a registered host now renders the
  hostname only (e.g. `wireguard`), with the resolved IP shown in the hover
  tooltip instead of an awkwardly truncated inline CIDR.

### Added

- **Dual-stack firewall handling.** On hosts with both `nft` and `iptables`
  installed, LabDog now:
  - picks the managed backend with a documented decision ladder — operator
    override / stickiness to an existing LabDog ruleset, then a
    container-runtime constraint (Docker / kube-proxy / nerdctl force
    iptables), then the active ruleset, then a default of nftables — and
    records the reason;
  - **tears down its own footprint in the inactive backend on every firewall
    sync** (drops the `LABDOG-INPUT`/`LABDOG-OUTPUT` iptables chains, or deletes
    the LabDog-owned nftables `inet filter` table), so there is a single source
    of truth and stale rules can't hide in the backend collection doesn't read;
  - **warns at collect time** when a competing LabDog ruleset is found in the
    inactive backend.
  The host's **Rules** tab now shows the active backend as a badge next to
  **Effective Rules**. See [Hosts › Firewall backend selection](docs/ui/hosts.md).

## [0.6.2] — 2026-07-03

### Fixed

- **`.deb` / `.rpm` / tarball installs shipped a non-working virtualenv.**
  The bundled venv referenced the *build machine's* Python path (in CI, a
  tool-cache interpreter under `/opt/hostedtoolcache/...`), so on a clean
  target host `venv/bin/python` was a dangling symlink and the systemd
  service failed to start. The venv is now relocated at build time to the
  distro `python3.12` the package depends on (`/usr/bin/python3.12`).
  Affected the 0.6.0 and 0.6.1 packages; the container image was not
  affected.

### Added

- **Containerised packaging smoke harness (`packaging/tests/`).** A new
  `packaging-smoke` CI job builds the `.deb` / `.rpm` / tarball and
  install-tests each in a clean target-OS container (Ubuntu 24.04, Rocky
  9) — files present, service account created, the venv imports the app
  cross-distro, and the reported version is correct. Runs on release
  commits and packaging PRs. (It caught the venv bug above on its first
  run.)

## [0.6.1] — 2026-07-03

### Fixed

- **Host sync progress no longer shows in two places.** Syncing a module
  from the host page ran its own progress poller in the preview dialog *and*
  registered the job with the global sync tray, so progress appeared twice
  and the dialog reached completion before the tray did. The host flow now
  matches the group flow: on Apply the dialog closes and the global sync
  tray is the single source of truth for progress and completion.
- **Firewall backend is now detected when collecting from the Rules tab.**
  "Collect" on the Rules tab could leave a host stuck on "No Firewall
  Detected" — the firewall only appeared after running "Collect all" on the
  Overview tab. A per-module collect now refreshes the host row so a
  newly-detected backend surfaces, and a single firewall collect against a
  still-unknown host runs the same robust detection the Overview collect
  uses.

## [0.6.0] — 2026-07-02

### Added

#### Sync preview + global progress tray

- **Preview before every manual sync.** Host-page syncs — per-module and
  "Sync All" — now show a diff preview of exactly what will change before
  anything is applied, replacing the old fire-and-forget apply. Only
  cleanly-previewed, changed modules are applied (a module whose current
  state could not be read is never applied blind).
- **Global sync progress tray + completion toasts.** A bottom-right tray
  tracks every user-initiated sync (host apply, per-module, group, Sync
  All) as a live operation with per-host progress bars, and raises a
  success/failure toast on completion. Each host row has a per-module
  drill-down.
- **Group view reaches parity with the host view.** Replaced the separate
  "Firewall Sync" tab with per-tab Sync buttons on every module tab plus a
  "Sync All" (with preview). Group and host now sync the same way through
  the same coalesced per-host job.
- `GET /api/sync/jobs` gained an `ids` filter for efficient batch polling
  of many jobs at once (backs the tray).

### Changed

- **Adopted PostgreSQL 18 + Redis 8** across CI, `dev/docker-compose.yml`,
  and the production compose reference. The PG 18 volume mount moved from
  `/var/lib/postgresql/data` to `/var/lib/postgresql` (PG 18 keeps the
  cluster in a version-specific subdirectory). Existing PostgreSQL 16
  deployments are **not** a drop-in tag bump — see the new
  ["Upgrading PostgreSQL 16 → 18"](docs/production-deploy.md#upgrading-postgresql-16--18)
  section for the dump/restore procedure.
- **Reproducible builds.** Container images and packaging now install the
  exact locked dependency set from `backend/uv.lock` (via `uv export`)
  instead of resolving dependencies at build time.

### Fixed

- **`/api/version` no longer returns 500 when the distribution metadata is
  absent** in the from-source container image, which had been flapping the
  container healthcheck to "unhealthy". The version now resolves through a
  fallback chain (installed metadata → `LABDOG_VERSION` → bundled `VERSION`
  file → build info) and the image ships its `VERSION` file.
- **`.deb`/`.rpm`/tarball installs reported `/api/version` as `0.0.0`.**
  The packaged venv built `labdog-backend` without the repo-root `VERSION`
  next to `pyproject.toml`, so setuptools' dynamic version resolved to
  `0.0.0`; the build now stages `VERSION` in and the installed metadata
  carries the real version.
- **Firewall detection.** `nft`/`iptables` installed under `/usr/sbin` is
  now detected during facts collection — hosts with a firewall were being
  mis-reported as "No Firewall Detected".
- **Firewall previews.** "Managed by LabDog" now shows on every managed
  rule (not just some) in sync previews; a truncated source IP in the host
  Current State table is fixed; the sync-preview dialog readability and
  width were improved.
- **Firewall host references** in rules now resolve to CIDRs in the desired
  state, and dangling references are tolerated on the effective-rules
  display instead of raising.
- **Package drift** now detects apt `hold` / dnf `versionlock` state.
- **UI stability.** Stabilized the SSH terminal (xterm) effect and guarded
  the nftables-install poll loop against component unmount.
- **Docker** busts the apt layer cache each build so base-image security
  upgrades always land (previously a cached layer could serve stale
  packages past a published fix).

### Security

- **SSH host-key verification (TOFU) enforced everywhere.** The interactive
  terminal and **all** state collectors (firewall, services, packages,
  cron, resolver, users/groups, hosts-file) now verify host keys, replacing
  paths that previously connected with `known_hosts=None`.
- **Dependency & supply-chain hardening.** Raised floors to patched
  releases: `cryptography>=49`, `gitpython>=3.1.49`, `asyncssh>=2.23.1`,
  `starlette>=1.0.1` (CVE-2026-48710 "BadHost" host-header auth bypass),
  `python-multipart>=0.0.30`; bumped `fastapi-users>=15` and
  `ansible-core>=2.18`. The Redis server is pinned to a line carrying the
  CVE-2026-23479 fix.
- **SSRF + authorization hardening** from the code audit. Clarified the
  authorization model — all active users are trusted operators and
  superuser status only gates user administration — and removed an
  over-restrictive superuser gate that had crept onto the Schedules and
  Settings surfaces.

## [0.5.0] — 2026-06-30

### Fixed

- **iptables collector returning zero rules for all hosts.** The iptables
  state collector reported an empty ruleset regardless of a host's actual
  configuration, so iptables-backed hosts never diffed or synced correctly.

### Changed

- Bumped the bundled `LABDOG_PLAYBOOKS_REF` pin.

### Security

- `npm audit fix` cleared a high-severity frontend advisory.

## [0.4.0] — 2026-06-14

### Added

#### Grafana Mimir/Loki integration — live host metrics

LabDog can now render **instant** CPU / memory / disk usage on each
host's Overview tab by querying a registered Grafana Mimir
(Prometheus-compatible) backend. Instant values only — no graphs.

- New **Integrations → Grafana** page to register **Mimir** (metrics) and
  **Loki** (logs) endpoints separately. Each instance takes a single
  ingest/remote-write URL (plus optional tenant/`X-Scope-OrgID`,
  authentication — none / bearer token / HTTP basic, secret encrypted at
  rest — TLS verify + CA cert, per-kind default flag), with per-row and
  pre-save connection tests. LabDog derives the query URL from the
  ingest URL — strip the path to the host, append the kind's API path
  (Mimir → `/prometheus/api/v1/query`) — so the operator enters one URL.
  Modeled on the Proxmox integration (`grafana_instances` table,
  migrations `0011`/`0012`).
- `GET /api/grafana/hosts/{id}/metrics` queries the default Mimir instance
  for the host's CPU/memory/disk, matched on a stable `labdog_host_id`
  label. Panel hidden until a Mimir backend is configured; rendered as a
  compact strip at the top of the host Overview card.
- **Closes the loop with the bundled `alloy-install` action:** the run
  dialog now shows registered Grafana instance pickers instead of free-text
  URL fields. A `metrics_backend` manifest mapping lets LabDog fill the
  Alloy remote-write/Loki URLs from the default Mimir/Loki instances at
  dispatch, and the per-host executor always injects `labdog_host_id` /
  `labdog_hostname` so shipped metrics are queryable back. Register
  endpoints, run Install Alloy, and metrics appear automatically.
  See [docs/ui/metrics.md](docs/ui/metrics.md).
- **Bundled-pack pin auto-bump CI.** A new GitHub Actions workflow opens a
  PR against `dev` whenever `labdog-playbooks` `main` moves ahead of the
  pinned `LABDOG_PLAYBOOKS_REF` — triggered immediately via
  `repository_dispatch` from the playbooks repo, and daily as a fallback.

### Changed

- **Action run view identifies hosts by name.** The Host Status grid and
  per-host output headers now show the target hostname instead of the
  numeric host ID.
- **Per-host log filtering on multi-host runs.** The run-detail page shows
  the Host Status grid for any multi-host run (group, fleet, or scheduled),
  and each host card is clickable to filter the output to that host's log.
- **Compact host metrics panel.** The Resource Usage strip is embedded as
  the first block of the host info card instead of a full-width card above
  it; used/total figures and sample age moved to hover tooltips; abnormal
  states (no data, error, stale) surface as icon+tooltip rather than
  callout blocks. The page-level Refresh button now also refreshes metrics.

### Fixed

- Action-private roles (e.g. `alloy-install`'s `role-alloy-linux-*`) were
  not included in the Ansible role search path, causing "role not found"
  errors at runtime. Fixed in the pack loader.
- Grafana metrics query returning HTTP 401 against Mimir with multitenancy
  enabled: `X-Scope-OrgID` header now defaults to `"anonymous"` when no
  tenant is configured, consistent with the Alloy default.
- CPU metric intermittently blanked on refresh: widened the `rate()` window
  from 2m to 5m so CPU tolerates ingestion gaps like the instant gauges.
- Modal fields overflowing past dialog edge when containing wide,
  non-wrapping content (e.g. the service-override `systemctl cat` preview):
  `DialogContent` grid column is now shrinkable (`minmax(0,1fr)`).

## [0.3.1] — 2026-06-05

### Added

- **Per-action playbook timeout floor.** Action manifests gain an
  optional `playbook_timeout_seconds` field. The effective wall-clock
  budget for the main playbook is `max(this, the global
  ansible.playbook_timeout setting)`, so a long-running action (e.g. a
  package upgrade) can guarantee itself enough time without operators
  raising the global limit for every playbook. Unset preserves the prior
  behaviour; the bundled `linux-upgrade` / `linux-os-upgrade` actions set
  it.
- **Per-node Proxmox CA certificate trust.** Each Proxmox node can carry
  its own CA certificate for TLS verification, configured from Settings,
  instead of relying on a single system trust store (BUG-52).

### Changed

- **Action run view identifies hosts by name.** The Host Status grid and
  the per-host output headers now show the target hostname instead of the
  numeric host ID.
- **Per-host log filtering on multi-host runs.** The run-detail page shows
  the Host Status grid for any multi-host run (group, fleet, or
  scheduled), and each host card is clickable to filter the output to that
  host's log, with a "Show all hosts" toggle back to the combined view.

## [0.3.0] — 2026-05-31

### Changed

#### Bundled action pack is now fetched at build time from `labdog-playbooks`

The bundled pack at `backend/app/ansible/` is no longer a
byte-identical mirror committed to the labdog repo. It's cloned
from `open-labdog/labdog-playbooks` at the SHA pinned in the new
repo-root `LABDOG_PLAYBOOKS_REF` file when the container image,
release artefacts, or local dev environment are built. Bumping the
bundled pack is now a one-line change to that file -- no rsync,
no drift gate.

- **Dockerfile**: new `bundled-pack-fetcher` stage clones at the
  ref passed via the `LABDOG_PLAYBOOKS_REF` build-arg (CI reads
  the value from the file).
- **packaging/Makefile**: new `fetch-bundled-pack` target that
  the `build` target depends on; reads from the same file. The
  `.deb` / `.rpm` / `.tar.gz` artefacts pick up the same content
  the container does.
- **dev/dev.sh**: auto-fetches into `backend/app/ansible/` on
  first `start` when the dir is empty. New `./dev/dev.sh bundle`
  subcommand re-fetches on demand. Set
  `LABDOG_PLAYBOOKS_LOCAL=/path/to/labdog-playbooks` to rsync from
  a sibling working copy instead of cloning -- useful when
  iterating on upstream playbooks.
- **CI**: `backend-test` and `ansible-lint` jobs now run the same
  fetch logic before pytest / ansible-lint so the bundled pack is
  present.
- **Removed from git**: `backend/app/ansible/` (the in-repo bundled
  pack directory, ~2200 lines of YAML), `scripts/check-bundled-mirrors-playbooks.sh`
  (the drift gate), and the `bundled-pack-mirror` CI job. With the
  bundled pack build-time-fetched at a pinned ref, drift can't
  exist; the directory is now gitignored and populated at build
  time (or via `./dev/dev.sh bundle` in dev).

The DB-backed `labdog-playbooks` override pack auto-registered
on fresh install is unchanged (still points at `main` and is
synced via the normal pack-sync flow). The two paths -- bundled
(immutable, baked in at build time) and DB-backed (mutable,
synced at runtime) -- continue to serve their respective roles.

### Added

#### Action manifests can register installed resources into labdog's desired-state model

Action manifests gain a `post_run_register` field that declares which
resources the action installs. On successful, non-dry-run
completion, labdog inserts those resources as host-scope override
rows (`host_id=<target>`, `group_id=NULL`) so the resources become
labdog-managed going forward. Per-host fan-out for group-dispatched
actions. After the inserts, a follow-up `post_run_sync` for the
affected modules fires automatically so the Host detail tabs
reflect the new state without waiting for the next drift check.

Top-level keys are canonical module names (`packages`, `resolver`,
`services`, `hosts-file`, `cron`, `linux-users`, `firewall`). Each
value is a list of dicts validated against the module's REST API
Create schema -- same fields, same defaults, same validators
operators get from the UI / API. `host_id` and `group_id` are
implicit. Example:

```yaml
post_run_register:
  packages:
    - package_name: alloy
      state: present
  services:
    - service_name: alloy.service
      state: running
      enabled: true
```

Conflict semantic is **skip silently**: if the operator already
declared the resource for that host (e.g. `alloy.service` with
`state: stopped` deliberately), the manifest declaration is
ignored and labdog logs a line. Operator intent wins. Per-insert
savepoints isolate collisions so one skipped item doesn't unwind
the rest of the batch.

This closes the gap left by `post_run_sync`: that one re-enforces
existing desired state, this one extends desired state with new
rows. Action authors pick the right primitive per action:
re-rotate-cert actions want `post_run_sync`; install-alloy-style
actions want `post_run_register`.

A new docs section in `docs/ui/actions.md` walks through both
primitives and adds a warning about the four purge-mode modules
(firewall, hosts-file, resolver, SSH-keys) where mutating via
actions without declaring in labdog's desired state will be
undone on next sync.

#### Action manifests can declare opt-in post-run module sync

Action manifests gain a `post_run_sync` field (a list of canonical
module names: `packages`, `resolver`, `services`, `hosts-file`,
`cron`, `linux-users`, `firewall`). After the action succeeds
labdog dispatches a normal per-host sync against the same target
host for each named module, re-enforcing labdog's desired state.
Per-host fan-out across the group for `supports_host: false`
group-dispatched actions. Skipped on dry-run, on whole-run
failure, and on cancellation. Sync dispatch failures are logged
but never affect the action's terminal status — the action
already completed.

The semantic is **push, not collect**: this routes through the
normal sync pipeline which reconciles the host against labdog's
desired state for those modules. Action authors must only declare
modules where pushing the existing desired state is what the
action wants — declaring `packages` from an action that installs
something labdog's desired-state list doesn't cover would
(re)remove it.

- New `app.sync.post_run.dispatch_post_run_sync` helper creates a
  `SyncJob` per module + dispatches `run_host_sync.delay(...)` with
  an explicit `module_filter`. Per-insert savepoints isolate
  collisions on the existing `(host_id, module_type)` active-row
  unique index — a collision is treated as "already queued, skip".
- `ActionDefinitionOut` and the frontend `ActionDefinition` type
  gain `post_run_sync: list[str]`. The action card surfaces it as
  a "Post-run sync: …" hint so the operator sees the side effect
  before clicking Run.

#### Sync queue parity: `SyncJob.pending_reason`

Deferred `SyncJob` rows now carry the same `pending_reason` field
that `ActionRun` / `ActionHostRun` got in 0.2.0. When
`host_sync_orchestrator._claim_or_defer` finds another op already
running on the target host, it stamps the SyncJob with the
formatted blocker (`"Waiting for action sample.say-hello on host
node-1"`, `"Waiting for sync 47 on host node-1"`, etc.) alongside
leaving `status='pending'`. The sync queue UI surfaces the reason
inline next to the pending icon so operators see *what* is ahead
of them in the per-host queue instead of a context-free amber
"Pending" badge — closing the loop on the operator-clarity work
that already shipped on the action side.

- **Schema migration `0005_syncjob_pending_reason`** adds the
  `sync_jobs.pending_reason VARCHAR(255)` column. Additive,
  nullable; existing pending rows and any future row dispatched
  while the host is free keep NULL and render the same way as
  before.
- `SyncJobResponse` (the API shape returned from every sync
  endpoint, reused by the per-module sync routers) gains
  `pending_reason: str | None`.
- The Group Firewall Sync page polls the deferred job and now
  renders the reason inline when status is `pending`.

#### Collect All now also collects host facts and auto-heals placeholder hostnames

The "Collect All" button on the host overview now also triggers
`collect_host_facts` after the per-module collectors finish, so the
Overview tab populates with OS info, kernel, default NIC, and the
firewall backend in addition to per-module state. Previously
"Collect All" only ran the per-module collectors; OS facts were only
gathered via the separately-dispatched `_builtin.collect_state`
action, leaving "OS: Not collected" on freshly approved hosts even
after repeated button presses.

The same SSH session also fetches the remote `hostname` and, **only
when the stored hostname matches the canonical `host-<ip>`
discovery placeholder**, replaces it with the value the remote
reports. Operator-chosen names are never overwritten. On a name
collision with another host the rename is skipped silently (leaving
the placeholder in place) rather than mangling the fetched name
with a numeric suffix.

To make the placeholder reliably detectable, every "no real
hostname could be resolved" code path now emits the same
`host-<ip>` shape:

- `app.discovery.verify.verify_ssh` no longer synthesises the bare
  IP as a hostname fallback — it returns `None` and lets callers
  pick the placeholder. New helpers
  `placeholder_hostname(ip)` / `is_placeholder_hostname(name, ip)`
  centralise the format.
- `POST /api/discovery/add-hosts` (bulk-add) previously used the
  bare IP as the fallback hostname; now uses `host-<ip>` for
  consistency with the scan-approve path.
- The scan runner's `auto_add` branch now also creates the host
  (with placeholder) when SSH succeeded but no hostname could be
  resolved — previously these went to the pending queue with a
  misleading `"unknown error"` message.

Existing hosts that carry the old bare-IP fallback are left as-is
(rename in the UI to opt into auto-heal on the next collect).

### Changed

#### Action pack precedence: drop positional ordering, pure per-key pinning

**Breaking change to the action-pack precedence model.** The
`ActionPack.position` column and the drag-to-reorder UI on
`/action-packs` are gone. Each action key now has at most one
source pack chosen by an explicit operator pin (the
`action_resolution` table). Contested keys without a pin are
*unresolved* — the action is unrunnable until the operator picks a
winner. Uncontested keys still win automatically.

- **Schema migration `0004_drop_pack_position`** drops the
  `action_packs.position` column. Before dropping, the migration
  backfills `action_resolution` rows from the current
  `action_registry_snapshot` so existing positional defaults
  become explicit pins — operators see no behavioural change at
  upgrade time. Pins they don't want can be edited or deleted in
  the UI later. Downgrade re-adds the column with default 0
  (best-effort; perfect roundtrip impossible because pins may
  have been edited between up/down).
- **`POST /api/action-packs/reorder` deleted.** The endpoint and
  its `ActionPackReorderRequest` schema are gone.
- **`POST /api/action-packs/{id}/claim-all-keys` added.** Bulk-pin
  every key a pack contributes via this pack; returns
  `{created, updated, skipped}` counts. Idempotent.
  Confirmation dialog on `/action-packs` shows the diff before
  commit. Overwrites pins on other packs for the same keys.
- **`POST /api/actions/runs` rejects unresolved actions** with
  HTTP 409 and a clear message directing the operator to
  `/action-packs` to pick a winner.
- **`GET /api/actions/`** gains `winning_pack_id: int | None` and
  `unresolved: bool` on every `ActionDefinitionOut`. The frontend
  reads these to disable the Run button + show an Unresolved
  badge on action cards.
- **`GET /api/action-resolutions`** gains `is_unresolved: bool`
  on each row; `current_winner` is `null` when unresolved.
- **`/action-packs` page rewritten.** Top: Action Registry table
  (every action key, winner, inline radio picker when contested).
  Bottom: Pack Sources table (add/sync/edit/delete + "Make
  winner for all keys" button per row). The bundled pack appears
  as a read-only built-in row. No drag handles, no position
  column. Conflict resolution flows inline — the standalone
  `ConflictResolutionDialog` component is gone.
- **Run dialog and action cards.** When an action's
  `winning_pack_id` is null (and it's not a built-in), the action
  card shows an "Unresolved" badge with a link to `/action-packs`
  and the Run button is disabled. The Run dialog refuses to
  submit with the same message inline.

Migration is one-way safe — existing installs become explicit pins
that mirror the prior positional winner. The freeze-on-fresh-
conflict behaviour is unchanged (auto-pin previous winner; UI
surfaces "Frozen" until the operator confirms).

## [0.2.0] — 2026-05-13

### Added

#### Group-dispatch actions and one-click Kubernetes upgrade

Actions that declare `supports_host: false` are now dispatched as a
single `ansible-playbook` invocation against the whole group's flat
inventory, instead of fanning out per-host. Multi-node coordination
(`serial:`, `add_host`, `delegate_to`, `run_once`) lives entirely
inside the pack's own playbook — labdog has no notion of per-member
"roles" or cluster topology. The existing per-host fan-out is
unchanged for everything else.

- **`app.tasks.action_group.run_action_group`** — new Celery task
  for the group-dispatch path. Builds a flat `all` Ansible inventory
  of every member host, runs `ansible-playbook` once, routes per-host
  events back to per-host `ActionHostRun` rows by inventory hostname.
  The per-host advisory locks, audit log shape, and SSE streaming
  channel work the same as the per-host path; only the dispatch
  shape changes.
- **`POST /api/actions/runs`** — actions with `supports_host: false`
  reject host-target submissions with HTTP 400 and a clear message.
- **Production `k8s-upgrade` playbook** — the bundled pack ships
  the canonical kubeadm upgrade flow at
  `backend/app/ansible/actions/k8s-upgrade/`. Self-discovers
  control-plane vs worker nodes by probing every member for
  `/etc/kubernetes/manifests/kube-apiserver.yaml` in a setup play,
  then `serial: 1` upgrades control-plane nodes (first runs
  `kubeadm upgrade plan + apply`; subsequent run `kubeadm upgrade
  node`), then workers serially. `kubectl`-driven tasks (drain /
  uncordon / Ready-wait) delegate to the first control-plane node
  so no kubeconfig has to ship elsewhere. Apt-only for now;
  RHEL/Rocky/Alma support tracked in `TODO.md`.
- **Destructive group-dispatched actions get the same per-host
  snapshot/verify/rollback envelope as per-host actions.** Before
  the single ansible-playbook invocation, labdog snapshots every
  member with a Proxmox VM mapping. After: per-host verify
  (`verify_playbook` if declared, else built-in SSH/services/packages
  checks). Per-host rollback policy on failure — only hosts whose
  action OR verify failed get their snapshot reverted; successfully-
  upgraded hosts keep their state and their snapshots get cleaned up.
  The operator inspects the partial outcome and re-runs the action;
  pack idempotency carries the resumption.

### Changed

#### Action pack precedence: drop role, add position + per-key resolutions

The `default` / `override` role concept is gone. Pack precedence is now
a single linear `ActionPack.position` integer (higher wins; bundled
implicit at 0); operators reorder packs by drag-and-drop on the
**Action Packs** page, matching the firewall-rules UX. Per-key
conflicts have a dedicated resolution path so adding or syncing a
pack never silently flips behaviour.

- **`ActionPack.role` column dropped, `position` added.** Migration
  `e7b2c4f9a3d1` backfills positions in stable, behaviour-preserving
  order (today's local > override > default precedence). Local packs
  lose their implicit "always wins" status — operators can now demote
  a local pack below other packs.
- **`POST /api/action-packs/reorder`** — atomic full-list rewrite of
  pack positions. Submitted ids must match the current set exactly;
  the UI builds the body from its full sorted list.
- **`action_resolution` table + endpoints.** `GET/PUT/DELETE
  /api/action-resolutions[/{action_key}]` lets operators inspect
  contested keys and pin which pack wins each one. `pack_id NULL`
  pins bundled. Pack delete cascades — pinned-to-deleted-pack rows go
  away automatically.
- **`action_registry_snapshot` table + freeze-on-fresh-conflict.** The
  registry rebuild reads the snapshot of last-known winners; when a
  sync introduces a new manifest that turns a previously-uncontested
  key into a contested one, LabDog auto-pins the previous winner via
  an `action_resolution` row. Behaviour does not silently flip — the
  conflict banner on **Action Packs** flags frozen rows for operator
  review. Resetting a resolution clears the snapshot row so the next
  rebuild treats the key fresh.
- **Wizard now requires per-key picks.** When activating a repo whose
  packs collide with existing keys, the review step shows a
  per-key winner radio (one row per contested key). Activation
  rejects 409 if any contested key has no decision. The old
  pre-checked `role=override` semantics are gone — every contested
  key is an explicit operator choice.
- **`/action-packs` page rewrite.** Drag-to-reorder, info banner
  explaining priority, conflict banner that links to a
  per-key resolution dialog, no role radio in the Add/Edit form.
  Bundled is implicit (no row) — the info banner explains the
  ordering convention.

Drop the role concept outright (no deprecation shim). Existing
installs lose pack-level role configuration but keep the same
effective ordering on first boot via the migration backfill.

### Added

#### Coalesced per-host sync (option-c)

Replaces the seven independent per-module Celery sync tasks with one
orchestrator task per host that produces a single unified Ansible
playbook. Eliminates the per-host SSH race between concurrent module
syncs and unblocks bulk-sync UX.

- **New `POST /api/sync/hosts/{host_id}/bulk` endpoint** — sync any
  subset of modules (or all of them) for a host in one call.
  Validates `module_filter` (rejects empty list, unknown module names),
  is idempotent on the in-flight job (HTTP 200 with the existing
  `job_id` if a bulk sync is already pending or running for the host),
  and requires superuser auth (matches per-tab convention).
- **`run_host_sync` Celery task** — drives the full lifecycle:
  per-host serialisation via PostgreSQL advisory lock, atomic
  per-module status writes (`HostModuleStatus`), per-(sync_job)
  audit log emission with composite `module_outcomes` payload,
  tmpfs `/dev/shm` lifecycle, exception compensation, and
  dispatch-next-pending on completion.
- **Per-tab delegation** — the seven existing per-module sync tasks
  (`run_sync_playbook`, `service_sync.run_sync`, etc.) are now
  one-line delegators to `run_host_sync`. Same task names preserved
  for any external Celery clients; same per-module audit + status
  semantics. Sync triggered from any single-tab API call now goes
  through the unified orchestrator.
- **Pending-job queue** — sync requests against a host that already
  has a running sync are queued (status `pending`); the running
  task dispatches the oldest pending one when it finishes. UI sees
  the queued state immediately.
- **Stale-job sweeper** — periodic Celery beat task
  (`app.tasks.sync_sweeper.sweep_stale_syncs`, every 5 minutes)
  that finds `SyncJob` rows stuck in `running` for longer than
  30 minutes (2× the worst-case orchestrator timeout), flips
  them to `failed`, marks every seeded `HostModuleStatus` as
  `error`, emits a `sync_failed` audit row, and dispatches the
  queued successor. Closes the crash-recovery hole left open by
  the option-c chain: a worker dying mid-task no longer blocks
  the host's queue indefinitely.
- **`sync_triggered` audit events** — bulk and per-tab sync API
  endpoints now emit an audit row at the moment of trigger
  (separate from the existing `sync_completed` row at finish).

#### Schedulable actions

Folds the legacy `UpdateWorkflow` model into a unified `ScheduledAction`
that can schedule any registered action — pack-supplied or built-in —
against a host, a group, or the entire fleet.

- **New `ScheduledAction` model** at
  `app/models/scheduled_action.py` with `target_kind` (`host` /
  `group` / `fleet`), `target_id`, `action_key`, `parameters`,
  `schedule_cron`, plus the universal destructive-flow toggles
  (`snapshot_enabled`, `verify_enabled`, `auto_rollback`,
  `batch_size`). `action_runs` gets a nullable `scheduled_action_id`
  FK and mirrors of the three toggles so per-host executors see
  immutable run-time intent.
- **Three built-in pseudo-actions** (`_builtin.sync`,
  `_builtin.drift_check`, `_builtin.collect_state`) registered
  alongside pack-supplied actions in
  `app/actions/builtins.py`. The `_builtin.` namespace is reserved —
  pack manifests with underscore-prefixed keys are rejected at
  validation time. New `supports_fleet` capability flag on
  `ActionDefinition` and `ActionManifest`; opt-in only.
- **Unified scheduler** at
  `app/tasks/scheduled_action_schedule.py:check_due` (replaces
  `workflow_schedule.check_scheduled_workflows`). RedBeat ticks every
  60 s; `last_dispatched_at` is the cron walk's reference, so a
  missed tick doesn't fire-twice. Schedules with a non-terminal
  `ActionRun` are skipped — no duplicate dispatch.
- **Per-host built-in dispatchers** in
  `app/tasks/builtin_dispatchers.py` — thin wrappers that delegate
  to existing engines (`run_host_sync` for sync, the new
  `_check_drift_for_one_host` helper for drift, `collect_host_facts`
  for state) and write back `ActionHostRun.status`. `_builtin.sync`
  creates the SyncJob row option-c expects.
- **`POST /api/scheduled-actions/*` API** — CRUD plus run-now and
  run-history-list endpoints. Superuser-only. Cross-cutting
  validation enforces target compatibility (`supports_fleet/group/
  host`), cron syntax via `croniter.is_valid`, and parameter shape
  via the new `app.actions.validation.build_param_model` Pydantic
  dynamic-model builder shared with `POST /actions/runs`.
- **GitOps `scheduled_actions:` block** (replaces the legacy
  singleton `workflow:`). List-shaped, leave-alone-on-absence
  semantics: section absent ⇒ DB rows untouched; section present
  ⇒ delete-and-replace by `(target_kind='group', target_id, action_key)`.
- **Frontend** — rebuilt `/schedules` page with filter strip
  (Built-in / Pack / Target / enabled-only / search) and a kebab
  menu (Edit, Run now, View runs, Delete); shared
  `<ScheduleActionDialog>` 4-step wizard reachable from `/schedules`
  "+ New", action cards on host/group detail (preselects action),
  and a new "Schedules" tab on host & group detail (preselects
  target); the `<CronInput>` component posts to
  `/api/scheduled-actions/validate-cron` for live next-fire-times
  preview; new generic `/actions/runs/[runId]` route for fleet runs.

**Migration:** alembic backfills `update_workflows` rows into
`scheduled_actions` (target_kind=`group`, mapping
`pre_update_snapshot`→`snapshot_enabled`) and drops the legacy
`workflow_runs`, `workflow_host_runs`, `update_workflows` tables
plus the three Postgres enums. **Breaking:** the legacy
`/api/groups/{id}/workflow/*` endpoints are gone, the legacy YAML
`workflow:` block is dropped (re-shape on next push), the
`qemu-guest-agent` PackageRule auto-add side-effect is removed
(footgun), and `verification_prompt` / `auto_reboot` columns are
dropped (nothing read them). The dead
`workflow.schedule_check_interval_seconds` setting is gone — the
scheduler ticks at a hardcoded 60 s.

#### Documentation & process

- New top-level `ROADMAP.md` — high-altitude in-design / ideas /
  out-of-scope view, distinct from `TODO.md` (open near-term tasks).
- `CONTRIBUTING.md` documents the branch-scoped `plans/` workflow:
  drop plan files into `plans/` on a work branch, capture decisions
  in commit messages, delete `plans/` before merge. `dev` and
  `main` never carry it.

### Fixed

- Pre-existing bug in `app.rules.desired_state.get_desired_state`:
  short-circuited to `[]` when a host had no groups AND no host-level
  rules, skipping the auto-injected SSH lockout rule. Now always runs
  `merge_group_rules` so the lockout rule is unconditionally present
  on every code path (firewall sync, drift check, orchestrator).

### Security

- **SEC-03**: `POST /api/sync/hosts/{host_id}/bulk` now requires
  superuser (was authenticated-user). Matches the existing per-tab
  endpoint policy.
- **SEC-04**: SSH key tmpfs file is opened with `O_NOFOLLOW` to
  foreclose symlink-attack regressions.

### Internal

- Composer + 7 fragment adapters at `app.ansible_runtime.composer`:
  pure library code that wraps each per-module generator into a
  uniform `PlaybookFragment` and concatenates them in canonical
  order with module-tagged tasks and a `hosts` sentinel.
- `app.ansible_runtime.outcomes`: per-module outcome aggregator that
  resolves module identity from `event_data.play` on ansible-runner
  events.
- Firewall `get_effective_rules` / `get_effective_policies` moved
  from `app/api/rules.py` to `app/rules/merge.py` for consistency
  with the per-module pattern (cron, services, packages, …).

## [0.1.0] — 2026-04-26

First public release. LabDog is a small-fleet Linux configuration
manager: declare desired state per host group, push changes over SSH
via Ansible, watch for drift, run scheduled OS upgrades with
automatic snapshot + rollback, and (optionally) keep the whole thing
in git. The pieces below describe the surface as it exists at v0.1.0.

### Added

#### Host management
- Add hosts manually or via network discovery scans.
  `auto_add: false` puts new hits in a Pending Approval queue;
  `auto_add: true` admits them straight into the fleet.
- Per-host SSH connection (asyncssh), terminal embed in the UI.
- Auto-collected facts on add: OS family, codename, kernel, default
  NIC, firewall backend (`nftables` / `iptables`).
- Group membership with priorities; configuration merges with
  higher-priority groups winning.

#### Configuration modules
Eight modules, each with desired-state ↔ collected-state diffing,
SSH-pushed Ansible reconciliation, and a per-host detail tab:

- **Firewall** — `nftables` and `iptables`. Chain policies
  (input / output) plus per-rule `allow` / `deny` / `reject`.
  System-injected SSH-lockout-prevention rule that stays put across
  imports.
- **Systemd services** — managed `running` / `stopped`, `enabled`
  flag, override-only or full unit-content deploys with templated
  units. Protected services (sshd, networking, NetworkManager…)
  rejected at parse time.
- **Packages** — apt / dnf / yum, version pinning + holds, optional
  package repositories with GPG keys.
- **/etc/hosts entries** — literal IP-hostname pairs or
  `host_ref_id` cross-references that resolve at apply time.
- **Cron jobs** — per-user, with optional environment dict and
  schedule-string preservation.
- **DNS resolver** — `resolv.conf` / `systemd-resolved` /
  `NetworkManager` backends, DNS-over-TLS, options validation.
- **Linux users + groups** — `authorized_keys`, `supplementary_groups`,
  `sudo_rule`, shell, uid/gid pinning.
- **CA certificates** — bundle deploy + per-host override.

#### Actions
- Bundled action pack ships three actions: `linux-upgrade`,
  `linux-os-upgrade` (with `current_version` / `next_version`
  parameters), `k8s-upgrade`.
- DB-backed action packs: configure additional packs from the UI at
  `/action-packs`, sourced from a git repository (public, SSH key,
  or HTTPS PAT) or a local filesystem path. Packs sync at FastAPI
  lifespan + Celery `worker_ready`.
- Action manifest schema (`*.manifest.yml`) declares parameters,
  destructive flag, `verify_playbook`, supported scope (host /
  group), and version. Override semantics — pack role
  (`default` / `override`) derives a priority tier, admins never
  enter integers.
- Snapshot + verify + rollback wrap destructive actions when a
  Proxmox VM mapping exists for the host: pre-action snapshot,
  optional `verify_playbook` post-action, automatic rollback on
  failure.

#### Update workflows
- Per-group scheduled action runs at `/groups/{id}/workflow/`.
  Cron schedule, batch size, snapshot / rollback / reboot toggles,
  optional verification prompt. Action picker exposes any action
  registered in the live registry — including pack-supplied ones —
  with parameter inputs derived from the manifest.

#### GitOps
- Per-group YAML imports the eight configuration modules plus the
  per-group update workflow, under per-group PostgreSQL advisory
  locks.
- Optional `_global.yaml` at the repo root imports the global
  drift-check interval and any number of `ScanConfig` rows for
  network discovery, with cross-references resolved by name
  (`ssh_key: <name>`, `default_groups: [<name>, …]`).
- Webhook receivers for GitHub, GitLab, and Gitea (HMAC-verified
  per provider). Each module emits its own `gitops.import.*`
  audit event with before/after state.
- The UI mutation lock: with `gitops_enabled=true` on a group, all
  group-scoped mutation endpoints return 403 — git is the source of
  truth.
- Worked examples at [`docs/examples/gitops/`](docs/examples/gitops/).

#### Discovery
- Recurring network scans by CIDR with per-config schedule
  (`interval_minutes` XOR `cron_expression`), default group
  assignment for auto-added hosts, optional review queue for the
  rest. Rate-limited at 100k IP-checks/min.

#### Drift detection
- Configurable interval (1–1440 min, default 30) collects current
  host state via SSH and diffs against desired. Per-module status
  on the host detail page; fleet-level rollup on the dashboard.

#### Audit log
- Append-only `audit_log` table records every mutation across hosts,
  groups, modules, action runs, workflow runs, GitOps imports,
  scan-config approvals, and authentication events. User email
  resolved at query time.

#### Authentication
- `fastapi-users` with cookie + JWT, bcrypt password hashing.
  First-user-becomes-superuser bootstrap; subsequent users created
  by superusers from the Users page.

#### Operations
- AES-256-GCM at-rest encryption for SSH private keys, Proxmox API
  tokens, and HTTPS git credentials. Encryption key required at
  startup; insecure defaults rejected.
- Backup + restore guide at
  [`docs/backup-restore.md`](docs/backup-restore.md): `pg_dump`
  invocation, encryption-key handling, systemd timer + script,
  fresh-host restore, point-in-time restore, and disaster scenarios.
- Release artifacts (`.tar.gz` + `.deb` + `.rpm` + `SHA256SUMS`)
  built and attached to GitHub Releases automatically on `v*` tag
  push by the `release-artifacts` job in
  [`.github/workflows/ci.yml`](.github/workflows/ci.yml).
- systemd service unit shipped in
  [`packaging/systemd/`](packaging/systemd/) for production deploys
  on Debian / Ubuntu / Fedora / RHEL.

### Stack
- Backend: Python 3.12+, FastAPI, SQLAlchemy 2.0 (async), asyncpg,
  Celery + Redis (RedBeat scheduler), ansible-runner, asyncssh.
- Frontend: Next.js 16 (App Router), shadcn/ui, TanStack Query.
- Database: PostgreSQL 16.

### Known limitations at v0.1.0

- Per-host action execution only — multi-host coordination
  (one ansible-runner invocation against a host group) is parked,
  see [`TODO.md`](TODO.md).
- `nftables` and `iptables` only; no Cisco / pfSense / opnsense /
  Mikrotik backends.
- No encryption-key rotation tooling — recovery from a leaked key
  is a documented truncate-and-re-enter procedure (see
  [`docs/backup-restore.md`](docs/backup-restore.md)). Build a
  proper rotation runbook when an install with non-trivial
  credential inventory needs it.
- No upgrade guide — first public version, nothing to upgrade from.

### Security
- AGPL-3.0-or-later licence (see [`LICENSE`](LICENSE)).
- Vulnerability reporting via GitHub Private Vulnerability
  Reporting; see [`SECURITY.md`](SECURITY.md). Contributions are
  inbound-equals-outbound under AGPL — no CLA.

[Keep a Changelog]: https://keepachangelog.com/en/1.1.0/
[Semantic Versioning]: https://semver.org/spec/v2.0.0.html
[Unreleased]: https://github.com/open-labdog/labdog/compare/v0.9.0...HEAD
[0.9.0]: https://github.com/open-labdog/labdog/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/open-labdog/labdog/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/open-labdog/labdog/compare/v0.6.3...v0.7.0
[0.6.3]: https://github.com/open-labdog/labdog/compare/v0.6.2...v0.6.3
[0.6.2]: https://github.com/open-labdog/labdog/compare/v0.6.1...v0.6.2
[0.6.1]: https://github.com/open-labdog/labdog/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/open-labdog/labdog/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/open-labdog/labdog/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/open-labdog/labdog/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/open-labdog/labdog/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/open-labdog/labdog/compare/v0.2.5...v0.3.0
[0.2.0]: https://github.com/open-labdog/labdog/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/open-labdog/labdog/releases/tag/v0.1.0
