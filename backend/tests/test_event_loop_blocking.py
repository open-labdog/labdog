"""BUG-71: work that blocks must not run on the event loop.

The API is a single uvicorn worker. A coroutine that calls a blocking
function does not just make its own request wait — it stops the loop,
and with it every other request, ``/health``, the SSE streams and the
terminal WebSocket, for as long as the call takes. Two of the callers
here shell out to git, which is given 120 s per invocation before it is
killed, and one of them is reached from an HTTP handler.

These tests assert the property rather than the shape of the code: a
blocking call is started, and a second coroutine has to make progress
while it is still running.
"""

import asyncio
import threading
import time

#: Long enough that a blocked loop cannot possibly tick past it, short
#: enough that the suite does not notice.
_BLOCK_SECONDS = 0.4


async def _ticks_while(coro) -> tuple[int, object]:
    """Run *coro*, counting how many times the loop got back to us."""
    ticks = 0
    task = asyncio.ensure_future(coro)
    while not task.done():
        ticks += 1
        await asyncio.sleep(0.01)
    return ticks, task.result()


class TestTheGitCloneRunsInAThread:
    async def test_the_loop_keeps_ticking_during_a_slow_clone(self, monkeypatch):
        from app.packs import clone as clone_mod

        calling_threads: list[int] = []

        def _slow_clone(url, ref, path, *, auth=None):  # noqa: ARG001
            calling_threads.append(threading.get_ident())
            time.sleep(_BLOCK_SECONDS)
            return "deadbeef" * 5

        class _NoAuth:
            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def learned_host_key(self):
                return None

        monkeypatch.setattr(clone_mod, "sync_remote_pack", _slow_clone)
        monkeypatch.setattr(clone_mod, "git_auth_context", lambda **_kw: _NoAuth())

        ticks, result = await _ticks_while(
            clone_mod.clone_to_thread(
                "file:///nowhere",
                "main",
                "/tmp/labdog-test-clone",
                ssh_key=None,
                token=None,
                host_key_entry=None,
            )
        )

        assert result == ("deadbeef" * 5, None)
        assert ticks > 1, "the event loop was blocked for the whole clone"
        assert calling_threads[0] != threading.get_ident(), "the clone ran on the loop's thread"

    async def test_the_learned_host_key_comes_back_to_the_caller(self, monkeypatch):
        """Trust-on-first-use is a database write, so it must not happen
        inside the worker thread — the key is returned instead."""
        from app.packs import clone as clone_mod

        class _LearningAuth:
            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def learned_host_key(self):
                return "git.example.com ssh-ed25519 AAAA"

        monkeypatch.setattr(clone_mod, "sync_remote_pack", lambda *a, **k: "abc123")
        monkeypatch.setattr(clone_mod, "git_auth_context", lambda **_kw: _LearningAuth())

        sha, learned = await clone_mod.clone_to_thread(
            "ssh://git@git.example.com/x",
            "main",
            "/tmp/labdog-test-clone",
            ssh_key=None,
            token=None,
            host_key_entry=None,
        )
        assert sha == "abc123"
        assert learned == "git.example.com ssh-ed25519 AAAA"


class TestStartupDoesNotWaitForGit:
    """An unreachable pack remote used to hold the lifespan open past the
    container healthcheck, and the orchestrator restarted the process —
    a loop that cannot resolve itself, because restarting does not make
    the remote reachable."""

    async def test_the_app_serves_before_the_pack_sync_finishes(self, monkeypatch):
        import app.main as main_mod

        reload_calls: list[str] = []
        sync_started = asyncio.Event()
        release_sync = asyncio.Event()

        async def _slow_sync_enabled_packs(_session):
            sync_started.set()
            await release_sync.wait()
            reload_calls.append("sync")
            return []

        async def _reload_registry_async(_session):
            reload_calls.append("reload")

        async def _refresh_settings_cache(_session):
            return None

        class _FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

        monkeypatch.setattr("app.db.AsyncSessionLocal", lambda: _FakeSession())
        monkeypatch.setattr("app.packs.service.sync_enabled_packs", _slow_sync_enabled_packs)
        monkeypatch.setattr("app.actions.registry.reload_registry_async", _reload_registry_async)
        monkeypatch.setattr("app.settings_service.refresh_settings_cache", _refresh_settings_cache)

        async with main_mod._lifespan(None):
            # Startup has completed: the registry was folded in from
            # what is already on disk, and the git sync is still running.
            assert reload_calls == ["reload"]
            await asyncio.wait_for(sync_started.wait(), timeout=2)
            release_sync.set()
            for _ in range(200):
                if len(reload_calls) == 3:
                    break
                await asyncio.sleep(0.01)

        # ...and once the remote answers, the fresh checkout is folded in.
        assert reload_calls == ["reload", "sync", "reload"]

    async def test_a_failing_pack_sync_does_not_take_the_app_down(self, monkeypatch):
        import app.main as main_mod

        async def _boom(_session):
            raise RuntimeError("remote unreachable")

        async def _noop(_session):
            return None

        class _FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

        monkeypatch.setattr("app.db.AsyncSessionLocal", lambda: _FakeSession())
        monkeypatch.setattr("app.packs.service.sync_enabled_packs", _boom)
        monkeypatch.setattr("app.actions.registry.reload_registry_async", _noop)
        monkeypatch.setattr("app.settings_service.refresh_settings_cache", _noop)

        async with main_mod._lifespan(None):
            await asyncio.sleep(0.05)


class TestThePackWalkRunsInAThread:
    """``reload_registry_async`` reaches ``pack_policy``, which does an
    ``rglob("*")`` over every enabled pack's root and parses the YAML of
    every playbook it finds. Four API handlers call it, and the cost
    scales with repository size — which is the operator's to choose."""

    async def test_the_loop_keeps_ticking_while_packs_are_walked(self, db, monkeypatch):
        import app.actions.packs as packs_mod
        import app.actions.registry as registry_mod

        real_load = packs_mod.load_packs_with_resolutions
        calling_threads: list[int] = []

        def _slow_load(*args, **kwargs):
            calling_threads.append(threading.get_ident())
            time.sleep(_BLOCK_SECONDS)
            return real_load(*args, **kwargs)

        monkeypatch.setattr(packs_mod, "load_packs_with_resolutions", _slow_load)
        # Leave the process-wide registry alone; this is about where the
        # rebuild runs, not what it produces.
        monkeypatch.setattr(registry_mod, "_install", lambda _result: None)

        ticks, _ = await _ticks_while(registry_mod.reload_registry_async(db))

        assert ticks > 1, "the event loop was blocked for the whole pack walk"
        assert calling_threads[0] != threading.get_ident(), "the walk ran on the loop's thread"


class TestTheCheckoutRemovalRunsInAThread:
    async def test_the_loop_keeps_ticking_while_a_checkout_is_deleted(self, monkeypatch, tmp_path):
        from app.packs import service as service_mod

        calling_threads: list[int] = []

        def _slow_rmtree(_path):
            calling_threads.append(threading.get_ident())
            time.sleep(_BLOCK_SECONDS)

        monkeypatch.setattr(service_mod, "checkout_path_for", lambda _pack_id: tmp_path)
        monkeypatch.setattr("shutil.rmtree", _slow_rmtree)

        ticks, _ = await _ticks_while(service_mod.delete_checkout_async(1))

        assert ticks > 1, "the event loop was blocked for the whole rmtree"
        assert calling_threads[0] != threading.get_ident()
