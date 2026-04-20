# Barricade — Example Configurations

Two sets of examples live here:

- **[gitops/](./gitops/)** — YAML files a git repository serves to Barricade. One file per Barricade group; covers all seven configuration modules (firewall, services, packages, /etc/hosts, cron jobs, DNS resolver, users + linux groups). Start with [gitops/README.md](./gitops/README.md) for a walk-through, [gitops/minimal.yaml](./gitops/minimal.yaml) for the smallest valid file, or [gitops/web-servers.yaml](./gitops/web-servers.yaml) for an end-to-end realistic example.

- **[precedence/](./precedence/)** — How Barricade merges group-level and host-level configurations when a host belongs to multiple groups. Read this alongside the GitOps docs once you're managing more than one group.
