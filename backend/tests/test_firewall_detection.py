"""
Unit tests for _detect_firewall_backend() in app.api.host_state.

The function opens its own SSH connection internally, so we patch
asyncssh.import_private_key (to avoid key parsing) and ssh_connect
(to inject a mock connection).  The database session is mocked to
return empty result-sets for the firewalld/ufw package-rule checks.

conn.run() is mocked with a substring matcher (``make_run_side_effect``):
the first key that is a substring of the command wins, everything else
returns exit 1 (== "absent"/"false").  The token constants below are
chosen so each probe is matched by a substring unique to that probe's
command — see the ``_PROBE_*`` constants in host_state.  Because the
iptables "active" probe string is a superset of the iptables "LabDog"
probe string, its unique token (``T_IPT_ACTIVE``) must be inserted into
the mapping *before* ``T_IPT_LABDOG`` for the active case.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from app.api.host_state import _detect_firewall_backend, _probe_competing_firewall_store

# ---------------------------------------------------------------------------
# Probe tokens — each is a substring of exactly one _PROBE_* command
# ---------------------------------------------------------------------------

T_NFT_PRESENT = "command -v nft >/dev/null"  # _PROBE_NFT_PRESENT (labdog/active use "|| echo")
T_IPT_PRESENT = "command -v iptables >/dev/null"  # _PROBE_IPT_PRESENT
T_NFT_LABDOG = "Managed by LabDog"  # _PROBE_NFT_LABDOG only
T_NFT_ACTIVE = "hook input"  # _PROBE_NFT_ACTIVE only
T_IPT_ACTIVE = "grep -qvE"  # _PROBE_IPT_ACTIVE only (must precede T_IPT_LABDOG)
T_IPT_LABDOG = "LABDOG-INPUT >/dev/null 2>&1"  # in both ipt LabDog + active commands
T_DOCKER = "docker.sock"
T_KUBE = "kubelet"
T_NERDCTL = "nerdctl"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_run_side_effect(command_results: dict[str, int]):
    """Return an async side-effect for conn.run() keyed on command substrings.

    The first matching key (in insertion order) wins.  Any command not matched
    returns exit_status=1.
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
    with (
        patch(_PATCH_IMPORT_KEY, return_value=MagicMock()),
        patch(_PATCH_SSH_CONNECT, return_value=ctx),
    ):
        return (
            await _detect_firewall_backend(
                _HOST_IP, _SSH_PORT, _PRIVATE_PEM, _SSH_USER, _HOST_ID, db
            ),
            conn,
            db,
        )


# ---------------------------------------------------------------------------
# Single-backend cases
# ---------------------------------------------------------------------------


class TestSingleBackendPresent:
    async def test_only_nftables(self):
        (backend, messages), *_ = await _run({T_NFT_PRESENT: 0})
        assert backend == "nftables"
        assert any("Only nftables" in m for m in messages)

    async def test_only_iptables(self):
        (backend, messages), *_ = await _run({T_IPT_PRESENT: 0})
        assert backend == "iptables"
        assert any("Only iptables" in m for m in messages)

    async def test_neither_present(self):
        (backend, messages), *_ = await _run({})
        assert backend is None
        assert messages == []


# ---------------------------------------------------------------------------
# Both present — the decision ladder
# ---------------------------------------------------------------------------


class TestBothPresentLadder:
    async def test_stickiness_nftables_labdog(self):
        """Existing LabDog nftables ruleset → keep nftables."""
        (backend, messages), *_ = await _run(
            {T_NFT_PRESENT: 0, T_IPT_PRESENT: 0, T_NFT_LABDOG: 0}
        )
        assert backend == "nftables"
        assert any("keeping nftables" in m for m in messages)

    async def test_stickiness_iptables_labdog(self):
        """Existing LabDog iptables ruleset → keep iptables."""
        (backend, messages), *_ = await _run(
            {T_NFT_PRESENT: 0, T_IPT_PRESENT: 0, T_IPT_LABDOG: 0}
        )
        assert backend == "iptables"
        assert any("keeping iptables" in m for m in messages)

    async def test_stickiness_beats_container_constraint(self):
        """Stickiness (step 2) sits above the container constraint (step 3):
        a LabDog nftables ruleset is kept even when Docker is present."""
        (backend, messages), *_ = await _run(
            {T_NFT_PRESENT: 0, T_IPT_PRESENT: 0, T_NFT_LABDOG: 0, T_DOCKER: 0}
        )
        assert backend == "nftables"
        assert any("keeping nftables" in m for m in messages)
        # The container reason must not have been reached.
        assert not any("Docker" in m for m in messages)

    async def test_competing_store_warns_and_falls_through(self):
        """LabDog rules in BOTH backends → warn, then decide via tiebreakers
        (here Docker forces iptables)."""
        (backend, messages), *_ = await _run(
            {
                T_NFT_PRESENT: 0,
                T_IPT_PRESENT: 0,
                T_NFT_LABDOG: 0,
                T_IPT_LABDOG: 0,
                T_DOCKER: 0,
            }
        )
        assert backend == "iptables"
        assert any("BOTH" in m for m in messages)
        assert any("Docker" in m for m in messages)

    async def test_container_docker_forces_iptables(self):
        """No LabDog rules, Docker present → iptables (hard constraint)."""
        (backend, messages), *_ = await _run(
            {T_NFT_PRESENT: 0, T_IPT_PRESENT: 0, T_DOCKER: 0}
        )
        assert backend == "iptables"
        assert any("Docker" in m for m in messages)

    async def test_container_kubeproxy_forces_iptables(self):
        (backend, messages), *_ = await _run(
            {T_NFT_PRESENT: 0, T_IPT_PRESENT: 0, T_KUBE: 0}
        )
        assert backend == "iptables"
        assert any("kube" in m.lower() for m in messages)

    async def test_container_nerdctl_forces_iptables(self):
        (backend, messages), *_ = await _run(
            {T_NFT_PRESENT: 0, T_IPT_PRESENT: 0, T_NERDCTL: 0}
        )
        assert backend == "iptables"
        assert any("nerdctl" in m.lower() for m in messages)

    async def test_active_ruleset_tiebreak_nftables(self):
        """No LabDog rules, no container, only nftables has an active ruleset."""
        (backend, messages), *_ = await _run(
            {T_NFT_PRESENT: 0, T_IPT_PRESENT: 0, T_NFT_ACTIVE: 0}
        )
        assert backend == "nftables"
        assert any("active nftables" in m for m in messages)

    async def test_active_ruleset_tiebreak_iptables(self):
        """No LabDog rules, no container, only iptables has an active ruleset.

        T_IPT_ACTIVE is inserted before T_IPT_LABDOG so the iptables 'active'
        probe resolves true while its 'LabDog' probe resolves false."""
        (backend, messages), *_ = await _run(
            {T_NFT_PRESENT: 0, T_IPT_PRESENT: 0, T_IPT_ACTIVE: 0}
        )
        assert backend == "iptables"
        assert any("active iptables" in m for m in messages)

    async def test_default_nftables_on_greenfield(self):
        """Both installed, nothing configured → default to nftables."""
        (backend, messages), *_ = await _run({T_NFT_PRESENT: 0, T_IPT_PRESENT: 0})
        assert backend == "nftables"
        assert any("defaulting" in m for m in messages)

    async def test_docker_preempts_kube_and_nerdctl_probes(self):
        """Once Docker forces iptables, kube/nerdctl probes must not run."""
        conn, db, ctx = _make_ctx({T_NFT_PRESENT: 0, T_IPT_PRESENT: 0, T_DOCKER: 0})
        original_run = conn.run
        executed: list[str] = []

        async def recording_run(cmd, check=False):
            executed.append(cmd)
            return await original_run(cmd, check=check)

        conn.run = recording_run

        with (
            patch(_PATCH_IMPORT_KEY, return_value=MagicMock()),
            patch(_PATCH_SSH_CONNECT, return_value=ctx),
        ):
            backend, _messages = await _detect_firewall_backend(
                _HOST_IP, _SSH_PORT, _PRIVATE_PEM, _SSH_USER, _HOST_ID, db
            )

        assert backend == "iptables"
        assert [c for c in executed if "kubelet" in c or "nerdctl" in c] == []


# ---------------------------------------------------------------------------
# Competing-store probe (collect-time, on an already-known backend)
# ---------------------------------------------------------------------------


class TestCompetingStoreProbe:
    async def test_nftables_active_warns_on_stale_iptables(self):
        conn = make_mock_conn({T_IPT_LABDOG: 0})
        warning = await _probe_competing_firewall_store(conn, "nftables")
        assert warning is not None
        assert "iptables" in warning

    async def test_iptables_active_warns_on_stale_nftables(self):
        conn = make_mock_conn({T_NFT_LABDOG: 0})
        warning = await _probe_competing_firewall_store(conn, "iptables")
        assert warning is not None
        assert "nftables" in warning

    async def test_no_competing_store_returns_none(self):
        conn = make_mock_conn({})  # neither marker present
        assert await _probe_competing_firewall_store(conn, "nftables") is None
        assert await _probe_competing_firewall_store(conn, "iptables") is None
