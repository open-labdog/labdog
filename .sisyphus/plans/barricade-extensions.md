# Barricade Extensions — Feature Expansion Plan

## TL;DR

> **Quick Summary**: Extend Barricade from a firewall-only tool into a full **Linux node configuration management platform** — covering services, users, certificates, /etc/hosts, DNS resolver, and more. Each extension follows the same pattern as the firewall module: DB as truth, Ansible as enforcement, React UI for management, drift detection, audit log.

---

## Design Philosophy

Every extension shares the same architecture:
1. **DB model** — desired state stored in PostgreSQL
2. **Ansible renderer** — desired state → playbook tasks
3. **Drift detector** — actual state fetched from host, compared to desired
4. **Sync engine** — apply desired state via Celery + ansible-runner
5. **Audit log** — every change recorded with before/after state
6. **React UI** — manage desired state, preview diff, trigger sync

This is the "Barricade pattern". Each extension is a new module that plugs into the existing infrastructure.

---

## Extension Modules

---

### Module 1: Service Management

**What it does**: Manage systemd services across hosts — ensure services are running/stopped/enabled/disabled. Replace ad-hoc `systemctl` commands with a centralized UI.

**Use cases**:
- Ensure `nginx` is running and enabled on all web servers
- Ensure `mysql` is stopped on hosts that shouldn't run it
- Restart services after config changes
- Detect services that have crashed (drift: desired=running, actual=stopped)

**DB Model**:
```
ServiceRule:
  id, group_id (FK), service_name (e.g. "nginx"),
  state (enum: running | stopped | restarted | reloaded),
  enabled (bool — systemd enable/disable),
  priority (int), comment, is_system (bool),
  created_at, updated_at
```

**Ansible Renderer**:
```yaml
- name: Ensure nginx is running and enabled
  ansible.builtin.service:
    name: nginx
    state: started
    enabled: true
```

**Drift Detection**: `systemctl is-active {service}` + `systemctl is-enabled {service}`

**UI**: Service list per group, status badges (running/stopped/failed), toggle enabled/disabled, restart button

**Complexity**: Low — `ansible.builtin.service` is simple and well-understood.

---

### Module 2: User & Group Management

**What it does**: Manage Linux users and groups across hosts — create/delete users, manage SSH authorized keys, set sudo rules, manage group memberships.

**Use cases**:
- Ensure `deploy` user exists on all production hosts with specific SSH key
- Remove departed employee's account from all hosts
- Grant/revoke sudo access
- Manage `/etc/sudoers.d/` entries

**DB Model**:
```
LinuxUser:
  id, group_id (FK), username, uid (nullable — auto-assign if null),
  shell (default: /bin/bash), home_dir, comment,
  state (enum: present | absent),
  sudo_rule (nullable — e.g. "ALL=(ALL) NOPASSWD: /usr/bin/systemctl"),
  authorized_keys (JSONB array of public key strings),
  groups (JSONB array of supplementary group names),
  created_at, updated_at

LinuxGroup:
  id, group_id (FK), groupname, gid (nullable),
  state (enum: present | absent),
  created_at, updated_at
```

**Ansible Renderer**:
```yaml
- name: Ensure deploy user exists
  ansible.builtin.user:
    name: deploy
    state: present
    shell: /bin/bash
    groups: [docker, sudo]
    
- name: Set authorized keys for deploy
  ansible.posix.authorized_key:
    user: deploy
    key: "ssh-ed25519 AAAA..."
    state: present
    exclusive: true
```

**Drift Detection**: `getent passwd {user}` + `cat /home/{user}/.ssh/authorized_keys`

**Security Notes**:
- Never store private keys — only public keys in authorized_keys
- Sudo rules validated against safe patterns (no `NOPASSWD: ALL` without explicit override)
- Deletion of users with running processes: warn, don't auto-delete

**UI**: User list per group, SSH key management (add/remove public keys), sudo rule editor, group membership

**Complexity**: Medium — user management has edge cases (UID conflicts, home dir ownership).

---

### Module 3: TLS Certificate Management

**What it does**: Manage TLS certificates on hosts — deploy certificates from Let's Encrypt (via ACME), self-signed, or uploaded PEM files. Track expiry, auto-renew, deploy to services.

**Use cases**:
- Deploy Let's Encrypt cert for `api.example.com` to all API servers
- Track certificate expiry across fleet (alert when < 30 days)
- Rotate certificates without downtime (deploy new cert, reload nginx)
- Manage internal CA certificates

**DB Model**:
```
Certificate:
  id, name, domain (or CN), san_domains (JSONB array),
  source (enum: letsencrypt | uploaded | self_signed),
  cert_pem (encrypted, nullable — for uploaded),
  key_pem (encrypted, nullable — for uploaded),
  acme_account_key_id (FK to SSHKey — reuse encryption),
  acme_challenge (enum: http01 | dns01),
  deploy_path_cert (e.g. /etc/ssl/certs/api.pem),
  deploy_path_key (e.g. /etc/ssl/private/api.key),
  reload_service (nullable — e.g. "nginx"),
  expires_at (datetime, updated after each renewal),
  auto_renew (bool, default True),
  created_at, updated_at

HostCertificate:
  host_id, certificate_id, deployed_at, deployed_sha256
```

**Ansible Renderer**:
```yaml
- name: Deploy TLS certificate
  ansible.builtin.copy:
    content: "{{ cert_pem }}"
    dest: /etc/ssl/certs/api.pem
    owner: root
    group: ssl-cert
    mode: "0644"
    
- name: Deploy TLS private key
  ansible.builtin.copy:
    content: "{{ key_pem }}"
    dest: /etc/ssl/private/api.key
    owner: root
    group: ssl-cert
    mode: "0640"
    
- name: Reload nginx after cert deploy
  ansible.builtin.service:
    name: nginx
    state: reloaded
```

**Drift Detection**: `openssl x509 -in {cert_path} -noout -fingerprint` — compare SHA256 fingerprint

**Expiry Monitoring**: Celery beat task checks `expires_at` daily, sets status to `expiring_soon` (< 30 days) or `expired`

**ACME Integration**: Use `acme.sh` or `certbot` via Ansible, or implement ACME protocol directly with `acme-tiny` Python library

**UI**: Certificate list with expiry countdown badges, deploy status per host, manual renew button, upload PEM form

**Complexity**: High — ACME protocol, key security, service reload coordination.

---

### Module 4: /etc/hosts Management

**What it does**: Manage `/etc/hosts` entries across hosts — add/remove/update hostname-to-IP mappings. Useful for internal service discovery without DNS.

**Use cases**:
- Add `db.internal → 10.0.1.5` to all app servers
- Update IP when a service moves to a new host
- Remove stale entries
- Manage split-horizon DNS workarounds

**DB Model**:
```
HostsEntry:
  id, group_id (FK), ip_address, hostname, aliases (JSONB array),
  comment, priority (int), is_system (bool — for entries Barricade needs),
  created_at, updated_at
```

**Ansible Renderer**:
```yaml
- name: Manage /etc/hosts entries
  ansible.builtin.lineinfile:
    path: /etc/hosts
    regexp: "^{{ ip }}\\s"
    line: "{{ ip }} {{ hostname }} {{ aliases | join(' ') }}"
    state: present
```

Or for full file management (safer):
```yaml
- name: Write /etc/hosts
  ansible.builtin.template:
    src: hosts.j2
    dest: /etc/hosts
    owner: root
    group: root
    mode: "0644"
    validate: "grep -q '127.0.0.1 localhost' %s"
```

**Drift Detection**: Slurp `/etc/hosts`, parse, compare to desired entries

**Safety**: Always preserve `127.0.0.1 localhost` and `::1 localhost` — these are system entries (is_system=True)

**UI**: Hosts entries table per group, add/edit/delete entries, preview of full `/etc/hosts` file

**Complexity**: Low — simple file management.

---

### Module 5: DNS Resolver Configuration

**What it does**: Manage DNS resolver settings on hosts — configure `/etc/resolv.conf`, `systemd-resolved`, or `NetworkManager` DNS settings.

**Use cases**:
- Set all hosts to use internal DNS servers (10.0.0.53)
- Configure search domains (`search internal.example.com`)
- Switch between DNS providers (Cloudflare, Google, internal)
- Configure DNS-over-TLS

**DB Model**:
```
ResolverConfig:
  id, group_id (FK),
  nameservers (JSONB array of IPs, ordered),
  search_domains (JSONB array),
  options (JSONB — e.g. {"ndots": 5, "timeout": 2}),
  resolver_type (enum: resolv_conf | systemd_resolved | networkmanager),
  dns_over_tls (bool, default False),
  created_at, updated_at
```

**Ansible Renderer**:
```yaml
# For resolv.conf
- name: Configure DNS resolver
  ansible.builtin.template:
    src: resolv.conf.j2
    dest: /etc/resolv.conf
    owner: root
    group: root
    mode: "0644"

# For systemd-resolved
- name: Configure systemd-resolved
  ansible.builtin.template:
    src: resolved.conf.j2
    dest: /etc/systemd/resolved.conf
  notify: Restart systemd-resolved
```

**Drift Detection**: Slurp `/etc/resolv.conf` or `resolvectl status`, compare nameservers

**UI**: Resolver config form per group, nameserver list (drag to reorder), search domain list

**Complexity**: Medium — three different resolver backends, DNS-over-TLS setup.

---

### Module 6: Cron Job Management

**What it does**: Manage cron jobs across hosts — add/remove/update scheduled tasks. Centralize cron management instead of SSH-ing to each host.

**Use cases**:
- Deploy backup cron job to all database servers
- Remove deprecated cron jobs
- Ensure log rotation runs at consistent times
- Manage `/etc/cron.d/` files

**DB Model**:
```
CronJob:
  id, group_id (FK), name (slug), user (default: root),
  schedule (cron expression, e.g. "0 2 * * *"),
  command, environment (JSONB — env vars for the job),
  state (enum: present | absent),
  comment, created_at, updated_at
```

**Ansible Renderer**:
```yaml
- name: Manage backup cron job
  ansible.builtin.cron:
    name: "daily-backup"
    user: root
    minute: "0"
    hour: "2"
    job: "/usr/local/bin/backup.sh >> /var/log/backup.log 2>&1"
    state: present
```

**Drift Detection**: `crontab -l -u {user}` + parse `/etc/cron.d/`

**UI**: Cron job list per group, cron expression builder (human-readable: "Every day at 2am"), next-run preview

**Complexity**: Low — `ansible.builtin.cron` is straightforward.

---

### Module 7: Package Management

**What it does**: Ensure specific packages are installed or absent across hosts. Manage package versions, repositories, and GPG keys.

**Use cases**:
- Ensure `nginx=1.24.*` is installed on all web servers
- Remove `telnet` from all hosts (security hardening)
- Add custom APT/YUM repository
- Pin package versions to prevent unintended upgrades

**DB Model**:
```
PackageRule:
  id, group_id (FK), package_name, version (nullable — "latest" or specific),
  state (enum: present | absent | latest),
  package_manager (enum: apt | yum | dnf | auto),
  priority (int), comment, created_at, updated_at

PackageRepository:
  id, group_id (FK), name, url, key_url (nullable),
  distribution, components, state (enum: present | absent),
  created_at, updated_at
```

**Ansible Renderer**:
```yaml
- name: Install nginx
  ansible.builtin.package:
    name: nginx=1.24.*
    state: present
    
- name: Remove telnet
  ansible.builtin.package:
    name: telnet
    state: absent
```

**Drift Detection**: `dpkg -l {package}` or `rpm -q {package}` — check installed version

**UI**: Package list per group, version constraint input, installed/absent toggle, repository management

**Complexity**: Medium — package manager detection, version pinning syntax varies by distro.

---

### Module 8: Sysctl / Kernel Parameters

**What it does**: Manage kernel parameters via `sysctl` — network tuning, security hardening, performance settings.

**Use cases**:
- Enable IP forwarding on router hosts (`net.ipv4.ip_forward = 1`)
- Harden kernel (`kernel.dmesg_restrict = 1`)
- Tune TCP stack for high-throughput servers
- Disable IPv6 on specific hosts

**DB Model**:
```
SysctlRule:
  id, group_id (FK), key (e.g. "net.ipv4.ip_forward"),
  value (string), persistent (bool — write to /etc/sysctl.d/),
  comment, priority (int), created_at, updated_at
```

**Ansible Renderer**:
```yaml
- name: Set kernel parameters
  ansible.posix.sysctl:
    name: net.ipv4.ip_forward
    value: "1"
    state: present
    reload: true
    sysctl_file: /etc/sysctl.d/99-barricade.conf
```

**Drift Detection**: `sysctl {key}` — compare current value to desired

**UI**: Sysctl parameter list per group, key/value editor, common presets (security hardening, network tuning)

**Complexity**: Low — `ansible.posix.sysctl` is simple.

---

### Module 9: File & Directory Management

**What it does**: Manage specific files and directories across hosts — deploy config files, set permissions, create directories, manage symlinks.

**Use cases**:
- Deploy `/etc/nginx/conf.d/barricade.conf` to all web servers
- Ensure `/var/log/app/` exists with correct permissions
- Create symlinks for service configs
- Deploy SSH daemon config (`/etc/ssh/sshd_config`)

**DB Model**:
```
ManagedFile:
  id, group_id (FK), path (e.g. /etc/nginx/conf.d/app.conf),
  content (text — Jinja2 template supported),
  owner, group_owner, mode (e.g. "0644"),
  state (enum: present | absent | directory | symlink | touch),
  symlink_target (nullable),
  validate_cmd (nullable — e.g. "nginx -t -c %s"),
  notify_service (nullable — restart this service after change),
  created_at, updated_at
```

**Ansible Renderer**:
```yaml
- name: Deploy nginx config
  ansible.builtin.copy:
    content: "{{ file_content }}"
    dest: /etc/nginx/conf.d/app.conf
    owner: root
    group: root
    mode: "0644"
    validate: "nginx -t -c %s"
  notify: Reload nginx
```

**Drift Detection**: Slurp file, compare SHA256 hash to desired content hash

**UI**: File editor with syntax highlighting (Monaco editor), permission picker, validate command input, diff view

**Complexity**: Medium — Jinja2 templating, validate commands, service notifications.

---

## Cross-Cutting Concerns

### Shared Infrastructure (already exists in Barricade)
All extensions reuse:
- **RBAC**: per-group viewer/editor/admin roles
- **Audit log**: every change recorded
- **Drift detection**: Celery beat + manual trigger
- **Sync engine**: Celery + ansible-runner
- **SSH key management**: encrypted keys for host access
- **Plan-before-apply**: diff preview before sync

### New Shared Components Needed

**1. Multi-module Sync**
Currently sync is firewall-only. Need a `full_sync` that applies ALL modules for a host in one playbook:
```
firewall rules → service states → user accounts → certs → /etc/hosts → resolver → cron → packages → sysctl → files
```
Order matters (e.g., install packages before configuring services).

**2. Module Enable/Disable per Group**
Each group should have a `enabled_modules` list. Don't apply service management to a group that only needs firewall rules.

**3. Playbook Ordering**
Define execution order for multi-module sync:
1. Package installation (packages must exist before services)
2. User/group creation (users must exist before file ownership)
3. File deployment (configs before service start)
4. Service management (start after config)
5. Firewall rules (last — don't lock yourself out)
6. Sysctl (can be any time)
7. Cron jobs (can be any time)
8. /etc/hosts + resolver (can be any time)

**4. Dependency Graph**
Some modules depend on others:
- Service management depends on Package management (can't start nginx if not installed)
- File management depends on User management (file owner must exist)
- Certificate deployment depends on Service management (reload nginx after cert deploy)

---

## Implementation Plans (Individual Files)

| Priority | Module | Plan File | Status |
|----------|--------|-----------|--------|
| 1 | Service Management | [`ext-service-management.md`](ext-service-management.md) | Ready (10 tasks) |
| 2 | Linux User Management | [`ext-linux-user-management.md`](ext-linux-user-management.md) | Ready (10 tasks, depends on #1) |
| 3 | /etc/hosts Management | [`ext-etc-hosts.md`](ext-etc-hosts.md) | Ready (10 tasks, depends on #1) |
| 4 | Package Management | [`ext-package-management.md`](ext-package-management.md) | Ready (10 tasks, depends on #1) |
| 5 | Sysctl | _Not yet planned_ | — |
| 6 | Cron Jobs | [`ext-cron-jobs.md`](ext-cron-jobs.md) | Ready (10 tasks, depends on #1) |
| 7 | DNS Resolver | [`ext-dns-resolver.md`](ext-dns-resolver.md) | Ready (10 tasks, depends on #1) |
| 8 | File Management | _Not yet planned_ | — |
| 9 | TLS Certificates | _Not yet planned_ | — |

### Execution Order
Service Management MUST be built first — it creates shared infrastructure (`host_module_status` table, `SyncJob.module_type` column) that all subsequent modules reuse. Linux User Management depends on Service Management being complete.

---

## Naming Consideration

As Barricade expands beyond firewalls, consider:
- **Barricade** stays as the product name (it's a good brand)
- The firewall module becomes "Barricade Firewall"
- New modules are "Barricade Services", "Barricade Users", etc.
- Or: rename to **Bastion** (broader infrastructure management connotation)

---

## Estimated Effort per Module

| Module | Backend | Frontend | Tests | Total |
|--------|---------|----------|-------|-------|
| Services | S | S | S | S |
| Users | M | M | M | M |
| /etc/hosts | S | S | S | S |
| Packages | M | M | M | M |
| Sysctl | S | S | S | S |
| Cron | S | S | S | S |
| Resolver | M | S | S | M |
| Files | L | L | M | L |
| Certificates | XL | L | L | XL |

**Total**: ~6-8 months of development for all 9 modules (1 developer)
**MVP (top 4)**: ~2 months

---

## What NOT to Build

- ❌ **Puppet/Chef/Salt replacement** — Barricade is opinionated and UI-first, not a general-purpose CM tool
- ❌ **Container management** — that's Kubernetes/Nomad territory
- ❌ **Application deployment** — use CI/CD tools for that
- ❌ **Monitoring/alerting** — use Prometheus/Grafana
- ❌ **Secret management** — use Vault/SOPS
- ❌ **Network topology management** — use Netbox
- ❌ **Cloud resource management** — use Terraform
