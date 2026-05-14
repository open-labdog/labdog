# k8s-upgrade

Drains, `kubeadm`-upgrades, and re-admits each node in a Kubernetes
cluster, one node at a time. Control-plane nodes are upgraded first
(`kubeadm upgrade apply` on the first, `kubeadm upgrade node` on the
rest), then workers. Apt-only — Debian / Ubuntu hosts.

LabDog operators surface this as the **Upgrade Kubernetes cluster**
action on the group view. The action's manifest declares
`supports_host: false`, which tells LabDog to dispatch the playbook
once against the whole group (a flat `all` Ansible inventory) instead
of fanning out per-host. The playbook itself decides which nodes are
control-plane and which are workers — there are no roles to assign in
the LabDog UI.

## What it does

1. **Discovers cluster topology**: probes every node for
   `/etc/kubernetes/manifests/kube-apiserver.yaml`. Nodes that have
   it are control-plane; nodes that don't are workers. Uses
   `add_host` to build the in-memory `k8s_control_plane` and
   `k8s_worker` groups for the rest of the playbook. Refuses if no
   control-plane node is detected.
2. **Validates prerequisites** on the first control-plane node:
   `target_version` looks like a semver, `kubectl` is on `PATH`, and
   every Ansible inventory hostname matches an existing Kubernetes
   node name.
3. **Upgrades control-plane nodes serially** (`serial: 1`) via the
   `kubernetes-upgrade` role with `node_role=control_plane`.
4. **Upgrades worker nodes serially** (`serial: 1`) via the same role
   with `node_role=worker`.

The playbook never upgrades two nodes concurrently — Kubernetes
requires this. `kubectl drain` / `uncordon` / wait-Ready tasks
delegate to the first control-plane host so a kubeconfig only needs
to live there, not on every worker.

## Manifest parameters

See [`manifest.yml`](./manifest.yml).

| Key | Type | Default | Notes |
|---|---|---|---|
| `target_version` | string | required | Semver only (e.g. `1.30.4`). Must already be downloadable from the apt repo configured on every node. |
| `skip_preflight` | bool | `false` | Bypass per-node sanity checks. Use sparingly. |
| `drain_timeout` | int | `300` | Maximum seconds to wait for `kubectl drain` to evict pods. |

## Targeting

- `supports_group: true`, `supports_host: false` — the group flag
  exposes the action on the group view; the host flag, set to false,
  tells LabDog to dispatch the playbook in a single ansible-playbook
  invocation against all members (instead of the per-host fan-out the
  other actions in this pack use). Cluster-wide coordination
  (`serial:`, `add_host`, `delegate_to`, `run_once`) lives entirely
  inside this playbook — LabDog's involvement ends at "give the
  playbook a flat inventory of every host in the group."
- `destructive: true` — multi-node coordination is the playbook's
  responsibility; LabDog does **not** wrap group-dispatched runs in
  per-host snapshot/verify/rollback (the per-node snapshot wouldn't
  compose cleanly with a kubeadm rolling upgrade). The drain →
  uncordon → wait-Ready cycle in the role is the safety story.

## Requirements

- **OS**: Debian / Ubuntu on every node (`apt`-managed
  `kubeadm`/`kubelet`/`kubectl`).
- **Inventory**: every node's Ansible hostname must equal its
  Kubernetes node name. The preflight asserts this and fails fast on
  mismatch (DNS short vs FQDN, operator typos at bootstrap).
- **kubeconfig**: `/etc/kubernetes/admin.conf` on every control-plane
  node — the playbook sets `KUBECONFIG` to that path.
- **Privileges**: sudo/root on every node.

## Role

The single role that does the work lives at
[`roles/kubernetes-upgrade/`](./roles/kubernetes-upgrade/) — private
to this action. It's a useful template for adding RHEL-family support
(currently on the LabDog roadmap): copy it, swap `apt` for `dnf`, and
sibling it as `roles/kubernetes-upgrade-rhel/`.

## Customizing

- **Skip the preflight** for a single run: pass `skip_preflight=true`
  via the UI. For a permanent change, edit the `pre_tasks` in
  [`playbook.yml`](./playbook.yml).
- **Change drain behaviour**: edit the role's
  `defaults/main.yml`, or expose more knobs as manifest parameters.
- **Override the whole action**: copy `actions/k8s-upgrade/` into a
  higher-precedence pack and edit there.
