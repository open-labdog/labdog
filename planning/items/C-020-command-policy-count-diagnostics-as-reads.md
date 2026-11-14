# C-020: Command policy: count diagnostics as reads

| | |
|---|---|
| Edition | community |
| Source | roadmap, board item "Command policy: operator-editable rules, and diagnostics counted as reads" (second half) |
| Tier / score | P2 / 8.0 |
| Stage | 8 |
| Effort | S |
| Autonomy | A |
| Depends on | none |

## Goal
Common diagnostic commands that only read, such as a web server's configuration test, run without approval and are not counted as changes, so they take no snapshot and do not use up an alert's cooldown or daily cap.

## Scope
In scope:
- New `read_only` entries, with option tables and gates, in the shipped policy file for the diagnostics listed below.
- Tests for each form, including the forms that must stay changes.

Out of scope:
- Operator-editable rules (C-019).
- Any change to the classifier's logic in `policy.py` or `safety.py`, unless a listed form cannot be expressed in the file.
- Commands that dump configuration which may hold secrets (see the list).

## Context
- `backend/app/ai/command_policy.yaml` (1440 lines) lists what is read-only: `read_only` paths with a mode (`any`, `operands`, `exact`), `options` tables, `gates`, `wrappers` (sudo, doas, env, timeout) and `environment`. The header comment explains each. The rule: a command is read-only only in the form the file lists.
- `backend/app/ai/policy.py`: `CommandPolicy.from_data` (line 496) validates and builds the tree; `NEVER_READ_ONLY` (line 41) lists shells, interpreters and exec wrappers that can never be read-only; `DEFAULT = CommandPolicy.load()` (line 920). `backend/app/ai/safety.py`: `classify_command` (line 430).
- The board's report, from the live test on 2026-10-04: `nginx -t`, `nginx -V`, `last -x` and `sudo journalctl -k` were classified as writes, so at full auto each took a Proxmox snapshot and read-only sessions were counted as having changed the host.
- What the current file already does, checked by running `classify_command` on this branch:
  - Already read-only: `last -x`, `sudo last -x`, `journalctl -k`, `sudo journalctl -k`, `sudo -n journalctl -k -n 50 --no-pager`, `sudo dmesg`, `kubeadm version`.
  - Still classified as changes ("is not a known read-only command"): `nginx -t`, `nginx -V`, `nginx -v`, `nginx -T`, `sudo nginx -t`, `apachectl -t`, `apache2ctl configtest`, `sshd -t`, `sudo sshd -T`, `visudo -c`, `haproxy -c -f <file>`, `named-checkconf`, `postfix check`, `systemd-analyze verify <unit>`, `systemd-analyze blame`, `php-fpm -t`, `lastb`.
- So half of the board's four examples are fixed already, and the remaining work is the service configuration tests.
- Tests: `backend/tests/ai/test_command_policy.py` holds long parametrised lists of read-only and not-read-only commands; `tests/ai/test_safety.py` and `tests/ai/test_safety_vs_bash.py` cover the classifier.

## Dependencies
- None. If C-019 lands first, nothing changes here: the shipped file stays the default.

## Implementation steps
1. Add these forms to `read_only` in `command_policy.yaml`, each as `exact` with the options named, so any other option or operand makes the command a change. Check every option against the tool's own `--help` or man page, as the file's header requires.
   - `nginx`: flags `-t`, `-q`, `-v`, `-V`; value options `-c`, `-p`, `-g` are not allowed (`-g` sets directives, `-c` and `-p` point the test at arbitrary files). `-T` is left out: it prints the whole configuration, which can include secrets. `-s` (signal) must stay a change.
   - `apachectl` and `apache2ctl`: `-t`, `configtest`, `-v`, `-V`, `-S`, `-M`, and `-t -D DUMP_VHOSTS` / `-D DUMP_MODULES` if the option table can restrict the value; otherwise leave `-D` out.
   - `httpd` and `apache2`: `-t`, `-v`, `-V`, `-S`, `-M`.
   - `sshd`: `-t` only. `-T` prints the effective configuration and is left out for the first cut.
   - `visudo`: `-c` only, without `-f`.
   - `haproxy`: `-c` with `-f <path>` and `-v`, `-vv`. No other option.
   - `named-checkconf`: no operands, or one path, without `-p` (prints the configuration including keys).
   - `postfix check` and `postconf -n` are left out: `postfix check` can create missing directories. Record that in a comment.
   - `systemd-analyze`: subcommands `blame`, `time`, `critical-chain`, `verify`, `security` (the last two with unit operands), mode `operands`.
   - `php-fpm` and versioned names cannot be matched by a fixed word unless the path syntax supports a trailing `*` on the command name; if it does not, leave PHP out and note it.
   - `lastb`: `any`, like `last`.
2. Where a command already has an entry (`journalctl`, `last`, `dmesg`), change nothing.
3. Add a short comment block above the new entries: these are configuration tests that parse files and exit, and the options left out and why.
4. Tests in `tests/ai/test_command_policy.py`: add every allowed form, with and without `sudo` and `sudo -n`, to the read-only list. Add to the not-read-only list: `nginx -s reload`, `nginx -T`, `nginx -t -g 'daemon off;'`, `nginx -c /tmp/x -t`, `apachectl graceful`, `apachectl restart`, `sshd -T`, `sshd -D`, `visudo -c -f /tmp/x`, `visudo`, `haproxy -f /etc/haproxy/haproxy.cfg`, `named-checkconf -p`, `systemd-analyze set-log-level debug`, `postfix check`.
5. Run the policy loader on the edited file (`CommandPolicy.load()` at import) and the whole `tests/ai` policy and safety suites.

## Acceptance criteria
- `classify_command` returns `read_only` for: `nginx -t`, `sudo nginx -t`, `nginx -V`, `nginx -v`, `apachectl -t`, `apache2ctl configtest`, `sshd -t`, `sudo sshd -t`, `visudo -c`, `haproxy -c -f /etc/haproxy/haproxy.cfg`, `named-checkconf`, `systemd-analyze blame`, `systemd-analyze verify nginx.service`, `lastb`.
- It still returns `read_only` for the four board examples (`nginx -t`, `nginx -V`, `last -x`, `sudo journalctl -k`).
- It returns `mutating` for every command in step 4's second list.
- At `read_only` autonomy `nginx -t` runs; at `approval` it runs without an approval card; at `full_auto` it takes no snapshot and does not count as a change for the cooldown and daily cap.
- The policy file loads without `PolicyError`.

## Test plan
- `cd backend && source .venv/bin/activate && pytest tests/ai/test_command_policy.py tests/ai/test_safety.py tests/ai/test_safety_vs_bash.py tests/ai/test_ssh_tool_paths.py -q`, then the full suite once before the stage review.
- Running stack on lin-manager-2, host named "test" with nginx installed: in a read-only chat ask the assistant to test the nginx configuration and confirm the command runs and the session is not marked as having changed the host. Confirm `nginx -s reload` is refused at read-only.

## Docs and changelog
- `docs/ui/assistant.md`: add the service configuration tests to the examples of commands that need no approval, and name `nginx -T` and `sshd -T` as deliberately excluded.
- `CHANGELOG.md` `[Unreleased]`, Changed: "**More diagnostics count as reads.** Configuration tests for nginx, Apache, sshd, sudoers, HAProxy and BIND, `systemd-analyze` reports and `lastb` run without approval and take no snapshot."

## Risks and rollback
- A rule that is too wide makes a write look like a read, which skips approval and the snapshot. That is why each entry is `exact` with a short option list and why each has negative tests.
- `nginx -t` opens its log and pid paths and can create an empty log file owned by root if one is missing. That is a known side effect of the tool and is accepted here; say so in the comment.
- Rollback: revert the YAML and test changes.

## Assumptions
- Assumption (not yet confirmed): the list of diagnostics above. The board names only four commands and says "the common diagnostic forms". Question for Dennis: any tool to add or drop?
- Assumption (not yet confirmed): commands that print whole configurations (`nginx -T`, `sshd -T`, `named-checkconf -p`) stay out because of the secrets gate's purpose (SEC-47), even though they do not change the host.

## Definition of done
- [ ] Policy entries and tests merged; policy and safety suites green; full backend suite green.
- [ ] Docs and `CHANGELOG.md` updated.
- [ ] Stage 8 behaviour 3 verified on a host named "test".
- [ ] `planning/roadmap.yaml` status set, and a line added to `planning/PROGRESS.md`.
