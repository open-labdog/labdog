"""Nothing pack-supplied ships with LabDog.

The bundled pack used to be baked into the image and stood in for a
winner with ``pack_id NULL``. Every action now comes from an
``action_packs`` row, so a pin and a snapshot entry must name one, and a
registry built with no pack on disk holds the built-ins alone.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.actions import registry
from app.actions.registry import ACTION_REGISTRY, reload_registry_async

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("table", ["action_resolution", "action_registry_snapshot"])
async def test_a_winner_must_name_a_pack(db, table):
    with pytest.raises(IntegrityError, match="pack_id"):
        async with db.begin_nested():
            await db.execute(
                text(f"INSERT INTO {table} (action_key, pack_id) VALUES ('orphan', NULL)")
            )


async def test_with_no_pack_on_disk_the_registry_is_the_builtins(db):
    # The seeded labdog-playbooks pack may be enabled in the test
    # database, but nothing has synced it, so it has no checkout.
    try:
        await reload_registry_async(db)
        assert ACTION_REGISTRY
        assert all(defn.is_builtin for defn in ACTION_REGISTRY.values())
    finally:
        registry._BUILT_FROM = None
