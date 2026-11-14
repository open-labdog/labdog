# C-057: kennel: isolated sandbox network with a jump host

| | |
|---|---|
| Edition | community |
| Source | user (Dennis, kennel thread); decision D-23 |
| Tier / score | P2 / 4.3 |
| Stage | 11 |
| Effort | L |
| Autonomy | H |
| Depends on | C-028 |

## Goal

kennel can place a VM on a sandbox network that has no route to any production address. The VM can still fetch packages through outbound NAT, and unbork can reach it over SSH through a jump host that kennel runs in the sandbox.

## Scope

In scope:
- A `sandbox_networks` model in kennel: which bridge or SDN VNet on which cluster is the sandbox, its subnet, and its jump host.
- A function that rewrites every network card of a VM onto the sandbox before its first start.
- A jump host: a small leased VM with one leg in the sandbox and one leg unbork can reach.
- An isolation self-test that proves a sandboxed VM cannot reach production.
- In unbork: a per-host jump host, used by Ansible and by unbork's own SSH connections.

Out of scope:
- Creating the VLAN, SDN zone, firewall rules or NAT on Proxmox or on the site's router by kennel. Dennis or the operator creates them; kennel verifies them.
- Restoring anything into the sandbox (C-045, C-055).
- DNS inside the sandbox beyond what DHCP hands out.

## Context

- [D-23](../DECISIONS.md): "an isolated sandbox network is supported from the start (a VLAN or SDN zone with no route to production, a jump host for unbork's SSH, outbound NAT), so rehearsing a change on a copy of a VM is in scope."
- The write-up "How far kennel can go" ("Deployment model"): restore tests need no network because checks run through the guest agent; sandbox copies that unbork must reach over SSH need "a route from unbork into the sandbox, for example through a jump host kennel runs there, and egress through NAT for package mirrors". It also warns that "sandbox networking varies per site".
- Why isolation matters: a VM restored from a backup has the original's IP address, hostname, credentials and scheduled jobs. On a production network it would answer for the original, send its mail and run its cron jobs against real systems.
- Proxmox offers two ways to build the network: a plain Linux bridge with a VLAN tag that the site's switch and router isolate, or an SDN zone. A "Simple" SDN zone with a VNet and subnet can give DHCP (dnsmasq) and SNAT on the node itself, which needs no switch change but is local to one node unless a VXLAN or EVPN zone is used.
- From C-054: leases, blueprints, the tag stamp and the provision job. The jump host is a held lease made from a blueprint with two network cards.
- **unbork has no jump host support today.** `backend/app/ansible_runtime/inventory.py::build_ssh_common_args` (line 17) builds the `ansible_ssh_common_args` for every inventory entry and has no `ProxyJump`. `backend/app/ssh_utils.py::_SSHConnectContext` (line 106) calls `asyncssh.connect` without a `tunnel=` argument, and so do the direct calls at `ssh_utils.py:227` and `backend/app/workflows/steps/reboot.py:170`. The `Host` model (`backend/app/models/host.py`, line 56 onward) has no jump field. Host key pinning for Ansible is in `backend/app/ansible_runtime/known_hosts.py`.

## Dependencies

- C-028 and, for the jump host, C-054.
- **Network on the test cluster, created by Dennis:** either a VLAN-tagged bridge or an SDN zone and VNet, with a subnet that overlaps no production subnet, DHCP, outbound NAT to the internet (or to an internal mirror only), and no route or firewall allowance to any production address. Dennis logs in `planning/DECISIONS.md`: the cluster, the bridge or VNet name, the subnet, which production subnets must be unreachable, and which network the jump host's outer leg sits on.
- Proxmox privilege `SDN.Use` on the VNet (or on `/sdn/zones/<zone>/<vnet>`) for the kennel token, and the same on the outer bridge for the jump host only.

## Implementation steps

1. **Blocked until Dennis has created the sandbox network on the test cluster and logged its details in `planning/DECISIONS.md`.** Skip this item and C-055 until then. Do not create or change any network, firewall or SDN object on Proxmox from code or by hand.
2. **kennel migration** `backend/alembic/versions/000N_sandbox.py` (next free number): `sandbox_networks` (name, cluster_id, bridge, vlan_tag nullable, subnet CIDR, forbidden_cidrs array, jump_lease_id nullable, jump_address, jump_user, jump_host_key, last_isolation_test_at, last_isolation_test_ok).
3. **Routes** in `kennel/api/sandbox.py`: `GET /api/sandbox-networks` (`require("read")`); `POST`, `PATCH`, `DELETE /api/sandbox-networks/{id}` (`require("admin")`); `POST /api/sandbox-networks/{id}/test` (`require("admin")`), which queues the isolation test; `POST /api/sandbox-networks/{id}/jump-host` (`require("admin")`), which creates or replaces the jump host.
4. **NIC rewrite** `kennel/sandbox/nics.py::move_to_sandbox(client, node, vmid, network)`. It reads the VM config, and for every `netN` line keeps the model and writes a new line with the sandbox bridge, the VLAN tag if any, a freshly generated MAC address, and `firewall=1`. It removes nothing else. It then re-reads the config and raises if any `netN` still names another bridge. It refuses to run on a VM outside kennel's pool and on a VM that is running. C-045 and C-055 call it between restore and first start.
5. **Jump host.** A blueprint flagged `jump: true` with two network cards: `net0` on the outer bridge, `net1` on the sandbox. Cloud-init installs nothing beyond the base image; sshd allows only key login for one unprivileged user with `AllowTcpForwarding yes`, `PermitTTY no` and no shell (`ForceCommand /bin/false` is not usable with `ProxyJump`, so use a user with `nologin` and `-N`-style forwarding only; verify on the template). IP forwarding stays off, so the jump host is not a router. It is a held lease, so the sweeper never removes it. Its public key for callers: kennel generates a key pair per sandbox network, keeps the private key encrypted with the service key, and hands out a short-lived copy with each sandboxed lease (step 7).
6. **Isolation test** (job `kind="sandbox_isolation_test"`). It leases a probe VM from a blueprint onto the sandbox, then through the guest agent (`agent/exec`, no network needed) runs, for each address in a probe list derived from `forbidden_cidrs` (the gateway and a few live hosts the operator lists): a TCP connect to ports 22 and 443 and one ICMP echo, all of which must fail; then one HTTPS request to the configured mirror or a public address, which must succeed when NAT is expected. It records the result on the network row and releases the probe. A sandbox network whose last test failed or is older than a configurable age (default 7 days) cannot receive a VM: `move_to_sandbox` callers check `last_isolation_test_ok` first.
7. **Lease output.** A lease on a sandbox network returns, in `lease.ready` and `GET /api/leases/{id}`, a `jump` object: address, port, user, host key, and a private key valid for that lease.
8. **unbork: jump host support** (a change in this repository):
   - Migration `backend/alembic/versions/00NN_host_jump.py` (next free number after `0060_schedule_owner.py`): nullable columns on `hosts`: `jump_address`, `jump_port`, `jump_user`, `jump_host_key_entry`, `jump_ssh_key_id` (foreign key to `ssh_keys.id`, `ondelete="SET NULL"`).
   - `backend/app/ansible_runtime/inventory.py`: `build_ssh_common_args` gains an optional `jump` argument and appends `-o ProxyCommand=...` built from an `ssh -W %h:%p` call that carries its own `-i`, `UserKnownHostsFile` and `StrictHostKeyChecking=yes` for the jump host, every value passed through `shlex.quote`. `generate_inventory` (line 66) passes it. The jump key is written to the run's tmpfs workspace next to the host key (`backend/app/tasks/host_sync_orchestrator.py::_make_tmpfs_workspace`, `backend/app/tasks/action_host.py:707-721`) and removed with it.
   - `backend/app/ssh_utils.py`: `_SSHConnectContext` opens the jump connection first and passes it as `tunnel=` to `asyncssh.connect`; apply the same in the helper at line 227 and in `workflows/steps/reboot.py:170`. Close the tunnel when the inner connection closes.
   - `backend/app/schemas/hosts.py` and the host edit route: the fields are settable with `config.edit` on the host, like other connection fields. No new route.
   - The SSH terminal (`backend/app/api/ssh_terminal.py`) uses the same helper, so it gains the tunnel without its own change. Verify.
9. Docs (see below).

## Acceptance criteria

- The isolation test passes on Dennis's sandbox network, and fails with the offending address named when a route to a listed production subnet is opened on purpose.
- A leased VM placed on the sandbox has every network card on the sandbox bridge, checked from its Proxmox config.
- From inside that VM, a connection to a production address fails and a package index download succeeds.
- unbork, with the lease's jump details set on a host record, runs a `_builtin.collect_state` action and a sync against the sandboxed VM, and opens a terminal to it.
- Together these are the stage behaviour: "A sandboxed VM cannot reach a production address; unbork reaches it through the jump host."
- The jump host forwards TCP only to addresses inside the sandbox subnet: a `ssh -W` to a production address through it fails.
- A host with no jump fields behaves exactly as before: `tests/test_inventory*.py` and the SSH tests pass unchanged.
- A sandbox network with a failed or stale isolation test refuses new VMs.

## Test plan

- kennel: `cd backend && source .venv/bin/activate && pytest tests/test_sandbox_nics.py tests/test_sandbox_api.py tests/test_isolation_test.py -q` (new), then `pytest tests/ -q`, ruff and bandit.
- unbork: `cd backend && source .venv/bin/activate && pytest tests/test_jump_host_inventory.py tests/test_jump_host_ssh.py tests/test_permissions.py -q` (first two new; the SSH one uses an in-process asyncssh server as jump and target), then the full suite once before the stage review. `cd frontend && npx tsc --noEmit && npm run lint` if the host form gains the fields.
- Real network: the test cluster only, from kennel on lin-manager-2. Probe and target VMs are kennel's own leases. The deliberate "open a route" check is done by Dennis or with his go-ahead, on the test cluster, and reverted at once.

## Docs and changelog

- kennel: `docs/sandbox-network.md` (two worked setups: VLAN bridge and SDN Simple zone with SNAT; what to list as forbidden subnets; the isolation test; the jump host), `docs/proxmox-token.md`, `CHANGELOG.md`: "Sandbox networks with an isolation self-test and a jump host."
- unbork: `docs/ui/hosts.md` (or the host connection page that exists at that time) gains "Reaching a host through a jump host"; `docs/security-hardening.md` notes that the jump key is stored encrypted like other SSH keys. `CHANGELOG.md` `[Unreleased]`, Added: "Hosts can be reached through an SSH jump host."

## Risks and rollback

- A sandbox that is not isolated lets a restored copy act as the original on the production network. The isolation test gate, the re-read after the NIC rewrite and the refusal on a stale test are the guards. Treat any failure here as critical in the stage review.
- The jump host is a bridge between two networks. Keep IP forwarding off, restrict it to TCP forwarding for one key, and firewall its sandbox leg to the sandbox subnet.
- `ProxyCommand` strings are a command-injection surface. Every value is validated (address, port, user by strict patterns) and quoted; add a test with hostile values.
- SDN zones differ between Proxmox versions. Document the version the setups were tested on.
- Rollback: kennel side, delete the sandbox network rows and release the jump lease. unbork side, the columns are nullable and unused when empty; the migration's downgrade drops them.

## Assumptions

- [D-23](../DECISIONS.md) requires the sandbox, the jump host and outbound NAT from the start.
- Assumption (not yet confirmed): kennel verifies the network and never builds it. Question for Dennis: VLAN bridge or SDN zone on the test cluster, and should kennel later create SDN objects itself?
- Assumption (not yet confirmed): the jump host is a kennel-leased VM. Question for Dennis: or should the Proxmox node or an existing bastion serve as the jump?
- Assumption (not yet confirmed): the unbork jump host change belongs in this item. It is useful without kennel (any bastion setup), so it could be split into its own Community item and moved earlier.
- Assumption (not yet confirmed): outbound NAT to the internet is acceptable for the sandbox. A site may want mirror-only egress.

## Definition of done

- [ ] Dennis's network details logged in `DECISIONS.md` before any code.
- [ ] kennel: model, routes, NIC rewrite, jump host and isolation test merged; tests green.
- [ ] unbork: jump host migration and SSH changes merged; new tests and the full suite green.
- [ ] Isolation and reachability proven on the test cluster; results saved in the PR.
- [ ] Docs and both changelogs updated.
- [ ] `planning/roadmap.yaml` status updated; one line in `planning/PROGRESS.md`.
