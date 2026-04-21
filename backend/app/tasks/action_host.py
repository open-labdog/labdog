"""Per-host action executor Celery task.

Each ActionHostRun is processed independently by this task.  The
orchestrator (action_orchestrator.py) dispatches one instance per host
inside a batch, waits for the whole batch, then moves on.
"""

import asyncio
import logging
import os
import shutil
import tempfile
from datetime import UTC, datetime

from app.tasks import celery_app

logger = logging.getLogger(__name__)

MAX_OUTPUT_BYTES = 1_048_576  # 1 MB


# ---------------------------------------------------------------------------
# Celery task entry point
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    name="app.tasks.action_host.run_action_host",
    queue="long_running",
)
def run_action_host(self, action_run_id: int, host_run_id: int) -> dict:
    """Run ansible-runner for a single host within an action run.

    Args:
        action_run_id: ID of the parent ActionRun.
        host_run_id: ID of the ActionHostRun record to drive.

    Returns:
        A dict summarising the outcome, e.g.
        ``{"action_run_id": 1, "host_run_id": 2}``.
    """
    asyncio.run(_run_action_host_async(action_run_id, host_run_id))
    return {"action_run_id": action_run_id, "host_run_id": host_run_id}


# ---------------------------------------------------------------------------
# Async implementation
# ---------------------------------------------------------------------------


async def _run_action_host_async(action_run_id: int, host_run_id: int) -> None:
    """Drive a single ActionHostRun through ansible-runner."""
    import json

    import redis as redis_lib
    from sqlalchemy import select

    from app.actions.registry import ACTION_REGISTRY
    from app.ansible.inventory import generate_inventory
    from app.ansible.runner import run_ansible
    from app.config import settings
    from app.crypto import decrypt_ssh_key, get_master_key
    from app.db import task_session
    from app.models.action_run import ActionHostRun, ActionRun
    from app.models.host import Host
    from app.models.ssh_key import SSHKey
    from app.settings_service import get_setting_sync_typed

    r = redis_lib.from_url(settings.redis.url)
    channel = f"actions.run.{action_run_id}"

    private_data_dir = tempfile.mkdtemp(prefix="barricade-action-")
    fd, ssh_key_path = tempfile.mkstemp(dir="/dev/shm", prefix="barricade-action-", suffix=".key")
    os.close(fd)

    try:
        # ------------------------------------------------------------------ #
        # Pre-flight: honour cancel token before doing any real work          #
        # ------------------------------------------------------------------ #
        if r.exists(f"actions.cancel.{action_run_id}"):
            async with task_session() as db:
                hr_result = await db.execute(
                    select(ActionHostRun).where(ActionHostRun.id == host_run_id)
                )
                hr = hr_result.scalar_one_or_none()
                if hr is not None and hr.status == "queued":
                    hr.status = "cancelled"
                    await db.commit()
            r.publish(
                channel,
                json.dumps(
                    {"event": "host_status", "host_run_id": host_run_id, "status": "cancelled"}
                ),
            )
            return

        # ------------------------------------------------------------------ #
        # Load all required data from DB in a single session                  #
        # ------------------------------------------------------------------ #
        async with task_session() as db:
            hr_result = await db.execute(
                select(ActionHostRun).where(ActionHostRun.id == host_run_id)
            )
            hr: ActionHostRun = hr_result.scalar_one()

            run_result = await db.execute(select(ActionRun).where(ActionRun.id == action_run_id))
            run: ActionRun = run_result.scalar_one()

            host_result = await db.execute(select(Host).where(Host.id == hr.host_id))
            host: Host = host_result.scalar_one()

            if host.ssh_key_id is None:
                hr.status = "skipped"
                hr.error_message = "Host has no SSH key configured"
                hr.finished_at = datetime.now(UTC)
                await db.commit()
                r.publish(
                    channel,
                    json.dumps(
                        {
                            "event": "host_status",
                            "host_run_id": host_run_id,
                            "status": "skipped",
                        }
                    ),
                )
                return

            key_result = await db.execute(select(SSHKey).where(SSHKey.id == host.ssh_key_id))
            ssh_key: SSHKey = key_result.scalar_one()

            master_key = get_master_key()
            private_key_text = decrypt_ssh_key(ssh_key.encrypted_private_key, master_key)

            action = ACTION_REGISTRY.get(run.action_key)
            if action is None:
                hr.status = "failed"
                hr.error_message = f"Action '{run.action_key}' not found in registry"
                hr.finished_at = datetime.now(UTC)
                await db.commit()
                r.publish(
                    channel,
                    json.dumps(
                        {
                            "event": "host_status",
                            "host_run_id": host_run_id,
                            "status": "failed",
                        }
                    ),
                )
                return

            # Mark running
            hr.status = "running"
            hr.started_at = datetime.now(UTC)
            await db.commit()

            # Cache scalar values before the session closes
            host_id: int = host.id
            host_ip: str = host.ip_address
            host_port: int = host.ssh_port or 22
            ssh_user: str = ssh_key.ssh_user or "root"
            parameters: dict = dict(run.parameters or {})
            playbook_path = action.playbook_path

        # ------------------------------------------------------------------ #
        # Write SSH key to tmpfs                                              #
        # ------------------------------------------------------------------ #
        with open(ssh_key_path, "w") as fh:
            fh.write(private_key_text)
            if not private_key_text.endswith("\n"):
                fh.write("\n")
        os.chmod(ssh_key_path, 0o600)

        # ------------------------------------------------------------------ #
        # Build inventory and extra vars                                      #
        # ------------------------------------------------------------------ #
        inventory_json = generate_inventory(host_ip, host_port, ssh_key_path, ssh_user=ssh_user)

        dry_run = parameters.pop("__dry_run", False)
        extra_vars: dict | None = dict(parameters) if parameters else None
        if dry_run:
            extra_vars = extra_vars or {}
            extra_vars["ansible_check_mode"] = True

        # ------------------------------------------------------------------ #
        # Resolve playbook timeout from app settings                          #
        # ------------------------------------------------------------------ #
        try:
            timeout = int(get_setting_sync_typed("ansible.playbook_timeout"))
        except Exception:
            timeout = 1800

        # ------------------------------------------------------------------ #
        # Run ansible-runner                                                  #
        # ------------------------------------------------------------------ #
        r.publish(
            channel,
            json.dumps(
                {
                    "event": "host_status",
                    "host_id": host_id,
                    "host_run_id": host_run_id,
                    "status": "running",
                }
            ),
        )

        runner = run_ansible(
            playbook_path=playbook_path,
            inventory_json=inventory_json,
            private_data_dir=private_data_dir,
            extra_vars=extra_vars,
            timeout=timeout,
        )

        # ------------------------------------------------------------------ #
        # Capture and truncate output                                         #
        # ------------------------------------------------------------------ #
        output: str = runner.stdout.read() if hasattr(runner.stdout, "read") else str(runner.stdout)
        if len(output.encode()) > MAX_OUTPUT_BYTES:
            output = output[:MAX_OUTPUT_BYTES] + "\n\n(truncated — output exceeded 1 MB)"

        success: bool = runner.status == "successful"
        exit_code: int = runner.rc

        # Publish last 4 KB of output to SSE; full text is persisted to DB
        r.publish(
            channel,
            json.dumps(
                {
                    "event": "output",
                    "host_run_id": host_run_id,
                    "text": output[-4000:],
                }
            ),
        )

        # ------------------------------------------------------------------ #
        # Persist result to DB                                                #
        # ------------------------------------------------------------------ #
        async with task_session() as db:
            hr_result = await db.execute(
                select(ActionHostRun).where(ActionHostRun.id == host_run_id)
            )
            hr = hr_result.scalar_one()
            hr.status = "succeeded" if success else "failed"
            hr.exit_code = exit_code
            hr.finished_at = datetime.now(UTC)
            hr.output = output
            if not success:
                hr.error_message = (
                    f"ansible-runner exited with status={runner.status}, rc={exit_code}"
                )
            final_status = hr.status
            await db.commit()

        r.publish(
            channel,
            json.dumps(
                {
                    "event": "host_status",
                    "host_run_id": host_run_id,
                    "status": final_status,
                }
            ),
        )

        logger.info(
            "action_host: action_run %d / host_run %d completed — %s",
            action_run_id,
            host_run_id,
            final_status,
        )

    except Exception as exc:
        error_msg = str(exc)
        logger.exception(
            "action_host: action_run %d / host_run %d failed",
            action_run_id,
            host_run_id,
        )
        try:
            from sqlalchemy import select

            from app.db import task_session
            from app.models.action_run import ActionHostRun

            async with task_session() as db:
                hr_result = await db.execute(
                    select(ActionHostRun).where(ActionHostRun.id == host_run_id)
                )
                hr = hr_result.scalar_one_or_none()
                if hr is not None:
                    hr.status = "failed"
                    hr.error_message = error_msg
                    hr.finished_at = datetime.now(UTC)
                    await db.commit()
        except Exception:
            logger.exception(
                "action_host: could not persist failure for host_run %d",
                host_run_id,
            )
        try:
            r.publish(
                channel,
                json.dumps(
                    {"event": "host_status", "host_run_id": host_run_id, "status": "failed"}
                ),
            )
        except Exception:
            pass
        raise

    finally:
        # CRITICAL: always remove the SSH key from tmpfs
        if os.path.exists(ssh_key_path):
            os.unlink(ssh_key_path)
        if os.path.exists(private_data_dir):
            shutil.rmtree(private_data_dir, ignore_errors=True)
