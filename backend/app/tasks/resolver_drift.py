from app.tasks import celery_app


@celery_app.task(
    bind=True,
    name="app.tasks.resolver_drift.run_resolver_drift_check",
    queue="long_running",
)
def run_resolver_drift_check(self, host_id: int) -> dict:
    """Check DNS resolver drift on a single host via SSH."""
    import asyncio
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.crypto.encryption import decrypt_ssh_key
    from app.crypto.key_management import get_master_key
    from app.db import AsyncSessionLocal
    from app.models.host import Host
    from app.models.host_module_status import HostModuleStatus
    from app.models.ssh_key import SSHKey
    from app.resolver.collector import collect_resolver_state
    from app.resolver.diff import compute_resolver_diff
    from app.resolver.merge import get_effective_resolver

    async def _run():
        async with AsyncSessionLocal() as db:
            host = (
                await db.execute(select(Host).where(Host.id == host_id))
            ).scalar_one()
            effective = await get_effective_resolver(host_id, db)

            if not effective:
                return {"host_id": host_id, "status": "unmanaged", "has_drift": False}

            ssh_key = (
                await db.execute(
                    select(SSHKey).where(SSHKey.id == host.ssh_key_id)
                )
            ).scalar_one()
            master_key = get_master_key()
            private_key_pem = decrypt_ssh_key(
                ssh_key.encrypted_private_key, master_key
            )

            actual = await collect_resolver_state(
                host.ip_address,
                host.ssh_port,
                private_key_pem,
                effective.resolver_type,
            )

            desired = {
                "nameservers": effective.nameservers,
                "search_domains": effective.search_domains,
                "options": effective.options,
            }

            diff = compute_resolver_diff(actual, desired)

            hms = (
                await db.execute(
                    select(HostModuleStatus).where(
                        HostModuleStatus.host_id == host_id,
                        HostModuleStatus.module_type == "resolver",
                    )
                )
            ).scalar_one_or_none()
            if hms is None:
                hms = HostModuleStatus(
                    host_id=host_id, module_type="resolver"
                )
                db.add(hms)

            hms.sync_status = "in_sync" if not diff.has_changes else "out_of_sync"
            hms.last_drift_check_at = datetime.now(timezone.utc)
            await db.commit()

            return {
                "host_id": host_id,
                "has_drift": diff.has_changes,
                "nameservers_changed": diff.nameservers_changed,
                "search_domains_changed": diff.search_domains_changed,
                "options_changed": diff.options_changed,
                "current": diff.current,
                "desired": diff.desired,
            }

    return asyncio.run(_run())


@celery_app.task(
    name="app.tasks.resolver_drift.check_all_resolver_drift",
    queue="long_running",
)
def check_all_resolver_drift():
    """Periodic task: check resolver drift for all hosts with resolver drift enabled."""
    import asyncio
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.crypto.encryption import decrypt_ssh_key
    from app.crypto.key_management import get_master_key
    from app.db import AsyncSessionLocal
    from app.models.host import Host
    from app.models.host_module_status import HostModuleStatus
    from app.models.ssh_key import SSHKey
    from app.resolver.collector import collect_resolver_state
    from app.resolver.diff import compute_resolver_diff
    from app.resolver.merge import get_effective_resolver

    async def _run():
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(HostModuleStatus).where(
                    HostModuleStatus.module_type == "resolver",
                    HostModuleStatus.drift_check_enabled == True,  # noqa: E712
                )
            )
            statuses = result.scalars().all()

            for hms in statuses:
                try:
                    host_result = await db.execute(
                        select(Host).where(Host.id == hms.host_id)
                    )
                    host = host_result.scalar_one_or_none()
                    if not host or not host.ssh_key_id:
                        continue

                    key_result = await db.execute(
                        select(SSHKey).where(SSHKey.id == host.ssh_key_id)
                    )
                    ssh_key = key_result.scalar_one_or_none()
                    if not ssh_key:
                        continue

                    private_key_pem = decrypt_ssh_key(
                        ssh_key.encrypted_private_key, get_master_key()
                    )
                    effective = await get_effective_resolver(host.id, db)
                    if not effective:
                        continue

                    actual = await collect_resolver_state(
                        host.ip_address,
                        host.ssh_port,
                        private_key_pem,
                        effective.resolver_type,
                    )
                    desired = {
                        "nameservers": effective.nameservers,
                        "search_domains": effective.search_domains,
                        "options": effective.options,
                    }
                    diff = compute_resolver_diff(actual, desired)

                    hms.sync_status = (
                        "in_sync" if not diff.has_changes else "out_of_sync"
                    )
                    hms.last_drift_check_at = datetime.now(timezone.utc)
                except Exception:
                    hms.sync_status = "error"
                    hms.last_drift_check_at = datetime.now(timezone.utc)

            await db.commit()
            return len(statuses)

    count = asyncio.run(_run())
    return {"checked": count}


def _register_resolver_drift_schedule():
    from celery.schedules import schedule

    from redbeat import RedBeatSchedulerEntry

    from app.config import settings

    interval = schedule(run_every=settings.DRIFT_CHECK_INTERVAL_MINUTES * 60)
    entry = RedBeatSchedulerEntry(
        name="check-resolver-drift-periodic",
        task="app.tasks.resolver_drift.check_all_resolver_drift",
        schedule=interval,
        app=celery_app,
    )
    entry.save()


try:
    _register_resolver_drift_schedule()
except Exception:
    # Fallback: Redis may not be available at import time (e.g., during tests)
    pass
