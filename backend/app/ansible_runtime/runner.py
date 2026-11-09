import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.ansible_runtime.inventory import build_ssh_common_args

ANSIBLE_ROLES_PATH = Path(__file__).parent / "roles"


def generate_multi_host_inventory(
    hosts: list[dict],
) -> str:
    """Generate Ansible inventory JSON for multiple hosts.

    Each host gets a unique key derived from its 'name' value, or 'host_{ip}'
    if the name key is absent. All hosts must supply their own ssh_key_path
    (caller is responsible for writing the key to tmpfs beforehand).

    Args:
        hosts: List of dicts, each containing:
            - ip: str — target IP address
            - port: int — SSH port
            - ssh_user: str — SSH login user
            - ssh_key_path: str — path to the private key file on disk
            - name: str (optional) — unique label for this host entry
            - known_hosts_path: str | None (optional) — file holding this
              host's pinned SSH host key (SEC-26). Absent or None keeps
              the ``accept-new`` fallback for a host with no recorded
              key yet.

    Returns:
        JSON string with all hosts nested under ``all.hosts``.
    """
    all_hosts: dict[str, Any] = {}
    for host in hosts:
        key = host.get("name") or f"host_{host['ip']}"
        all_hosts[key] = {
            "ansible_host": host["ip"],
            "ansible_port": host["port"],
            "ansible_user": host["ssh_user"],
            "ansible_ssh_private_key_file": host["ssh_key_path"],
            "ansible_ssh_common_args": build_ssh_common_args(host.get("known_hosts_path")),
        }
    inventory = {"all": {"hosts": all_hosts}}
    return json.dumps(inventory, indent=2)


#: Environment for every ansible-playbook run LabDog starts.
#:
#: Ansible output is captured into the DB and rendered in HTML, not a
#: terminal. ansible-runner attaches a pty by default, which makes Ansible
# emit ANSI SGR escape codes that show up as literal garbage in the UI.
# ANSIBLE_NOCOLOR=1 tells Ansible to skip colouring regardless of tty.
#
# ANSIBLE_PIPELINING (BUG-154): without it every task makes a remote
# temp directory, copies the module over SFTP, marks it executable,
# runs it and removes the directory: several extra SSH round trips per
# task, about 80 ms each at zero network latency. Pipelining sends the
# module down the open session instead. With ``become`` it needs sudo
# without ``requiretty``, the default on every current Debian, Ubuntu
# and RHEL-family release (see docs/ui/hosts.md). A caller's envvars
# can still turn it off.
BASE_ANSIBLE_ENV: dict[str, str] = {"ANSIBLE_NOCOLOR": "1", "ANSIBLE_PIPELINING": "True"}


def run_ansible(
    *,
    playbook_yaml: str | None = None,
    playbook_path: Path | None = None,
    inventory_json: str,
    private_data_dir: str,
    extra_vars: dict | None = None,
    timeout: int | None = None,
    envvars: dict | None = None,
    roles_paths: list[Path] | tuple[Path, ...] | None = None,
    event_handler: Callable[[dict[str, Any]], Any] | None = None,
    check_mode: bool = False,
) -> Any:
    """Write playbook + inventory to *private_data_dir* and invoke ansible_runner.

    Exactly one of *playbook_yaml* or *playbook_path* must be provided.
    When *playbook_path* is given its content is read from disk.

    The caller is responsible for creating *private_data_dir* and for all
    tmpfs / cleanup lifecycle management — this function only writes the
    ``project/playbook.yml`` and ``inventory/hosts`` files, then delegates
    to ``ansible_runner.run()``.

    Args:
        playbook_yaml: Playbook content as a string.
        playbook_path: Path to an existing playbook file whose content will
            be read and written to the runner project directory.
        inventory_json: Ansible inventory serialised as a JSON string.
        private_data_dir: Path to the ansible-runner working directory.
        extra_vars: Optional dict of extra variables forwarded to
            ``ansible_runner.run()`` as ``extravars``.
        timeout: Optional timeout in seconds forwarded to
            ``ansible_runner.run()``.
        envvars: Optional dict of environment variables merged with the
            default ``ANSIBLE_ROLES_PATH`` env var and forwarded to
            ``ansible_runner.run()``.
        roles_paths: Optional list of additional roles directories (e.g. from
            user packs) joined into ``ANSIBLE_ROLES_PATH``. LabDog's own
            ``roles/`` dir next to this module is always included as a
            fallback.
        event_handler: Optional callback invoked by ansible-runner for every
            event as it is emitted during the run (not just at the end). Used
            to stream task-by-task output to the live view. Receives the event
            dict (which carries ``stdout``); its return value is ignored here.
            It must not raise — it runs inside ansible-runner's event loop.
        check_mode: Run with ``ansible-playbook --check``. This is the only
            way to get check mode: passing ``ansible_check_mode=True`` as an
            extra-var does nothing, because ansible-core sets that magic var
            from the play context and the extra-var is overwritten, so the
            playbook runs for real. A task a pack marks ``check_mode: false``
            (a read-only probe, say) still runs; everything else reports what
            it would change without changing it.

    Returns:
        The ``ansible_runner.Runner`` object returned by ``ansible_runner.run()``.

    Raises:
        ValueError: If neither or both of *playbook_yaml* / *playbook_path*
            are provided.
    """
    import ansible_runner

    if playbook_yaml is not None and playbook_path is not None:
        raise ValueError("Provide exactly one of playbook_yaml or playbook_path, not both.")
    if playbook_yaml is None and playbook_path is None:
        raise ValueError("One of playbook_yaml or playbook_path must be provided.")

    if playbook_path is not None:
        playbook_yaml = Path(playbook_path).read_text()

    os.makedirs(f"{private_data_dir}/project", exist_ok=True)
    os.makedirs(f"{private_data_dir}/inventory", exist_ok=True)

    with open(f"{private_data_dir}/project/playbook.yml", "w") as f:
        f.write(playbook_yaml)  # type: ignore[arg-type]
    with open(f"{private_data_dir}/inventory/hosts", "w") as f:
        f.write(inventory_json)

    # Assemble roles path: caller-supplied dirs (e.g. a user pack's roles)
    # take precedence, LabDog's own roles are the fallback. De-duplicate while
    # preserving order.
    seen: set[str] = set()
    combined_roles: list[str] = []
    for p in list(roles_paths or []) + [ANSIBLE_ROLES_PATH]:
        s = str(p)
        if s in seen:
            continue
        seen.add(s)
        combined_roles.append(s)

    merged_env = {"ANSIBLE_ROLES_PATH": ":".join(combined_roles), **BASE_ANSIBLE_ENV}
    if envvars:
        merged_env.update(envvars)

    # ``ansible_runner.run()`` is ``init_runner()`` + ``.run()``; split so the
    # environment can be scrubbed in between (SEC-46).
    runner = ansible_runner.interface.init_runner(
        private_data_dir=private_data_dir,
        playbook="playbook.yml",
        extravars=extra_vars or {},
        timeout=timeout,
        envvars=merged_env,
        event_handler=event_handler,
        cmdline="--check" if check_mode else None,
    )
    runner.config.env = scrub_child_env(runner.config.env)
    runner.run()
    return runner


#: Inherited variables an ansible-playbook process may see. Everything else
#: from LabDog's own environment is dropped (SEC-46).
_INHERITED_ENV_ALLOWLIST = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "TERM",
        "TMPDIR",
        "TZ",
        "LANG",
        "LANGUAGE",
        "VIRTUAL_ENV",
        "SSH_AUTH_SOCK",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    }
)
_INHERITED_ENV_PREFIXES = ("ANSIBLE_", "LC_")


def scrub_child_env(env: dict[str, str], inherited: dict[str, str] | None = None) -> dict[str, str]:
    """The environment for an ansible-playbook child, minus LabDog's secrets.

    SEC-46: ansible-runner seeds the child from ``os.environ.copy()``, so
    every playbook process saw ``LABDOG_SECURITY__SECRET_KEY``,
    ``LABDOG_SECURITY__ENCRYPTION_KEY`` and the database URL, and any
    controller-side evaluation (SEC-39, SEC-40) could read them. The runner
    also writes the environment into the run's ``artifacts/…/command`` file.

    Kept: whatever ansible-runner or LabDog set for this run (a value that
    differs from the inherited one), and inherited variables on an
    allowlist. ``LABDOG_*`` is always dropped, even if set for the run.
    """
    inherited = os.environ if inherited is None else inherited
    out: dict[str, str] = {}
    for key, value in env.items():
        if key.startswith("LABDOG_"):
            continue
        set_for_run = key not in inherited or inherited[key] != value
        if (
            set_for_run
            or key in _INHERITED_ENV_ALLOWLIST
            or key.startswith(_INHERITED_ENV_PREFIXES)
        ):
            out[key] = value
    return out
