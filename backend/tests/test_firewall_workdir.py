"""SEC-33: firewall rollback state must not live at guessable paths.

The deadman's switch wrote its backups and its revert PID to fixed
locations on the *managed* host — ``/tmp/nftables-backup.conf``,
``/tmp/nftables-revert.pid``, and the iptables equivalents — and then
ran ``kill $(cat …)`` as root against one of them.

Any local user on that host could pre-create or symlink those names and
choose what root read back: which ruleset gets restored when the switch
fires, or which process id gets signalled. ``fs.protected_regular``
blunts the write side on modern kernels; it does nothing about the read
side, and it is a kernel setting LabDog does not control on hosts it
manages.

The plays now register an ``ansible.builtin.tempfile`` directory — 0700,
owned by root, unpredictably named — and thread its path through.

These tests assert on the rendered playbook, since that YAML is what
Ansible executes.
"""

import yaml

from app.ansible_runtime.generator import generate_playbook
from app.rules.model import ChainPolicies, FirewallRuleSpec

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

BACKENDS = ["nftables", "iptables"]


def _playbook(backend: str) -> str:
    return generate_playbook(backend, "10.0.0.1", _RULES, "/dev/shm/key", policies=ChainPolicies())


def _tasks(backend: str) -> list[dict]:
    return yaml.safe_load(_playbook(backend))[0]["tasks"]


class TestNoGuessablePathsRemain:
    def test_the_playbook_names_no_fixed_tmp_file(self):
        """The regression guard. A single reintroduced literal is a
        symlink target on every managed host."""
        for backend in BACKENDS:
            body = _playbook(backend)
            for leaked in (
                "/tmp/nftables-backup.conf",
                "/tmp/nftables-revert.pid",
                "/tmp/nftables-revert.log",
                "/tmp/iptables-backup.rules",
                "/tmp/ip6tables-backup.rules",
                "/tmp/iptables-revert.pid",
                "/tmp/iptables-revert.log",
            ):
                assert leaked not in body, f"{backend}: {leaked} is back"

    def test_the_kill_no_longer_reads_a_guessable_pid_file(self):
        """This was the sharpest edge: root signalling whatever pid a
        local user chose to write."""
        for backend in BACKENDS:
            kill_tasks = [
                t
                for t in _tasks(backend)
                if "kill $(cat" in str(t.get("ansible.builtin.shell", ""))
            ]
            assert kill_tasks, f"{backend}: expected a cancel-revert task"
            for task in kill_tasks:
                shell = task["ansible.builtin.shell"]
                assert "{{ labdog_fw_tmp.path }}" in shell
                assert "/tmp/" not in shell


class TestTheWorkingDirectoryIsCreatedFirst:
    def test_it_is_the_very_first_task(self):
        """Every later task references it, so anything before it would
        render an empty path — and ``rm -rf /nftables-backup.conf`` is
        not the failure mode to discover in production."""
        for backend in BACKENDS:
            first = _tasks(backend)[0]
            assert "ansible.builtin.tempfile" in first
            assert first["register"] == "labdog_fw_tmp"

    def test_it_asks_for_a_directory_not_a_file(self):
        for backend in BACKENDS:
            spec = _tasks(backend)[0]["ansible.builtin.tempfile"]
            assert spec["state"] == "directory"

    def test_it_is_recognisable_on_a_host(self):
        """An operator finding one left behind after a failed sync
        should be able to tell where it came from."""
        for backend in BACKENDS:
            assert _tasks(backend)[0]["ansible.builtin.tempfile"]["prefix"].startswith("labdog")


class TestEveryReferenceUsesIt:
    def test_no_task_mentions_a_bare_tmp_path(self):
        for backend in BACKENDS:
            for task in _tasks(backend):
                rendered = yaml.safe_dump(task)
                assert "/tmp/" not in rendered, f"{backend}: {task.get('name')}"

    def test_the_backup_and_the_revert_agree_on_the_location(self):
        """A mismatch would restore nothing when the switch fired, and
        the playbook would still look like it worked."""
        for backend in BACKENDS:
            tasks = _tasks(backend)
            revert = next(
                t
                for t in tasks
                if "deadman" in t["name"].lower() or "automatic revert in" in t["name"].lower()
            )
            shell = revert["ansible.builtin.shell"]
            assert shell.count("{{ labdog_fw_tmp.path }}") >= 2


class TestCleanupRemovesTheDirectory:
    def test_the_last_task_removes_it(self):
        for backend in BACKENDS:
            cleanup = _tasks(backend)[-1]
            assert cleanup["ansible.builtin.file"]["state"] == "absent"
            assert cleanup["ansible.builtin.file"]["path"] == "{{ labdog_fw_tmp.path }}"

    def test_cleanup_happens_after_the_revert_is_cancelled(self):
        """Removing the backups while the deadman's switch could still
        fire would turn a rollback into an empty ruleset."""
        for backend in BACKENDS:
            names = [t["name"] for t in _tasks(backend)]
            cancel = next(i for i, n in enumerate(names) if "Cancel automatic revert" in n)
            cleanup = next(i for i, n in enumerate(names) if "Clean up" in n)
            assert cancel < cleanup


class TestThePlaybookIsStillWhatItWas:
    """The regression half — the point is safer paths, not a different
    firewall."""

    def test_it_is_valid_yaml_with_one_play(self):
        for backend in BACKENDS:
            plays = yaml.safe_load(_playbook(backend))
            assert len(plays) == 1
            assert plays[0]["become"] is True

    def test_the_rules_still_reach_the_host(self):
        assert "10.10.3.3/32" in _playbook("nftables")
        assert "10.10.3.3/32" in _playbook("iptables")

    def test_the_deadman_switch_still_exists_for_both_backends(self):
        for backend in BACKENDS:
            assert any("deadman" in t["name"].lower() for t in _tasks(backend))
