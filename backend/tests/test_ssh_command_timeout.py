"""BUG-66: a command on an open SSH session must have a deadline.

``connect_timeout`` bounds getting *to* a host. Nothing bounded what
happened afterwards. Thirty of thirty-two ``conn.run()`` call sites
passed no ``timeout``, so a host that completed TCP and authentication
and then hung — a wedged ``nft list ruleset``, a stuck NFS mount under
``systemctl list-units``, a process in D state — held the caller
indefinitely.

That is what made the drift sweep and ``collect-state`` unrecoverable
rather than merely slow: both walk hosts in one serial loop, so a single
unresponsive host stopped every host after it from being checked at all,
silently, on every tick.

The deadline is bound at the connection rather than at each call site.
Every SSH connection LabDog opens comes from ``app.ssh_utils``, so
binding it there covers the call sites that exist and the ones written
later, and no one can opt out by forgetting. These tests assert that
property — that the *connection* carries it — rather than counting call
sites, because counting call sites is what stops being true.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ssh_utils import (
    SSH_COMMAND_TIMEOUT,
    BoundedConnection,
    _get_command_timeout,
)


@pytest.fixture
def raw():
    conn = MagicMock()
    conn.run = MagicMock(return_value="sentinel")
    return conn


class TestEveryCommandGetsADeadline:
    def test_a_bare_run_is_given_the_configured_timeout(self, raw):
        BoundedConnection(raw, 42).run("uptime")
        assert raw.run.call_args.kwargs["timeout"] == 42

    def test_other_arguments_are_passed_through_untouched(self, raw):
        BoundedConnection(raw, 42).run("uptime", check=False)
        assert raw.run.call_args.args == ("uptime",)
        assert raw.run.call_args.kwargs["check"] is False

    def test_a_caller_that_wants_longer_gets_longer(self, raw):
        """The bound is a floor on care, not a ceiling on intent — a
        caller with a slow command it knows about still wins."""
        BoundedConnection(raw, 42).run("slow-thing", timeout=600)
        assert raw.run.call_args.kwargs["timeout"] == 600

    def test_an_explicit_none_is_respected(self, raw):
        """`timeout=None` is how asyncssh spells "no deadline". Silently
        overriding it would make the escape hatch unusable."""
        BoundedConnection(raw, 42).run("interactive", timeout=None)
        assert raw.run.call_args.kwargs["timeout"] is None

    def test_the_result_is_returned_unchanged(self, raw):
        assert BoundedConnection(raw, 42).run("uptime") == "sentinel"


class TestEverythingElseDelegates:
    """The wrapper must be invisible for every other use — in particular
    ``create_process``, which the interactive web terminal opens and which
    must *not* carry a command deadline."""

    def test_create_process_is_not_intercepted(self, raw):
        raw.create_process = MagicMock(return_value="proc")
        assert BoundedConnection(raw, 42).create_process(term_type="xterm") == "proc"
        assert "timeout" not in raw.create_process.call_args.kwargs

    @pytest.mark.parametrize(
        "attr", ["close", "wait_closed", "get_server_host_key", "get_extra_info"]
    )
    def test_the_connection_api_still_works(self, raw, attr):
        setattr(raw, attr, MagicMock(return_value=attr))
        assert getattr(BoundedConnection(raw, 42), attr)() == attr

    def test_the_underlying_connection_is_reachable(self, raw):
        assert BoundedConnection(raw, 42).wrapped is raw


class TestTheTimeoutComesFromSettings:
    def test_it_reads_the_app_setting(self, monkeypatch):
        import app.settings_service as svc

        monkeypatch.setattr(svc, "get_setting_cached_typed", lambda key: 123)
        assert _get_command_timeout() == 123

    def test_it_falls_back_when_the_cache_is_cold(self, monkeypatch):
        """Called from Celery tasks that may run before the cache warms;
        a missing setting must not mean "no deadline"."""
        import app.settings_service as svc

        def _boom(key):
            raise RuntimeError("cache not warm")

        monkeypatch.setattr(svc, "get_setting_cached_typed", _boom)
        assert _get_command_timeout() == SSH_COMMAND_TIMEOUT

    def test_the_setting_is_registered_so_it_is_editable(self):
        from app.settings_service import SETTING_DEFINITIONS

        defn = SETTING_DEFINITIONS["ssh.command_timeout"]
        assert defn["type"] == "int"
        assert defn["default"] == SSH_COMMAND_TIMEOUT
        assert defn["min"] >= 1, "zero or negative would mean 'expire immediately'"


class TestTheConnectionFactoriesBindIt:
    """The property that makes the call sites irrelevant."""

    async def test_ssh_connect_returns_a_bounded_connection(self, monkeypatch):
        import app.ssh_utils as ssh_utils

        fake = MagicMock()
        fake.wait_closed = AsyncMock()  # awaited on context exit
        monkeypatch.setattr(ssh_utils.asyncssh, "connect", AsyncMock(return_value=fake))
        async with ssh_utils.ssh_connect("10.0.0.1") as conn:
            assert isinstance(conn, BoundedConnection)
            assert conn.wrapped is fake

    async def test_no_connection_factory_hands_back_a_raw_connection(self):
        """Source-level: a new ``return self._conn`` in an __aenter__ would
        quietly reopen the whole class of bug."""
        import ast
        import inspect

        import app.ssh_utils as ssh_utils

        tree = ast.parse(inspect.getsource(ssh_utils))
        offenders = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.AsyncFunctionDef) and node.name == "__aenter__"):
                continue
            for ret in ast.walk(node):
                if (
                    isinstance(ret, ast.Return)
                    and isinstance(ret.value, ast.Attribute)
                    and ret.value.attr == "_conn"
                ):
                    offenders.append(ret.lineno)
        assert not offenders, (
            f"__aenter__ returns a raw connection at line(s) {offenders}; "
            "wrap it in BoundedConnection so its commands carry a deadline"
        )
