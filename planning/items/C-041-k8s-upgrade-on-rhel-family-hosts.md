# C-041: k8s-upgrade on RHEL-family hosts

| | |
|---|---|
| Edition | community |
| Source | roadmap, board item "k8s-upgrade: support RHEL-family hosts (dnf)" |
| Tier / score | P3 / 4.0 |
| Stage | parked |
| Effort | M |
| Autonomy | B |
| Depends on | none |

## Goal
The `k8s-upgrade` action upgrades a kubeadm cluster whose nodes run RHEL, Rocky or Alma, using `dnf` and `dnf versionlock` where it uses `apt` and `apt-mark hold` today.

## Why it is parked
There are no RHEL-family test hosts. The board's own bar is a smoke test on a mixed Rocky 9 and Debian 12 cluster, and an untested package path in a cluster upgrade is not acceptable.

## What would un-park it
- A disposable kubeadm cluster on hosts named "test" with at least one Rocky 9 node and one Debian 12 node, which Dennis confirms may be broken.

## Context
- The action is in the public pack: `/home/user/unbork-playbooks/actions/k8s-upgrade/` (`manifest.yml`, `playbook.yml`, `verify.yml`, role `roles/kubernetes-upgrade/`).
- The refusal is the task "Refuse non-Debian-family hosts (apt-only for now)" in `roles/kubernetes-upgrade/tasks/main.yml` (line 8).
- The board's sketch names `upgrade-control-plane.yml` and `upgrade-worker.yml`. Those files no longer exist. The apt-specific task files today are `apt-prepare.yml`, `upgrade-packages.yml`, `upgrade-kubeadm.yml`, `packages-available.yml`, and the repository files `repo-find.yml`, `repo-key.yml`, `repo-switch.yml`, `repo-test.yml`. Each needs a `dnf` counterpart selected on `ansible_facts['os_family']`.
- `preflight.yml` should check that `pkgs.k8s.io` serves the requested `target_version` for the host's family.
- Only ansible-core is available to packs (no collections); `ansible.builtin.dnf` is in core, and version locking needs the `dnf versionlock` plugin driven through `ansible.builtin.command` with `argv:`.
- The only automated test in the pack is `tests/test_drain_capacity.py`; the rest is proved on a real cluster.

## Assumptions
- Assumption (not yet confirmed): Rocky 9 and Alma 9 are the targets; RHEL itself is assumed to behave the same and is not tested separately. Question for Dennis: is a RHEL subscription host needed?
- Assumption (not yet confirmed): the `dnf versionlock` plugin is installed by the action when missing, as a reported change.
- The surge-node path (`surge-join.yml`, `surge-remove.yml`) is assumed to be family-neutral and is checked, not rewritten.
