import asyncio
import logging
from datetime import UTC, datetime

from app.tasks import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="gitops.process_webhook",
    max_retries=3,
    default_retry_delay=30,
)
def process_gitops_webhook(self, repo_id: int, commit_sha: str):
    """Celery task: clone repo, import YAML for each linked group, trigger sync."""
    asyncio.run(_process_webhook_async(self, repo_id, commit_sha))


async def _process_webhook_async(task, repo_id: int, commit_sha: str):
    from pathlib import Path

    from sqlalchemy import select

    from app.db import task_session
    from app.gitops.git_service import cleanup_repo, clone_repo, read_file_at_sha
    from app.gitops.importer import import_global_from_yaml, import_group_from_yaml
    from app.models.git_repository import GitAuthType, GitOpsStatus, GitRepository
    from app.models.host_group import HostGroup
    from app.models.ssh_key import SSHKey

    # Convention: the optional global YAML lives at the repo root.
    # Operators who want a different layout can symlink to it, but the
    # path itself is not user-configurable — keeps Phase 2 small.
    GLOBAL_YAML_PATH = "_global.yaml"

    repo_dir: Path | None = None

    try:
        async with task_session() as db:
            result = await db.execute(select(GitRepository).where(GitRepository.id == repo_id))
            repo = result.scalar_one_or_none()
            if not repo:
                logger.error("GitOps: repo %d not found", repo_id)
                return

            if repo.last_commit_sha == commit_sha:
                logger.info(
                    "GitOps: SHA %s already processed for repo %s, skipping",
                    commit_sha[:8],
                    repo.name,
                )
                return

            encrypted_ssh_key = None
            if repo.auth_type == GitAuthType.ssh_key and repo.ssh_key_id:
                key_result = await db.execute(select(SSHKey).where(SSHKey.id == repo.ssh_key_id))
                ssh_key = key_result.scalar_one_or_none()
                if ssh_key:
                    encrypted_ssh_key = ssh_key.encrypted_private_key

            try:
                _git_repo, repo_dir = clone_repo(repo, encrypted_ssh_key=encrypted_ssh_key)
            except Exception as e:
                logger.error("GitOps: failed to clone repo %s: %s", repo.name, e)
                raise task.retry(exc=e)

            # Global YAML is optional; missing file is the common case.
            # Failures here are logged but never abort the per-group loop —
            # an operator typo in `_global.yaml` shouldn't block per-group
            # rules from importing.
            try:
                global_yaml_content = read_file_at_sha(repo_dir, GLOBAL_YAML_PATH, commit_sha)
            except FileNotFoundError:
                global_yaml_content = None

            if global_yaml_content is not None:
                try:
                    global_result = await import_global_from_yaml(
                        repo_id=repo_id,
                        yaml_content=global_yaml_content,
                        commit_sha=commit_sha,
                        db=db,
                    )
                    if not global_result.success:
                        logger.warning(
                            "GitOps: global import failed for repo %s: %s",
                            repo.name,
                            global_result.error_message,
                        )
                    else:
                        logger.info(
                            "GitOps: global import OK for repo %s (changes=%s)",
                            repo.name,
                            global_result.any_changes(),
                        )
                except Exception as e:
                    logger.error(
                        "GitOps: error importing _global.yaml for repo %s: %s",
                        repo.name,
                        e,
                    )

            groups_result = await db.execute(
                select(HostGroup).where(
                    HostGroup.git_repository_id == repo_id,
                    HostGroup.gitops_enabled == True,  # noqa: E712
                )
            )
            groups = groups_result.scalars().all()

            if not groups:
                logger.info("GitOps: no gitops-enabled groups for repo %s", repo.name)
                repo.last_commit_sha = commit_sha
                repo.last_sync_at = datetime.now(UTC)
                await db.commit()
                return

            for group in groups:
                try:
                    try:
                        yaml_content = read_file_at_sha(
                            repo_dir, group.gitops_file_path, commit_sha
                        )
                    except FileNotFoundError:
                        group.gitops_status = GitOpsStatus.error
                        group.gitops_error_message = f"File not found: {group.gitops_file_path}"
                        await db.flush()
                        logger.warning(
                            "GitOps: file %s not found for group %s",
                            group.gitops_file_path,
                            group.name,
                        )
                        continue

                    import_result = await import_group_from_yaml(
                        group_id=group.id,
                        yaml_content=yaml_content,
                        commit_sha=commit_sha,
                        db=db,
                    )

                    if not import_result.success:
                        logger.warning(
                            "GitOps: import failed for group %s: %s",
                            group.name,
                            import_result.error_message,
                        )
                        continue

                    if import_result.any_changes():
                        await _trigger_group_sync(group.id, db)

                except Exception as e:
                    logger.error("GitOps: error processing group %s: %s", group.name, e)
                    group.gitops_status = GitOpsStatus.error
                    group.gitops_error_message = str(e)
                    await db.flush()

            repo.last_commit_sha = commit_sha
            repo.last_sync_at = datetime.now(UTC)
            await db.commit()

            logger.info(
                "GitOps: processed webhook for repo %s, SHA %s, %d groups",
                repo.name,
                commit_sha[:8],
                len(groups),
            )

    finally:
        if repo_dir is not None:
            cleanup_repo(repo_dir)


async def _trigger_group_sync(group_id: int, db):
    from sqlalchemy import select

    from app.models.host import HostGroupMembership
    from app.models.sync_job import SyncJob
    from app.tasks.sync import run_sync_playbook

    host_result = await db.execute(
        select(HostGroupMembership.c.host_id).where(HostGroupMembership.c.group_id == group_id)
    )
    host_ids = [row[0] for row in host_result.all()]

    if not host_ids:
        logger.info("GitOps: no hosts in group %d, skipping sync", group_id)
        return

    dispatched = 0
    for host_id in host_ids:
        running = await db.execute(
            select(SyncJob).where(
                SyncJob.host_id == host_id,
                SyncJob.status.in_(["pending", "running"]),
            )
        )
        if running.scalar_one_or_none():
            logger.info("GitOps: skipping host %d (sync already in progress)", host_id)
            continue

        # triggered_by_user_id=None distinguishes gitops-triggered from user-triggered
        job = SyncJob(
            host_id=host_id,
            group_id=group_id,
            status="pending",
            triggered_by_user_id=None,
        )
        db.add(job)
        await db.flush()

        run_sync_playbook.delay(job_id=job.id, host_id=host_id)
        dispatched += 1
        logger.info("GitOps: dispatched sync job %d for host %d", job.id, host_id)

    logger.info("GitOps: dispatched %d sync tasks for group %d", dispatched, group_id)
