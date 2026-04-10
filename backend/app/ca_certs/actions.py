"""Helpers for enqueueing CA cert deploy actions.

Used by:
- ``api/groups.py`` when hosts are added to a group (auto-enqueue)
- ``api/hosts.py`` when a host's group memberships change (auto-enqueue)
- ``api/ca_cert_actions.py`` for explicit user-triggered runs

The result row is a ``SyncJob`` with ``module_type='ca_cert'`` — we
intentionally reuse the SyncJob table for action-run tracking, since
the schema (status, output, timestamps, triggered_by) is identical to
what we need.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ca_certs.models import CACertRule
from app.models.host import Host
from app.models.sync_job import SyncJob

CA_CERT_MODULE_TYPE = "ca_cert"


async def group_has_ca_certs(group_id: int, db: AsyncSession) -> bool:
    """Return True if the group has at least one CA cert rule defined."""
    result = await db.execute(
        select(CACertRule.id).where(CACertRule.group_id == group_id).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def host_has_running_ca_cert_action(
    host_id: int, db: AsyncSession
) -> bool:
    result = await db.execute(
        select(SyncJob.id).where(
            SyncJob.host_id == host_id,
            SyncJob.module_type == CA_CERT_MODULE_TYPE,
            SyncJob.status.in_(["pending", "running"]),
        ).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def enqueue_ca_cert_action_for_host(
    host_id: int,
    db: AsyncSession,
    *,
    triggered_by_user_id: int | None = None,
    group_id: int | None = None,
) -> SyncJob | None:
    """Create a SyncJob row for a CA cert deploy and dispatch the Celery task.

    Returns the created SyncJob, or ``None`` if the action could not be
    enqueued (host missing SSH key, or another action already running).
    The caller is responsible for committing the surrounding transaction.
    """
    host = (await db.execute(
        select(Host).where(Host.id == host_id)
    )).scalar_one_or_none()
    if not host or not host.ssh_key_id:
        return None

    if await host_has_running_ca_cert_action(host_id, db):
        return None

    job = SyncJob(
        host_id=host_id,
        group_id=group_id,
        module_type=CA_CERT_MODULE_TYPE,
        status="pending",
        triggered_by_user_id=triggered_by_user_id,
    )
    db.add(job)
    await db.flush()

    # Dispatch after flush so the task sees a valid job_id.
    from app.tasks.ca_cert_action import run_ca_cert_action
    run_ca_cert_action.delay(job_id=job.id, host_id=host_id)

    return job


async def auto_enqueue_for_new_membership(
    host_id: int,
    group_id: int,
    db: AsyncSession,
    *,
    triggered_by_user_id: int | None = None,
) -> SyncJob | None:
    """Auto-enqueue a CA cert action when a host joins a group.

    Only fires if the group has at least one CA cert rule, the host has
    an SSH key, and no other CA cert action is already running for this
    host. Designed to be called from group-membership mutation endpoints.
    """
    if not await group_has_ca_certs(group_id, db):
        return None
    return await enqueue_ca_cert_action_for_host(
        host_id,
        db,
        triggered_by_user_id=triggered_by_user_id,
        group_id=group_id,
    )
