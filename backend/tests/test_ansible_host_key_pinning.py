"""SEC-26: the Ansible path must honour LabDog's own pinned host keys.

The asyncssh paths do real TOFU-with-pinning: the first connect records
``Host.ssh_host_key_entry`` and every later one verifies against it, so a
substituted key raises ``HostKeyMismatchError``. The Ansible path did
none of that — ``StrictHostKeyChecking=accept-new`` with no
``UserKnownHostsFile`` meant every playbook run started from an empty
known-hosts file and accepted whatever key was presented. A
man-in-the-middle the web terminal refuses was accepted by the pipeline
that pushes root-level configuration.

These tests assert on the rendered inventory, because that string is
what Ansible actually consumes, and on the file the run writes, because
pointing at a path that holds the wrong bytes would verify nothing.
"""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from app.ansible_runtime.inventory import build_ssh_common_args, generate_inventory
from app.ansible_runtime.known_hosts import (
    known_hosts_path_for,
    remove_known_hosts,
    write_known_hosts,
)
from app.ansible_runtime.runner import generate_multi_host_inventory

ENTRY = "10.0.0.5 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleKeyMaterialNotReal"


@pytest.fixture(autouse=True)
def _fixed_connect_timeout():
    """Pin the setting so these tests assert on host-key args only."""
    with patch("app.settings_service.get_setting_cached_typed", return_value=10):
        yield


class TestTheArgsPinWhenThereIsAKey:
    def test_a_pinned_path_switches_to_strict_verification(self):
        args = build_ssh_common_args("/dev/shm/x.key.known_hosts")
        assert "StrictHostKeyChecking=yes" in args
        assert "accept-new" not in args
        assert "UserKnownHostsFile=/dev/shm/x.key.known_hosts" in args

    def test_the_controllers_own_known_hosts_cannot_vouch_for_the_host(self):
        """LabDog verifies against the key it recorded, not against
        whatever the controller happens to trust system-wide."""
        assert "GlobalKnownHostsFile=/dev/null" in build_ssh_common_args("/tmp/kh")

    def test_a_path_needing_quoting_survives_ansibles_shlex_split(self):
        import shlex

        args = build_ssh_common_args("/tmp/a b/known_hosts")
        opts = shlex.split(args)
        assert "UserKnownHostsFile=/tmp/a b/known_hosts" in opts


class TestTheArgsFallBackOnFirstContact:
    """A host LabDog has never connected to has no key to pin to.
    Refusing would make a brand-new host unmanageable, so first contact
    keeps the previous posture — and the asyncssh paths record the key,
    so the window is one connection wide."""

    @pytest.mark.parametrize("value", [None, ""])
    def test_no_pinned_key_keeps_accept_new(self, value):
        args = build_ssh_common_args(value)
        assert "StrictHostKeyChecking=accept-new" in args
        assert "UserKnownHostsFile" not in args

    def test_the_other_ssh_args_are_unaffected_either_way(self):
        for args in (build_ssh_common_args(), build_ssh_common_args("/tmp/kh")):
            assert "ConnectTimeout=10" in args
            assert "ServerAliveInterval=30" in args
            assert "ServerAliveCountMax=6" in args


class TestTheInventoriesCarryIt:
    def test_single_host_inventory_pins(self):
        inv = json.loads(
            generate_inventory(
                "10.0.0.5",
                22,
                "/dev/shm/k.key",
                hostname="node-1",
                known_hosts_path="/dev/shm/k.key.known_hosts",
            )
        )
        common = inv["all"]["hosts"]["node-1"]["ansible_ssh_common_args"]
        assert "StrictHostKeyChecking=yes" in common
        assert "UserKnownHostsFile=/dev/shm/k.key.known_hosts" in common

    def test_multi_host_inventory_pins_each_host_to_its_own_key(self):
        """One inventory, two hosts, two different keys — a shared
        known-hosts file would let either host's key authenticate the
        other."""
        inv = json.loads(
            generate_multi_host_inventory(
                [
                    {
                        "name": "a",
                        "ip": "10.0.0.1",
                        "port": 22,
                        "ssh_user": "root",
                        "ssh_key_path": "/dev/shm/a.key",
                        "known_hosts_path": "/dev/shm/a.key.known_hosts",
                    },
                    {
                        "name": "b",
                        "ip": "10.0.0.2",
                        "port": 22,
                        "ssh_user": "root",
                        "ssh_key_path": "/dev/shm/b.key",
                        "known_hosts_path": "/dev/shm/b.key.known_hosts",
                    },
                ]
            )
        )
        assert "a.key.known_hosts" in inv["all"]["hosts"]["a"]["ansible_ssh_common_args"]
        assert "b.key.known_hosts" in inv["all"]["hosts"]["b"]["ansible_ssh_common_args"]

    def test_a_member_with_no_recorded_key_does_not_break_the_others(self):
        inv = json.loads(
            generate_multi_host_inventory(
                [
                    {
                        "name": "a",
                        "ip": "10.0.0.1",
                        "port": 22,
                        "ssh_user": "root",
                        "ssh_key_path": "/dev/shm/a.key",
                        "known_hosts_path": "/dev/shm/a.key.known_hosts",
                    },
                    {
                        "name": "b",
                        "ip": "10.0.0.2",
                        "port": 22,
                        "ssh_user": "root",
                        "ssh_key_path": "/dev/shm/b.key",
                    },
                ]
            )
        )
        args = {n: h["ansible_ssh_common_args"] for n, h in inv["all"]["hosts"].items()}
        assert "StrictHostKeyChecking=yes" in args["a"]
        assert "StrictHostKeyChecking=accept-new" in args["b"]


class TestTheFileTheInventoryPointsAt:
    """Pointing ``UserKnownHostsFile`` at a file with the wrong contents
    verifies nothing, so the write is worth its own tests."""

    def test_it_holds_the_stored_entry_and_nothing_else(self, tmp_path):
        key = tmp_path / "id.key"
        key.write_text("private")
        path = write_known_hosts(ENTRY, str(key))
        assert path == known_hosts_path_for(str(key))
        assert open(path).read() == ENTRY + "\n"

    def test_it_is_not_world_readable(self, tmp_path):
        key = tmp_path / "id.key"
        key.write_text("private")
        path = write_known_hosts(ENTRY, str(key))
        assert oct(os.stat(path).st_mode)[-3:] == "600"

    @pytest.mark.parametrize("entry", [None, "", "   \n"])
    def test_nothing_to_pin_writes_no_file(self, tmp_path, entry):
        key = tmp_path / "id.key"
        key.write_text("private")
        assert write_known_hosts(entry, str(key)) is None
        assert not os.path.exists(known_hosts_path_for(str(key)))

    def test_it_lives_beside_the_key_so_the_same_cleanup_finds_it(self, tmp_path):
        key = tmp_path / "id.key"
        key.write_text("private")
        write_known_hosts(ENTRY, str(key))
        remove_known_hosts(str(key))
        assert not os.path.exists(known_hosts_path_for(str(key)))

    def test_cleanup_is_safe_when_there_was_never_a_file(self, tmp_path):
        remove_known_hosts(str(tmp_path / "never-written.key"))
        remove_known_hosts(None)

    def test_it_refuses_to_write_through_a_symlink(self, tmp_path):
        """Mirrors the O_NOFOLLOW on the private-key write: the
        directory is ours, but writing through a planted symlink should
        fail loudly rather than silently."""
        key = tmp_path / "id.key"
        key.write_text("private")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.write_text("")
        os.symlink(elsewhere, known_hosts_path_for(str(key)))
        with pytest.raises(OSError):
            write_known_hosts(ENTRY, str(key))
