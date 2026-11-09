"""SEC-39: operator strings in module playbooks must never be templated.

ansible-core templates task arguments on the controller. Every generator
placed operator data (cron commands, firewall comments, ``/etc/hosts``,
unit files, user fields) into task arguments as plain scalars, so a Jinja
delimiter in any of them ran on the LabDog host. ``dump_playbook`` now tags
every string with a delimiter ``!unsafe`` unless LabDog marked it as its own
template.
"""

import pytest
import yaml

from app.ansible_runtime.composer import (
    compose_playbook,
    fragment_cron,
    fragment_firewall,
    fragment_hosts_file,
    fragment_linux_users,
    fragment_packages,
    fragment_resolver,
    fragment_services,
)
from app.ansible_runtime.playbook_yaml import Template, dump_playbook, load_playbook
from app.rules.model import FirewallRuleSpec

EVIL = "{{ lookup('pipe', 'id') }}"


def _scalars(node):
    if isinstance(node, yaml.ScalarNode):
        yield node
    elif isinstance(node, yaml.SequenceNode):
        for item in node.value:
            yield from _scalars(item)
    elif isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            yield from _scalars(key)
            yield from _scalars(value)


def _assert_operator_values_unsafe(text: str) -> None:
    seen = 0
    for node in _scalars(yaml.compose(text)):
        if "lookup('pipe'" in node.value:
            seen += 1
            assert node.tag == "!unsafe", node.value
        elif "{{" in node.value:
            # LabDog's own expressions stay templated.
            assert node.tag != "!unsafe", node.value
    assert seen, "the operator value did not reach the playbook"


@pytest.mark.parametrize(
    "fragment",
    [
        lambda: fragment_cron(
            [
                {
                    "name": "j",
                    "user": "root",
                    "schedule": "* * * * *",
                    "command": f"echo {EVIL}",
                    "state": "present",
                    "environment": {"X": EVIL},
                }
            ]
        ),
        lambda: fragment_hosts_file(f"127.0.0.1 localhost\n10.0.0.1 a  # {EVIL}\n"),
        lambda: fragment_services(
            [
                {
                    "service_name": "foo",
                    "state": "running",
                    "enabled": True,
                    "unit_content": f"[Service]\nExecStart=/bin/echo {EVIL}\n",
                    "deploy_mode": "full",
                }
            ]
        ),
        lambda: fragment_firewall(
            "nftables",
            [FirewallRuleSpec(action="allow", protocol="tcp", direction="input", comment=EVIL)],
        ),
        lambda: fragment_firewall(
            "iptables",
            [FirewallRuleSpec(action="allow", protocol="tcp", direction="input", comment=EVIL)],
        ),
        lambda: fragment_linux_users(
            [
                {
                    "username": "u",
                    "state": "present",
                    "shell": "/bin/bash",
                    "comment": EVIL,
                    "authorized_keys": [f"ssh-ed25519 AAAA {EVIL}"],
                }
            ],
            [],
        ),
        lambda: fragment_packages(
            [{"package_name": "curl", "state": "present", "version": EVIL}], []
        ),
        lambda: fragment_resolver("resolv_conf", f"nameserver 1.1.1.1\n# {EVIL}\n"),
    ],
)
def test_operator_values_are_tagged_unsafe(fragment):
    _assert_operator_values_unsafe(compose_playbook([fragment()]))


def test_labdog_templates_survive_a_round_trip():
    text = dump_playbook([{"a": Template("{{ x }}"), "b": EVIL, "c": "plain"}])
    again = dump_playbook(load_playbook(text))
    assert again == text
    loaded = load_playbook(text)[0]
    assert isinstance(loaded["a"], Template)
    assert not isinstance(loaded["b"], Template)
