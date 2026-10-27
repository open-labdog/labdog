"""Cross-process locks around the action registry and the pack checkouts.

Every LabDog process keeps its own copy of the action registry — the API
and both Celery workers — and rebuilds it at boot and again whenever it
needs to. Two of them, the API and the ``work`` worker, also clone or
pull every git pack into the same directories at boot. Nothing used to
serialise either, and the registry rebuild raced: each one replaces
``action_registry_snapshot`` with a DELETE and an INSERT, and under READ
COMMITTED the second DELETE cannot see the rows the first just
committed, so its INSERT failed on the primary key. The API lost that
race after a restart on 2026-09-28 and was left serving only the
bundled actions (BUG-96).

Both locks are transaction-level, so they release on whatever commit or
rollback ends the caller's transaction, and a process that dies holding
one releases it with its connection.

They use Postgres's two-key form. Every other advisory lock in LabDog is
taken with a single 64-bit key, the host locks among them keyed on the
host id itself, and the two key spaces never overlap.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: First key of the registry lock. The second is always 0.
REGISTRY_LOCK_CLASS = 1

#: First key of a pack-checkout lock. The second is the pack's id.
PACK_CHECKOUT_LOCK_CLASS = 2


async def lock_registry(db: AsyncSession) -> None:
    """Hold the registry for this transaction: one rebuild at a time, fleet-wide.

    Taken before the rebuild reads the resolutions and the snapshot, so
    it reads what the previous rebuild committed and replaces it whole.
    """
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:cls, 0)"),
        {"cls": REGISTRY_LOCK_CLASS},
    )


async def lock_pack_checkout(db: AsyncSession, pack_id: int) -> None:
    """Hold one pack's checkout for this transaction: one git run in it at a time.

    Two processes cloning into, or fetching and resetting, the same
    directory at once can leave it half-written or trip over each
    other's ``.git/index.lock``. The process that waits fetches again
    afterwards, which finds nothing new and costs a round trip.
    """
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:cls, :pack_id)"),
        {"cls": PACK_CHECKOUT_LOCK_CLASS, "pack_id": pack_id},
    )
