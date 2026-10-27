"""BUG-96: a registry rebuild never loses to another process's.

On lin-manager, 2026-09-28, the API and the ``work`` worker rebuilt the
action registry at the same moment after the container was recreated.
Both replaced ``action_registry_snapshot`` with a DELETE and an INSERT;
the API's INSERT failed on the primary key, and because the API only
installed what it had merged once that write succeeded, it went on
serving the bundled pack alone. Every git pack's actions were gone from
the UI, with both packs reporting healthy.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, insert, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.actions import registry
from app.actions.registry import ACTION_REGISTRY, reload_registry_async
from app.models.git_repository import GitAuthType, GitRepository
from app.packs.locks import PACK_CHECKOUT_LOCK_CLASS
from app.packs.models import ActionPack, ActionRegistrySnapshot, ActionResolution, PackSourceType

pytestmark = pytest.mark.integration


def _make_pack(root: Path, *keys: str) -> Path:
    for key in keys:
        action_dir = root / "actions" / key
        action_dir.mkdir(parents=True)
        (action_dir / "playbook.yml").write_text(f"---\n- name: {key}\n  hosts: all\n  tasks: []\n")
        (action_dir / "manifest.yml").write_text(
            f"key: {key}\n"
            f"name: {key}\n"
            "description: demo\n"
            "icon: Box\n"
            "playbook: playbook.yml\n"
            'version: "1.0"\n'
            'estimated_duration: "1 min"\n'
        )
    return root


@pytest.fixture(autouse=True)
def keep_the_registry():
    """Put the process-wide registry back as it was: these tests rebuild it."""
    saved = dict(ACTION_REGISTRY)
    saved_contributors = dict(registry.ACTION_REGISTRY_CONTRIBUTORS)
    yield
    ACTION_REGISTRY.clear()
    ACTION_REGISTRY.update(saved)
    registry.ACTION_REGISTRY_CONTRIBUTORS.clear()
    registry.ACTION_REGISTRY_CONTRIBUTORS.update(saved_contributors)


def _local_pack(name: str, path: Path) -> ActionPack:
    return ActionPack(
        name=name,
        source_type=PackSourceType.LOCAL,
        local_path=str(path),
        enabled=True,
    )


async def _snapshot(db) -> dict[str, int | None]:
    rows = await db.execute(
        select(ActionRegistrySnapshot.action_key, ActionRegistrySnapshot.pack_id)
    )
    return dict(rows.all())


# ---------------------------------------------------------------------------
# Two processes rebuilding at once
# ---------------------------------------------------------------------------


class TestTwoRebuildsAtOnce:
    async def test_both_install_the_full_registry_and_the_snapshot_is_whole(self, pg_url, tmp_path):
        """Two sessions on two connections, as the API and the worker are.

        Committed for real, unlike the rest of the suite, because the race
        is between two transactions; everything it writes is removed after.
        """
        engine = create_async_engine(pg_url, pool_size=4, max_overflow=4)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        pack_path = _make_pack(tmp_path / "pack", "race-one", "race-two")

        async with sessions() as setup:
            saved_snapshot = await _snapshot(setup)
            pack = _local_pack("race-pack", pack_path)
            setup.add(pack)
            await setup.flush()
            pack_id = pack.id
            # A snapshot already on file is what made the second DELETE
            # miss the first one's rows.
            await setup.execute(delete(ActionRegistrySnapshot))
            await setup.execute(
                insert(ActionRegistrySnapshot),
                [{"action_key": "race-one", "pack_id": pack_id}],
            )
            await setup.commit()

        installed: list[set[str]] = []
        real_install = registry._install

        def _record(result):
            real_install(result)
            installed.append(set(result.registry))

        reads = 0
        first_is_reading = asyncio.Event()
        let_first_finish = asyncio.Event()
        real_read = registry._load_resolutions_and_snapshot_async

        async def _read(db):
            # The first rebuild stops just after it starts reading, with the
            # registry lock held, until the test lets it go.
            nonlocal reads
            reads += 1
            if reads == 1:
                first_is_reading.set()
                await let_first_finish.wait()
            return await real_read(db)

        async def _rebuild():
            async with sessions() as db:
                await reload_registry_async(db)

        tasks: list[asyncio.Task] = []
        try:
            with (
                patch.object(registry, "_install", side_effect=_record),
                patch.object(registry, "_load_resolutions_and_snapshot_async", side_effect=_read),
            ):
                tasks.append(asyncio.create_task(_rebuild()))
                await first_is_reading.wait()
                tasks.append(asyncio.create_task(_rebuild()))
                await asyncio.sleep(0.5)
                # Without the lock the second one was reading the same
                # snapshot by now, and went on to DELETE and INSERT over
                # the first one's — the primary-key failure the API hit.
                waited = reads == 1
                let_first_finish.set()
                await asyncio.gather(*tasks)
                assert waited, "the second rebuild did not wait for the first"

            assert reads == 2
            assert len(installed) == 2
            assert all({"race-one", "race-two"} <= keys for keys in installed)
            async with sessions() as check:
                snapshot = await _snapshot(check)
            assert snapshot["race-one"] == pack_id
            assert snapshot["race-two"] == pack_id
        finally:
            let_first_finish.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            async with sessions() as cleanup:
                await cleanup.execute(delete(ActionPack).where(ActionPack.id == pack_id))
                await cleanup.execute(delete(ActionRegistrySnapshot))
                if saved_snapshot:
                    await cleanup.execute(
                        insert(ActionRegistrySnapshot),
                        [{"action_key": k, "pack_id": v} for k, v in saved_snapshot.items()],
                    )
                await cleanup.commit()
            await engine.dispose()


# ---------------------------------------------------------------------------
# The registry is installed whatever happens to the record of it
# ---------------------------------------------------------------------------


class TestTheRegistryIsInstalledFirst:
    async def test_a_failed_write_still_installs_what_was_merged(self, db, tmp_path):
        db.add(_local_pack("install-first", _make_pack(tmp_path / "p", "installed-anyway")))
        await db.commit()
        before = await _snapshot(db)

        with patch.object(
            registry,
            "_persist_merge_outcome_async",
            side_effect=RuntimeError("duplicate key value violates unique constraint"),
        ):
            await reload_registry_async(db)

        assert "installed-anyway" in ACTION_REGISTRY
        # Rolled back to its savepoint, not past it.
        assert await _snapshot(db) == before

    async def test_a_failed_write_keeps_the_callers_own_work(self, db, tmp_path):
        """Callers reload with their own changes in flight — the
        orchestrator claims its run first — and rely on the reload's
        commit for them."""
        pack = _local_pack("callers-work", _make_pack(tmp_path / "p", "kept"))
        db.add(pack)
        await db.flush()
        pack.last_sync_error = "written before the reload"

        with patch.object(
            registry, "_persist_merge_outcome_async", side_effect=RuntimeError("boom")
        ):
            await reload_registry_async(db)

        db.expire_all()
        row = (
            await db.execute(select(ActionPack).where(ActionPack.name == "callers-work"))
        ).scalar_one()
        assert row.last_sync_error == "written before the reload"


# ---------------------------------------------------------------------------
# A partial view is not recorded
# ---------------------------------------------------------------------------


class TestAPackMissingOnDiskIsUnknownNotEmpty:
    async def test_its_pin_and_its_last_known_winner_survive(self, db, tmp_path):
        """A fresh container before its first sync. The two present packs
        contest ``shared``, which the operator pinned to the pack that is
        not on disk yet. Read as contributing nothing, that pack's pin was
        dropped as stale, and its keys were recorded under whoever won
        without it — so once it was back, the next rebuild froze that
        other winner instead."""
        present_a = _local_pack("present-a", _make_pack(tmp_path / "a", "shared", "only-a"))
        present_b = _local_pack("present-b", _make_pack(tmp_path / "b", "shared"))
        absent = _local_pack("not-cloned-yet", tmp_path / "nothing-here")
        db.add_all([present_a, present_b, absent])
        await db.flush()
        db.add(ActionResolution(action_key="shared", pack_id=absent.id, decided_by_user_id=None))
        await db.execute(delete(ActionRegistrySnapshot))
        await db.execute(
            insert(ActionRegistrySnapshot), [{"action_key": "shared", "pack_id": absent.id}]
        )
        await db.commit()

        await reload_registry_async(db)

        # Served regardless: what is on disk is the best there is for now.
        assert "only-a" in ACTION_REGISTRY
        pin = (
            await db.execute(
                select(ActionResolution).where(ActionResolution.action_key == "shared")
            )
        ).scalar_one_or_none()
        assert pin is not None and pin.pack_id == absent.id
        snapshot = await _snapshot(db)
        assert snapshot["shared"] == absent.id
        # The rest of the rebuild is recorded as usual.
        assert snapshot["only-a"] == present_a.id


# ---------------------------------------------------------------------------
# POST /api/actions/refresh
# ---------------------------------------------------------------------------


class TestRefreshKeepsTheDbPacks:
    async def test_refresh_rebuilds_with_the_db_packs(
        self, superuser_client, db, tmp_path, monkeypatch
    ):
        """It called a synchronous rebuild that needs psycopg2, which LabDog
        does not install, fell back to the bundled pack and installed that."""
        from app.actions.git_sync import GitSyncError
        from app.config import settings
        from app.packs import service

        # The git pack every install is seeded with cannot be reached from
        # here; a pack that fails to sync must not stop the rebuild.
        monkeypatch.setattr(settings.ansible, "packs_root_dir", str(tmp_path / "packs"))
        monkeypatch.setattr(
            service, "clone_to_thread", AsyncMock(side_effect=GitSyncError("offline"))
        )
        db.add(_local_pack("refresh-pack", _make_pack(tmp_path / "p", "refreshed")))
        await db.commit()

        resp = await superuser_client.post("/api/actions/refresh")

        assert resp.status_code == 200, resp.text
        assert "refresh-pack" in resp.json()["packs"]
        assert "refreshed" in ACTION_REGISTRY


# ---------------------------------------------------------------------------
# One git run per checkout at a time
# ---------------------------------------------------------------------------


class TestASyncHoldsItsCheckout:
    async def test_the_checkout_lock_is_held_while_git_runs(
        self, db, pg_url, tmp_path, monkeypatch
    ):
        from app.config import settings
        from app.packs import service

        monkeypatch.setattr(settings.ansible, "packs_root_dir", str(tmp_path / "packs"))
        repo = GitRepository(
            name="lock-test-repo",
            url="file:///nowhere",
            branch="main",
            auth_type=GitAuthType.ssh_key,
            ssh_key_id=None,
        )
        db.add(repo)
        await db.flush()
        pack = ActionPack(
            name="lock-test-pack",
            source_type=PackSourceType.GIT,
            git_repository_id=repo.id,
            enabled=True,
        )
        db.add(pack)
        await db.flush()

        held: list[int] = []
        engine = create_async_engine(pg_url)

        async def _clone(*_args, **_kwargs):
            # Asked from another connection, as another process would see it.
            async with engine.connect() as other:
                count = await other.scalar(
                    text(
                        "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                        "AND classid = :cls AND objid = :pack AND objsubid = 2 AND granted"
                    ),
                    {"cls": PACK_CHECKOUT_LOCK_CLASS, "pack": pack.id},
                )
            held.append(count)
            return "0" * 40, None

        try:
            with patch.object(service, "clone_to_thread", side_effect=_clone):
                assert await service.sync_pack(db, pack) is True
        finally:
            await engine.dispose()

        assert held == [1]
