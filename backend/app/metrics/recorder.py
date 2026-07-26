from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.drift_sample import DriftSample


async def record_drift_sample(
    db: AsyncSession,
    *,
    host_id: int,
    module_type: str,
    status: str,
    add_count: int = 0,
    remove_count: int = 0,
    policy_change_count: int = 0,
    duration_ms: int | None = None,
) -> None:
    """Record one forward-only drift-check metrics sample.

    Mirrors the commit contract of ``app.audit.logger.log_action``: this
    function does **not** commit. It must be called on the caller's own
    session, immediately before that path's existing ``db.commit()``, so the
    sample is written atomically with the ``HostModuleStatus`` /
    ``Host.sync_status`` update it describes. Never open a second session to
    call this — asyncpg does not support concurrent operations on one
    connection ("another operation is in progress").

    Drift samples are metrics, not audit trail — do NOT route them through
    ``log_action`` / ``AuditLog``.
    """
    sample = DriftSample(
        host_id=host_id,
        module_type=module_type,
        status=status,
        add_count=add_count,
        remove_count=remove_count,
        policy_change_count=policy_change_count,
        duration_ms=duration_ms,
        checked_at=datetime.now(UTC),
    )
    db.add(sample)
