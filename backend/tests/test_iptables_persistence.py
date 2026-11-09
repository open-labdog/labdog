"""BUG-163: the iptables play installs persistence only where it is missing.

It ran ``ansible.builtin.package`` for ``iptables-persistent`` and then
``netfilter-persistent`` on every sync, both with ``ignore_errors``: two
package-manager runs each time, and on an RPM host two failures.
"""

import os
import subprocess  # nosec B404 - runs the generated check script locally
from pathlib import Path

import pytest
import yaml

from app.ansible_runtime.generator import generate_playbook
from app.rules.model import ChainPolicies, FirewallRuleSpec

_RULES = [FirewallRuleSpec(action="allow", protocol="tcp", direction="input", port_start=22)]


def _tasks() -> list[dict]:
    pb = generate_playbook("iptables", "10.0.0.1", _RULES, "/tmp/key", policies=ChainPolicies())
    return yaml.safe_load(pb)[0]["tasks"]


def _named(name: str) -> dict:
    return next(t for t in _tasks() if t["name"] == name)


def test_no_package_manager_run_unless_it_is_missing():
    tasks = _tasks()
    assert not any("ansible.builtin.package" in t for t in tasks)
    install = _named("Install iptables-persistent for boot persistence")
    assert install["ansible.builtin.apt"] == {"name": "iptables-persistent", "state": "present"}
    assert install["when"] == "labdog_netfilter_persistent.stdout == 'missing'"
    names = [t["name"] for t in tasks]
    assert names.index("Check for netfilter-persistent") < names.index(install["name"])
    assert names.index(install["name"]) < names.index("Save iptables rules for persistence")


def test_the_check_reads_but_changes_nothing():
    check = _named("Check for netfilter-persistent")
    assert check["changed_when"] is False
    assert check["check_mode"] is False
    assert check["register"] == "labdog_netfilter_persistent"


@pytest.mark.parametrize(
    ("binaries", "expected"),
    [
        (["netfilter-persistent", "apt-get"], "present"),
        (["apt-get"], "missing"),
        (["dnf"], "unsupported"),
    ],
)
def test_the_check_tells_the_three_cases_apart(tmp_path: Path, binaries, expected):
    if Path("/usr/sbin/netfilter-persistent").exists():
        pytest.skip("this machine has netfilter-persistent in /usr/sbin")
    for name in binaries:
        path = tmp_path / name
        path.write_text("#!/bin/sh\n")
        path.chmod(0o755)
    script = _named("Check for netfilter-persistent")["ansible.builtin.shell"]
    out = subprocess.run(  # nosec B603 B607 - a fixed local shell
        ["/bin/sh", "-c", script],
        env={**os.environ, "PATH": str(tmp_path)},
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == expected
