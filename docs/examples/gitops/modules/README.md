# Per-module YAML references

Focused YAML files, one per configuration module. Each is a standalone
example that parses cleanly against the live `LabDogGroupYAML`
schema and demonstrates every field plus common edge cases with inline
comments.

Browse the full [GitOps guide](../README.md) for context, or jump straight
in:

| File | Module | Notable edge cases shown |
|---|---|---|
| [firewall.yaml](./firewall.yaml) | Firewall rules | ICMP no-port rule, port ranges, IPv6 CIDRs, reject vs deny, default-deny pattern |
| [services.yaml](./services.yaml) | Systemd services | Override vs full deploy, multi-line `unit_content`, template instances (`getty@tty1`) |
| [packages.yaml](./packages.yaml) | Packages + repositories | Version pinning + hold, state=absent, apt repository declaration, GPG key URL, RHEL/dnf |
| [hosts-entries.yaml](./hosts-entries.yaml) | /etc/hosts entries | Literal vs `host_ref_id` variants, IPv6, priority-driven emission order |
| [cron-jobs.yaml](./cron-jobs.yaml) | Cron jobs | Environment dict order-insensitive diff, byte-identical schedule preservation, per-user variants, common schedule patterns |
| [resolver.yaml](./resolver.yaml) | DNS resolver (singleton) | Three backend types (`resolv_conf` / `systemd_resolved` / `networkmanager`), DNS-over-TLS silent-falsify, options validation, leave-alone semantics |
| [users.yaml](./users.yaml) | Linux users + groups | `authorized_keys` sort-copy diff, `supplementary_groups` cross-reference warning, protected-name rejection, service-account `/usr/sbin/nologin` shell |
| [scheduled-actions.yaml](./scheduled-actions.yaml) | Cron-driven scheduled actions (list) | Multiple schedules per group, action_key validation against the live registry, typed parameters, snapshot + verify + rollback toggles for destructive actions, leave-alone-on-absence semantics |
