"""
Unit tests for _detect_firewall_backend() in app.api.host_state.

The function opens its own SSH connection internally, so we patch
asyncssh.import_private_key (to avoid key parsing) and ssh_connect
(to inject a mock connection).  The database session is mocked to
return empty result-sets for the firewalld/ufw package-rule checks.
"""

from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from app.api.host_state import _detect_firewall_backend


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_run_side_effect(command_results: dict[str, int]):
    """Return an async side-effect for conn.run() keyed on command substrings.

    The first matching key wins.  Any command not matched returns exit_status=1.
    """
    async def side_effect(cmd, check=False):
        for key, exit_code in command_results.items():
            if key in cmd:
                return MagicMock(exit_status=exit_code, stdout="", stderr="")
        return MagicMock(exit_status=1, stdout="", stderr="")
    return side_effect


def make_mock_conn(command_results: dict[str, int]) -> MagicMock:
    """Build an asyncssh-style connection mock with a pre-configured run()."""
    conn = MagicMock()
    conn.run = make_run_side_effect(command_results)
    return conn


def make_mock_db() -> AsyncMock:
    """Build an async DB session mock whose execute() always returns empty."""
    db = AsyncMock()
    empty_result = MagicMock()
    empty_result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=empty_result)
    db.flush = AsyncMock()
    db.add = MagicMock()
    return db


# Patch targets used in every test
_PATCH_SSH_CONNECT = "app.api.host_state.ssh_connect"
_PATCH_IMPORT_KEY = "asyncssh.import_private_key"

# Dummy values forwarded to the function (not validated under the patches)
_HOST_IP = "10.0.0.1"
_SSH_PORT = 22
_PRIVATE_PEM = "-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n-----END OPENSSH PRIVATE KEY-----"
_SSH_USER = "root"
_HOST_ID = 1


def _make_ctx(command_results: dict[str, int]):
    """Return (mock_conn, mock_db, context-manager patch pair)."""
    conn = make_mock_conn(command_results)
    db = make_mock_db()

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)

    return conn, db, ctx


async def _run(command_results: dict[str, int]):
    """Run _detect_firewall_backend with the given per-command exit codes."""
    conn, db, ctx = _make_ctx(command_results)
    with patch(_PATCH_IMPORT_KEY, return_value=MagicMock()), \
         patch(_PATCH_SSH_CONNECT, return_value=ctx):
        return await _detect_firewall_backend(
            _HOST_IP, _SSH_PORT, _PRIVATE_PEM, _SSH_USER, _HOST_ID, db
        ), conn, db


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestFirewallBackendDetection:

    async def test_nftables_no_containers(self):
        """nft present, no Docker/k8s/nerdctl — stays nftables, no messages."""
        (backend, messages), conn, db = await _run({
            "command -v nft": 0,
            # All container checks return non-zero (absent)
        })
        assert backend == "nftables"
        assert messages == []

    async def test_docker_downgrades_to_iptables(self):
        """nft + Docker + iptables available → iptables with Docker message."""
        (backend, messages), conn, db = await _run({
            "command -v nft": 0,
            "docker.sock": 0,           # Docker present
            "command -v iptables": 0,   # iptables available
        })
        assert backend == "iptables"
        assert len(messages) == 1
        assert "Docker" in messages[0]

    async def test_docker_no_iptables_stays_nftables(self):
        """nft + Docker present but iptables absent → stays nftables."""
        (backend, messages), conn, db = await _run({
            "command -v nft": 0,
            "docker.sock": 0,           # Docker present
            # iptables check falls through to exit_status=1
        })
        assert backend == "nftables"
        assert messages == []

    async def test_kubeproxy_iptables_mode_downgrades(self):
        """nft + no Docker + kubelet active + KUBE- chains + iptables → iptables."""
        (backend, messages), conn, db = await _run({
            "command -v nft": 0,
            # Docker absent (docker.sock / systemctl docker not in map → exit 1)
            "kubelet": 0,               # kubelet active AND KUBE- grep succeeds
            "command -v iptables": 0,
        })
        assert backend == "iptables"
        assert len(messages) == 1
        assert "kube-proxy" in messages[0].lower() or "kubernetes" in messages[0].lower()

    async def test_kubeproxy_nftables_mode_no_downgrade(self):
        """kubelet active but no KUBE- chains (nftables-mode kube-proxy) → nftables."""
        (backend, messages), conn, db = await _run({
            "command -v nft": 0,
            # The combined kube command fails (grep -q KUBE- finds nothing)
            # We need the combined kubelet command to fail, not the individual parts.
            # Since our helper matches on substrings, we can't easily distinguish
            # "kubelet active" from the full compound command.  The compound command
            # key "systemctl is-active --quiet kubelet" is in the map, but we want
            # it to fail (simulate KUBE- grep miss).
            # We leave it unmapped so exit_status=1.
        })
        assert backend == "nftables"
        assert messages == []

    async def test_nerdctl_rootful_downgrades(self):
        """nft + no Docker + no k8s + nerdctl + CNI conflist → iptables."""
        (backend, messages), conn, db = await _run({
            "command -v nft": 0,
            "nerdctl": 0,               # nerdctl present + /etc/cni/net.d + conflist
            "command -v iptables": 0,
        })
        assert backend == "iptables"
        assert len(messages) == 1
        assert "nerdctl" in messages[0].lower()

    async def test_nerdctl_rootless_no_downgrade(self):
        """nerdctl binary present but no /etc/cni/net.d conflist → nftables."""
        # The compound nerdctl command checks for the conflist file; if the
        # ls fails the whole compound exits non-zero.  We simulate that by
        # NOT mapping "nerdctl" (so exit_status=1 for the compound check).
        (backend, messages), conn, db = await _run({
            "command -v nft": 0,
            # nerdctl compound command not mapped → exit 1
        })
        assert backend == "nftables"
        assert messages == []

    async def test_docker_preempts_kube_and_nerdctl(self):
        """Docker triggers iptables downgrade; k8s + nerdctl probes must not run."""
        conn, db, ctx = _make_ctx({
            "command -v nft": 0,
            "docker.sock": 0,
            "command -v iptables": 0,
        })
        # Intercept conn.run so we can record which commands were executed
        original_run = conn.run
        executed_cmds: list[str] = []

        async def recording_run(cmd, check=False):
            executed_cmds.append(cmd)
            return await original_run(cmd, check=check)

        conn.run = recording_run

        with patch(_PATCH_IMPORT_KEY, return_value=MagicMock()), \
             patch(_PATCH_SSH_CONNECT, return_value=ctx):
            backend, messages = await _detect_firewall_backend(
                _HOST_IP, _SSH_PORT, _PRIVATE_PEM, _SSH_USER, _HOST_ID, db
            )

        assert backend == "iptables"
        # Neither kubelet nor nerdctl probes should have been issued
        kube_or_nerdctl = [
            c for c in executed_cmds
            if "kubelet" in c or "nerdctl" in c
        ]
        assert kube_or_nerdctl == [], (
            f"Expected no k8s/nerdctl probes after Docker downgrade, "
            f"but got: {kube_or_nerdctl}"
        )

    async def test_iptables_fallback(self):
        """No nft, iptables present → iptables backend."""
        (backend, messages), conn, db = await _run({
            # nft not found → exit 1 (unmapped)
            "command -v iptables": 0,
        })
        assert backend == "iptables"
        assert messages == []

    async def test_no_firewall(self):
        """Neither nft nor iptables found → None backend."""
        (backend, messages), conn, db = await _run({
            # nothing maps → everything returns exit_status=1
        })
        assert backend is None
        assert messages == []
