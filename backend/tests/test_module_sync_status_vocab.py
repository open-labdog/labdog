"""One spelling for one state on ``HostModuleStatus.sync_status``.

Three of the seven modules — packages, cron and linux users — wrote
``"drifted"`` where the other four wrote ``"out_of_sync"``. Nothing was
broken by it: the host rollup treated the two as equivalent and each drift
task translated before recording its metrics sample. It was two spellings
of one state that every consumer had to know about, and the translation
step is exactly the sort of thing that gets forgotten at the eighth call
site.

The source assertion below is the one that keeps it gone. A behavioural
test can only cover the paths it happens to exercise; a grep over the
package cannot miss a writer.
"""

import pathlib

import pytest

from app.api.host_state import refresh_host_sync_status
from app.models.host_module_status import HostModuleStatus
from tests.conftest import create_host

_APP = pathlib.Path(__file__).resolve().parent.parent / "app"

#: Vendored at build time from labdog-playbooks; not ours to lint.
_SKIP_DIRS = {"ansible"}


def _python_sources() -> list[pathlib.Path]:
    return [
        p
        for p in _APP.rglob("*.py")
        if not any(part in _SKIP_DIRS for part in p.relative_to(_APP).parts)
    ]


class TestNothingWritesTheLegacySpelling:
    def test_no_source_file_assigns_drifted(self):
        offenders = []
        for path in _python_sources():
            for lineno, line in enumerate(path.read_text().splitlines(), 1):
                if "sync_status" in line and '"drifted"' in line:
                    offenders.append(f"{path.relative_to(_APP)}:{lineno}: {line.strip()}")
        assert not offenders, (
            "HostModuleStatus.sync_status has one spelling for drift, 'out_of_sync' "
            "(migration 0037). Found:\n  " + "\n  ".join(offenders)
        )


class TestTheRollupStillSeesDrift:
    """``refresh_host_sync_status`` used to accept both spellings.

    Now it accepts one, so these check the surviving one still rolls up —
    dropping the wrong branch would have been a silent way to make every
    host read as in-sync.
    """

    @pytest.mark.parametrize(
        ("module_status", "expected"),
        [
            ("out_of_sync", "out_of_sync"),
            ("in_sync", "in_sync"),
            ("error", "error"),
        ],
    )
    async def test_a_single_module_sets_the_host_status(self, db, module_status, expected):
        host = await create_host(db, hostname=f"vocab-{module_status}")
        db.add(HostModuleStatus(host_id=host.id, module_type="cron", sync_status=module_status))
        await db.flush()

        await refresh_host_sync_status(host, db)

        assert host.sync_status.value == expected

    async def test_error_outranks_out_of_sync(self, db):
        host = await create_host(db, hostname="vocab-precedence")
        db.add(HostModuleStatus(host_id=host.id, module_type="cron", sync_status="out_of_sync"))
        db.add(HostModuleStatus(host_id=host.id, module_type="package", sync_status="error"))
        await db.flush()

        await refresh_host_sync_status(host, db)

        assert host.sync_status.value == "error"

    async def test_one_drifted_module_among_healthy_ones_still_shows(self, db):
        host = await create_host(db, hostname="vocab-mixed")
        db.add(HostModuleStatus(host_id=host.id, module_type="cron", sync_status="in_sync"))
        db.add(HostModuleStatus(host_id=host.id, module_type="package", sync_status="out_of_sync"))
        db.add(HostModuleStatus(host_id=host.id, module_type="service", sync_status="in_sync"))
        await db.flush()

        await refresh_host_sync_status(host, db)

        assert host.sync_status.value == "out_of_sync"
