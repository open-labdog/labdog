# Security Policy

LabDog manages SSH keys, Proxmox API tokens, and pack credentials on
behalf of administrators. Security reports are taken seriously.

## Supported versions

LabDog is pre-release software. Security fixes land on `dev` first
and are included in the next tagged release. When tagged releases
exist, only the most recent minor version receives security
backports.

## Reporting a vulnerability

**Please report security issues privately, not through public GitHub
Issues.**

Use GitHub's private vulnerability reporting on the repository —
**Security → Advisories → Report a vulnerability**. This creates a
confidential discussion visible only to you and the maintainers.

### What to include

- A description of the vulnerability and its impact.
- Reproduction steps or a proof-of-concept.
- The version (tag or commit) you tested against.
- Your name and optional contact for coordinated disclosure credit.

### Response expectations

- Acknowledgement within **72 hours**.
- An initial assessment within **7 days** (severity, whether we
  treat it as in-scope, planned timeline).
- Fixes prioritised by severity: critical issues target a patch
  within 30 days; lower-severity issues land in the next regular
  release.
- Coordinated disclosure: we publish a Security Advisory with
  credit to the reporter (unless anonymity is requested) once a
  fix is available.

### In scope

- The LabDog backend (FastAPI) and its Celery workers.
- The LabDog frontend (Next.js static export).
- The action-pack subsystem, including credential handling on
  linked Git repositories.
- Authentication, session handling, and the SSH / web-terminal
  paths.
- The Ansible-runner playbook execution pipeline.
- SQL injection, SSRF, CSRF, auth bypass, privilege escalation,
  deserialisation, or secret leakage in any of the above.

### Out of scope

- Denial-of-service by sheer load against a self-hosted instance
  (run behind a reasonable reverse proxy).
- Social engineering against maintainers.
- Vulnerabilities in third-party dependencies without a proof of
  exploitation in LabDog — file those with the upstream project
  instead; we'll track the CVE and update when a fix is available.
- Issues requiring physical access to the LabDog host.
- Findings on deployments not running an official release (modified
  forks, source-built commits other than tagged versions).

### Safe harbor

Good-faith research conducted per this policy will not be pursued
legally. "Good faith" means:

- You didn't access, modify, or delete data beyond what was necessary
  to demonstrate the issue.
- You didn't disrupt service for other users.
- You reported promptly and gave us a reasonable window before
  public disclosure.

## Known risk areas to consider when reporting

If you're auditing, these are the areas most worth scrutinising:

- **Credential handling at rest and in transit.** SSH private keys,
  Proxmox tokens, HTTPS PATs, Grafana bearer tokens, and SSH
  known-hosts all touch the
  `encrypted_*` columns in the DB (AES-256-GCM) and the
  `app.crypto.encryption` pipeline. Any path that could leak one
  of these to logs, API responses, or `git config` is interesting.
- **The action-pack git sync.** Action packs clone arbitrary git
  repositories the admin points them at. Make sure URLs, tokens,
  and SSH keys don't leak through ansible-runner output or error
  paths.
- **Ansible playbook execution.** Playbooks are executed against
  managed hosts over SSH. Any path where an untrusted YAML file
  could gain execution on the LabDog host itself (not just the
  target) is serious.
- **The web-shell.** `asyncssh` WebSocket bridge lets users open
  interactive SSH sessions. Authentication scope and channel
  lifecycle matter.
