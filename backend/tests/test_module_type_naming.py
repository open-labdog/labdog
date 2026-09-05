"""BUG-68: the /etc/hosts module status was written under a name nothing read.

``hosts/dependents.py`` wrote ``module_type="hosts_entries"``; every
consumer — the drift API and task, the state API, the metrics aggregates,
the orchestrator's type mapping, the frontend — reads ``"hosts_file"``.

So when a referenced host's IP changed, ``invalidate_host_ref_dependents``
dutifully raised the drift flag on each dependant... onto a brand-new row
that nothing ever looked at. The row they *did* read still said
``in_sync``, and their ``/etc/hosts`` kept pointing at the old address.
No error, no drift, no sync — the failure is entirely silent.

The firewall half of the same two-element loop used the right name, which
is what made this survive: the loop looks symmetrical and is right half
the time.
"""

import pytest
from sqlalchemy import select

from app.models.host_module_status import HostModuleStatus
from app.module_types import LEGACY_MODULE_TYPES, ModuleType
from tests.conftest import create_host

pytestmark = pytest.mark.integration


class TestTheNameWrittenIsTheNameRead:
    async def test_invalidation_writes_the_name_consumers_read(self, db):
        """The bug, directly: a dependant's flag must land on the row the
        drift API and the sync path actually query."""
        from app.hosts.dependents import invalidate_host_ref_dependents
        from app.hosts_mgmt.models import HostsEntry

        referenced = await create_host(db, hostname="ref-host", ip="10.9.0.1")
        dependant = await create_host(db, hostname="dep-host", ip="10.9.0.2")
        db.add(
            HostsEntry(
                host_id=dependant.id,
                host_ref_id=referenced.id,
                aliases=[],
                priority=0,
                is_system=False,
            )
        )
        await db.flush()

        await invalidate_host_ref_dependents(db, referenced.id)
        await db.flush()

        rows = (
            (
                await db.execute(
                    select(HostModuleStatus).where(HostModuleStatus.host_id == dependant.id)
                )
            )
            .scalars()
            .all()
        )
        written = {r.module_type for r in rows}
        assert ModuleType.hosts_file in written, (
            f"the dirty flag went to {written} — nothing reads those"
        )
        assert "hosts_entries" not in written

    async def test_the_summary_uses_the_same_name(self, db):
        from app.hosts.dependents import invalidate_host_ref_dependents

        host = await create_host(db, hostname="lonely", ip="10.9.0.3")
        summary = await invalidate_host_ref_dependents(db, host.id)
        assert set(summary) == {ModuleType.firewall, ModuleType.hosts_file}


class TestTheNamesAreNamedOnce:
    def test_no_module_writes_the_legacy_name(self):
        """A source check, because the original bug was one literal in one
        place and nothing connected it to its readers."""
        import ast
        from pathlib import Path

        offenders = []
        for path in Path("app").rglob("*.py"):
            if path.name in {"module_types.py"}:
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and node.value in LEGACY_MODULE_TYPES:
                    # The GitOps importer's `module=` is a YAML section name,
                    # a different namespace that legitimately shares the word.
                    if "gitops" in str(path) or "models.py" in path.name:
                        continue
                    offenders.append(f"{path}:{node.lineno}")
        assert not offenders, (
            f"legacy module_type name written at {offenders}; use ModuleType so "
            "the writer and the readers cannot drift apart again"
        )

    @pytest.mark.parametrize("member", list(ModuleType))
    def test_every_name_is_a_plain_string(self, member):
        """These go into a String(50) column and are compared against raw
        literals all over; a StrEnum that did not compare equal to its own
        value would be worse than the bug."""
        assert member == str(member)
        assert isinstance(member, str)

    def test_the_orchestrator_knows_every_module(self):
        """`_MODULE_TYPE_MAPPING` translates between the orchestrator's
        vocabulary and these DB names; a name absent from it cannot be
        re-dispatched after a defer."""
        from app.tasks.host_sync_orchestrator import _DB_TO_CANONICAL

        missing = [m for m in ModuleType if str(m) not in _DB_TO_CANONICAL]
        assert not missing, f"{missing} have no orchestrator mapping"
