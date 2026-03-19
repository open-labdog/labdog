# Precedence & Merge Examples

Concrete examples showing how Barricade merges group-level and host-level
configurations when a host belongs to multiple groups.

## Setup

All examples below use this group and host layout:

```
Group: "base-security"     priority: 10    (lowest)
Group: "web-servers"       priority: 50
Group: "production"        priority: 100   (highest)

Host: "web-prod-01"        member of: base-security, web-servers, production
Host: "web-staging-01"     member of: base-security, web-servers
```

---

## 1. Firewall Rules

Firewall rules are group-level only (no host overrides). Two rules are
considered "the same" when their **signature** matches: protocol + direction +
port range + source CIDR + destination CIDR.

### Example: Overlapping SSH rules

```
Group "base-security" (priority 10):
  - ALLOW tcp input port 22 from 0.0.0.0/0      comment: "SSH from anywhere"

Group "production" (priority 100):
  - ALLOW tcp input port 22 from 10.0.0.0/8      comment: "SSH internal only"
```

These have **different signatures** (different `source_cidr`), so both rules
appear in the effective ruleset. They are not considered duplicates.

```
Effective rules for web-prod-01:
  1. [SYSTEM] ALLOW tcp input port 22 from 192.168.1.5  (SSH lockout prevention, priority 999999)
  2. ALLOW tcp input port 22 from 10.0.0.0/8            (from "production")
  3. ALLOW tcp input port 22 from 0.0.0.0/0             (from "base-security")
```

### Example: Exact duplicate rules

```
Group "base-security" (priority 10):
  - DENY any input from 0.0.0.0/0                comment: "Default deny"

Group "web-servers" (priority 50):
  - DENY any input from 0.0.0.0/0                comment: "Deny all inbound"
```

Same signature (same protocol, direction, port range, source, dest). The
higher-priority group wins. The lower-priority duplicate is discarded.

```
Effective rules for web-prod-01:
  - DENY any input from 0.0.0.0/0                (from "web-servers", priority 50 wins)
  # The "base-security" version is silently dropped.
```

### Example: Non-overlapping rules merge additively

```
Group "base-security" (priority 10):
  - ALLOW icmp input                              comment: "Ping"

Group "web-servers" (priority 50):
  - ALLOW tcp input port 80                       comment: "HTTP"
  - ALLOW tcp input port 443                      comment: "HTTPS"
```

Different signatures, so all three rules appear in the effective set.

```
Effective rules for web-prod-01:
  1. [SYSTEM] ALLOW tcp input port 22 from 192.168.1.5  (SSH lockout, always first)
  2. ALLOW tcp input port 80                             (from "web-servers")
  3. ALLOW tcp input port 443                            (from "web-servers")
  4. ALLOW icmp input                                    (from "base-security")
```

---

## 2. Services

Identity key: `service_name`. Host-level overrides fully replace the group
entry for that service.

### Example: Group priority wins

```
Group "base-security" (priority 10):
  - nginx: state=stopped, enabled=false

Group "web-servers" (priority 50):
  - nginx: state=running, enabled=true
```

"web-servers" has higher priority, so its version of `nginx` wins.

```
Effective services for web-prod-01:
  - nginx: running, enabled=true                 (from "web-servers")
```

### Example: Host override beats all groups

```
Group "web-servers" (priority 50):
  - nginx: state=running, enabled=true

Group "production" (priority 100):
  - nginx: state=running, enabled=true

Host "web-prod-01" override:
  - nginx: state=stopped, enabled=false
```

Host override always wins, regardless of group priority.

```
Effective services for web-prod-01:
  - nginx: stopped, enabled=false                (from host override)
```

### Example: Mixed sources

```
Group "base-security" (priority 10):
  - fail2ban: state=running, enabled=true

Group "web-servers" (priority 50):
  - nginx: state=running, enabled=true

Host "web-prod-01" override:
  - redis: state=running, enabled=true
```

No conflicts. All three services appear, each from a different source.

```
Effective services for web-prod-01:
  - fail2ban: running, enabled=true              (from "base-security")
  - nginx: running, enabled=true                 (from "web-servers")
  - redis: running, enabled=true                 (from host override)
```

Note: services not listed here (e.g. `sshd`, `cron`) are left untouched on
the host. Barricade only manages what you explicitly define.

---

## 3. /etc/hosts

Identity key: `ip_address`. Host-level overrides fully replace the group
entry for that IP. System entries (`127.0.0.1`, `::1`) are always injected.

### Example: Conflicting IP mappings

```
Group "base-security" (priority 10):
  - 10.0.0.5  →  old-db.internal

Group "production" (priority 100):
  - 10.0.0.5  →  primary-db.internal
```

Same IP address = same identity key. Higher-priority group wins.

```
Effective /etc/hosts for web-prod-01:
  127.0.0.1    localhost                          (system, always present)
  ::1          localhost                          (system, always present)
  10.0.0.5     primary-db.internal               (from "production")
```

### Example: Host override for a specific mapping

```
Group "production" (priority 100):
  - 10.0.0.5  →  primary-db.internal

Host "web-prod-01" override:
  - 10.0.0.5  →  local-db.internal
```

Host override wins.

```
Effective /etc/hosts for web-prod-01:
  127.0.0.1    localhost                          (system)
  ::1          localhost                          (system)
  10.0.0.5     local-db.internal                 (from host override)
```

On sync, the entire `/etc/hosts` file is replaced. Any entries not defined in
Barricade (and not system entries) will be removed.

---

## 4. Packages

Identity key: `package_name`. Host-level overrides fully replace the group
entry for that package.

### Example: Version pinning override

```
Group "base-security" (priority 10):
  - curl: state=present

Group "production" (priority 100):
  - curl: state=present, version=7.88.1-10+deb12u5
```

Higher-priority group wins. Production pins a specific version.

```
Effective packages for web-prod-01:
  - curl: present, version=7.88.1-10+deb12u5    (from "production")
```

### Example: Host needs a package removed

```
Group "web-servers" (priority 50):
  - php-fpm: state=present

Host "web-prod-01" override:
  - php-fpm: state=absent
```

This host doesn't need PHP. Host override wins.

```
Effective packages for web-prod-01:
  - php-fpm: absent                              (from host override — will be removed)
```

Unmanaged packages on the host are left alone. Only packages explicitly
defined in Barricade are installed, upgraded, or removed.

---

## 5. Linux Users

Identity key: `username` (users) / `groupname` (groups). Host-level overrides
fully replace the group entry for that user or group.

### Example: Different SSH keys per environment

```
Group "base-security" (priority 10):
  - user "deploy": shell=/bin/bash, authorized_keys=["ssh-ed25519 AAA...ops-team"]

Group "production" (priority 100):
  - user "deploy": shell=/bin/bash, authorized_keys=["ssh-ed25519 BBB...prod-deploy-key"]
```

Higher-priority group wins. The production deploy key replaces the ops key.

```
Effective users for web-prod-01:
  - deploy: shell=/bin/bash, keys=["ssh-ed25519 BBB...prod-deploy-key"]  (from "production")
```

Note: `authorized_keys` are synced with `exclusive=true`, meaning Barricade
replaces all SSH keys for that user. Keys added manually on the host will be
removed on next sync.

### Example: Host-specific sudo rule

```
Group "web-servers" (priority 50):
  - user "deploy": sudo_rule="ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart nginx"

Host "web-prod-01" override:
  - user "deploy": sudo_rule="ALL=(ALL) NOPASSWD: ALL"
```

This specific host needs broader sudo access. Host override wins.

```
Effective users for web-prod-01:
  - deploy: sudo_rule="ALL=(ALL) NOPASSWD: ALL"  (from host override)
```

Unmanaged system users (root, www-data, etc.) are never touched.

---

## 6. Cron Jobs

Identity key: `name` + `user` (composite). The same job name can exist for
different users without conflict. Host-level overrides fully replace the group
entry for that name+user pair.

### Example: Different schedules per environment

```
Group "base-security" (priority 10):
  - name="log-rotate", user=root, schedule="0 0 * * *", command="/usr/sbin/logrotate /etc/logrotate.conf"

Group "production" (priority 100):
  - name="log-rotate", user=root, schedule="0 */6 * * *", command="/usr/sbin/logrotate /etc/logrotate.conf"
```

Same name + user = same identity key. Production runs it every 6 hours instead of daily.

```
Effective cron jobs for web-prod-01:
  - log-rotate (root): 0 */6 * * *               (from "production")
```

### Example: Same job name, different users

```
Group "web-servers" (priority 50):
  - name="backup", user=root, schedule="0 2 * * *", command="/opt/backup/full.sh"
  - name="backup", user=postgres, schedule="0 1 * * *", command="pg_dump mydb > /tmp/db.sql"
```

Different users = different identity keys. Both jobs coexist.

```
Effective cron jobs for web-prod-01:
  - backup (root): 0 2 * * *     /opt/backup/full.sh
  - backup (postgres): 0 1 * * * pg_dump mydb > /tmp/db.sql
```

Unmanaged cron jobs on the host are left alone.

---

## 7. DNS Resolver

Singleton config (one per host). No identity key — the entire resolver config
is a single unit. If multiple groups define resolver settings, the
highest-priority group wins entirely. Host-level overrides replace everything.

### Example: Group priority

```
Group "base-security" (priority 10):
  - nameservers: [1.1.1.1, 1.0.0.1]
  - search_domains: [internal.corp]

Group "production" (priority 100):
  - nameservers: [10.0.0.2, 10.0.0.3]
  - search_domains: [prod.internal.corp]
```

Highest-priority group wins entirely (not merged field-by-field).

```
Effective resolver for web-prod-01:
  nameservers: [10.0.0.2, 10.0.0.3]              (from "production")
  search_domains: [prod.internal.corp]            (from "production")
  # The base-security nameservers are NOT included.
```

### Example: Host override

```
Group "production" (priority 100):
  - nameservers: [10.0.0.2, 10.0.0.3]

Host "web-prod-01" override:
  - nameservers: [10.0.0.2]
  - search_domains: [custom.local]
```

Host override wins. The entire resolver config is replaced.

```
Effective resolver for web-prod-01:
  nameservers: [10.0.0.2]                        (from host override)
  search_domains: [custom.local]                  (from host override)
```

On sync, the entire resolver config file is replaced (`/etc/resolv.conf`,
`/etc/systemd/resolved.conf`, or NetworkManager config depending on the
backend). Manual edits to the resolver file will be overwritten.

---

## Summary: Precedence Order

For every module, the merge order is:

```
1. Host-level override          (always wins, if defined)
        ↓ falls through if not set
2. Highest-priority group       (priority 100 beats 50 beats 10)
        ↓ falls through if no conflict
3. Lower-priority groups        (additive — non-conflicting items are included)
```

Items from different groups with **different identity keys** are combined
additively. Items with the **same identity key** are resolved by priority
(highest wins). Host overrides bypass priority entirely.
