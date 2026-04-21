import logging

from app.tasks import celery_app

logger = logging.getLogger(__name__)


def _parse_os_release(content: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in content.splitlines():
        line = line.strip()
        if "=" not in line or line.startswith("#"):
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"')
    return out


@celery_app.task(name="app.tasks.facts.collect_host_facts", queue="long_running")
def collect_host_facts(host_id: int):
    """Collect OS facts from a host via SSH and persist them."""
    import asyncio
    from datetime import UTC, datetime

    import asyncssh
    from sqlalchemy import select

    from app.crypto.encryption import decrypt_ssh_key
    from app.crypto.key_management import get_master_key
    from app.db import task_session
    from app.models.host import Host
    from app.models.ssh_key import SSHKey
    from app.ssh_utils import ssh_connect

    async def _run():
        async with task_session() as db:
            result = await db.execute(select(Host).where(Host.id == host_id))
            host = result.scalar_one_or_none()
            if host is None:
                logger.warning("collect_host_facts: host %d not found", host_id)
                return

            if host.ssh_key_id is None:
                logger.info("collect_host_facts: host %d has no SSH key, skipping", host_id)
                return

            key_result = await db.execute(select(SSHKey).where(SSHKey.id == host.ssh_key_id))
            ssh_key = key_result.scalar_one_or_none()
            if ssh_key is None:
                logger.warning(
                    "collect_host_facts: SSH key %d not found for host %d",
                    host.ssh_key_id,
                    host_id,
                )
                return

            private_key_pem = decrypt_ssh_key(ssh_key.encrypted_private_key, get_master_key())
            imported_key = asyncssh.import_private_key(private_key_pem)

            try:
                async with ssh_connect(
                    host.ip_address,
                    port=host.ssh_port,
                    username=ssh_key.ssh_user,
                    client_keys=[imported_key],
                ) as conn:
                    result_ssh = await conn.run("cat /etc/os-release", check=False)
                    content = result_ssh.stdout or ""

                facts = _parse_os_release(content)
                host.os_codename = facts.get("VERSION_CODENAME", None)
                host.os_pretty_name = facts.get("PRETTY_NAME", None)
                host.os_facts_collected_at = datetime.now(UTC)
                await db.commit()
            except (asyncssh.Error, OSError, TimeoutError) as exc:
                logger.warning("collect_host_facts: SSH error for host %d: %s", host_id, exc)
                # Do NOT update os_facts_collected_at so next tab load retriggers

    asyncio.run(_run())
