"""The generated firewall playbooks must survive Ansible's own parser.

Every other test here loads the playbook with ``yaml.safe_load`` and
inspects the result. That is not the same check Ansible performs. When a
module is given its arguments as a bare string — the "free-form" style,
``ansible.builtin.shell: some command`` — Ansible additionally runs the
string through ``split_args``, which tokenises it shell-style and
rejects anything it reads as an unbalanced quote or Jinja block.

An English apostrophe is enough. The comment line

    # table isn't recreated on the next boot

inside the nftables teardown script made ``split_args`` see an unclosed
single quote, so the *entire* iptables playbook failed to load — before
a single task ran. Every firewall sync to an iptables-backend host had
been failing outright, and no test noticed, because the YAML was
perfectly valid and only Ansible objected.

The teardown tasks now pass their script as ``{"cmd": ...}``, a mapping,
which skips free-form parsing entirely. These tests guard the whole
class: anything the generators emit has to be acceptable to the parser
that will actually read it.
"""

import pytest
import yaml

from app.ansible_runtime.composer import compose_playbook, fragment_firewall
from app.ansible_runtime.generator import generate_playbook
from app.rules.model import ChainPolicies, FirewallRuleSpec

BACKENDS = ["nftables", "iptables"]

_RULES = [
    FirewallRuleSpec(
        action="allow",
        protocol="tcp",
        direction="input",
        source_cidr="10.10.3.3/32",
        port_start=22,
        comment="ssh",
    )
]


def _split_args():
    """Ansible's own argument splitter, or skip if ansible-core is absent."""
    try:
        from ansible.parsing.splitter import split_args
    except ImportError:  # pragma: no cover - ansible-core is a dependency
        pytest.skip("ansible-core not installed")
    return split_args


def _free_form_strings(tasks: list[dict]) -> list[tuple[str, str]]:
    """(task name, command) for every task using the free-form style.

    A task whose arguments are a mapping is not parsed this way and
    cannot trip the splitter, so it is not returned.
    """
    out = []
    for task in tasks:
        for key, value in task.items():
            if key.startswith("ansible.builtin.") and isinstance(value, str):
                out.append((task.get("name", key), value))
    return out


@pytest.mark.parametrize("backend", BACKENDS)
def test_every_free_form_argument_survives_ansibles_splitter(backend):
    """The regression guard for the apostrophe bug."""
    split_args = _split_args()
    playbook = generate_playbook(
        backend, "10.0.0.1", _RULES, "/dev/shm/key", policies=ChainPolicies()
    )
    tasks = yaml.safe_load(playbook)[0]["tasks"]
    for name, command in _free_form_strings(tasks):
        try:
            split_args(command)
        except Exception as exc:  # noqa: BLE001 - the failure mode is the point
            pytest.fail(f"{backend}: task {name!r} is unparseable by Ansible: {exc}")


@pytest.mark.parametrize("backend", BACKENDS)
def test_the_composed_sync_playbook_parses_too(backend):
    """The coalesced sync is what actually runs in production, and it
    reuses the same task bodies. Checking only the standalone playbooks
    would have missed that every real sync was broken."""
    split_args = _split_args()
    fragment = fragment_firewall(backend=backend, rules=_RULES, policies=ChainPolicies())
    playbook = compose_playbook([fragment], hosts_alias="target")
    tasks = yaml.safe_load(playbook)[0]["tasks"]
    for name, command in _free_form_strings(tasks):
        try:
            split_args(command)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"{backend}: composed task {name!r} is unparseable: {exc}")


@pytest.mark.parametrize("backend", BACKENDS)
def test_the_teardown_script_is_passed_as_a_mapping(backend):
    """Pinning the fix, not just its effect.

    These two scripts are long, contain prose comments, and are the ones
    most likely to grow an apostrophe again. Passing them as ``cmd``
    takes them out of the free-form path for good.
    """
    playbook = generate_playbook(
        backend, "10.0.0.1", _RULES, "/dev/shm/key", policies=ChainPolicies()
    )
    tasks = yaml.safe_load(playbook)[0]["tasks"]
    teardown = [t for t in tasks if "Remove stale LabDog" in t.get("name", "")]
    assert teardown, f"{backend}: expected a teardown task"
    for task in teardown:
        assert isinstance(task["ansible.builtin.shell"], dict)
        assert "cmd" in task["ansible.builtin.shell"]


def test_the_apostrophe_that_caused_this_is_still_in_the_comment():
    """Deliberately keeping it. If the fix is ever reverted to the
    free-form style, the bug comes straight back — and this test says so
    rather than leaving a silently sanitised comment as the only defence."""
    from app.ansible_runtime.generator import _nftables_teardown_task

    assert "isn't" in _nftables_teardown_task()["ansible.builtin.shell"]["cmd"]
