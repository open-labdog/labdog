"""Tests for the SSH connection args injected into Ansible inventories (B2).

A ConnectTimeout bounds the initial connect; ServerAlive keepalives make
a host that freezes mid-task (TCP up, command frozen) surface within
minutes instead of riding out the whole playbook wall-clock timeout.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from app.ansible_runtime.inventory import build_ssh_common_args, generate_inventory
from app.ansible_runtime.runner import generate_multi_host_inventory


def test_build_ssh_common_args_uses_setting():
    with patch("app.settings_service.get_setting_sync_typed", return_value=25):
        args = build_ssh_common_args()
    assert "StrictHostKeyChecking=accept-new" in args
    assert "ConnectTimeout=25" in args
    assert "ServerAliveInterval=30" in args
    assert "ServerAliveCountMax=6" in args


def test_build_ssh_common_args_falls_back_on_error():
    with patch(
        "app.settings_service.get_setting_sync_typed",
        side_effect=RuntimeError("no db"),
    ):
        args = build_ssh_common_args()
    # Falls back to Ansible's default 10s connect timeout; never raises.
    assert "ConnectTimeout=10" in args
    assert "ServerAliveInterval=30" in args


def test_single_host_inventory_carries_ssh_args():
    with patch("app.settings_service.get_setting_sync_typed", return_value=10):
        inv = json.loads(generate_inventory("10.0.0.5", 22, "/dev/shm/k.key", hostname="node-1"))
    entry = next(iter(inv["all"]["hosts"].values()))
    common = entry["ansible_ssh_common_args"]
    assert "ConnectTimeout=10" in common
    assert "ServerAliveInterval=30" in common


def test_multi_host_inventory_carries_ssh_args():
    with patch("app.settings_service.get_setting_sync_typed", return_value=10):
        inv = json.loads(
            generate_multi_host_inventory(
                [
                    {
                        "name": "a",
                        "ip": "10.0.0.1",
                        "port": 22,
                        "ssh_user": "root",
                        "ssh_key_path": "/dev/shm/a.key",
                    }
                ]
            )
        )
    entry = inv["all"]["hosts"]["a"]
    assert "ConnectTimeout=10" in entry["ansible_ssh_common_args"]
    assert "ServerAliveInterval=30" in entry["ansible_ssh_common_args"]
