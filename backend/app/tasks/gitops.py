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

    from app.actions.registry import ensure_registry_current
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
            # The scheduled-actions importer checks each action key against
            # the registry; a pool process still on the built-ins alone
            # rejected every pack key (BUG-105). First, so the rebuild's
            # commit lands before the import's transaction begins.
            await ensure_registry_current(db)
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

            # SEC-27: an SSH clone records the server's host key on first
            # contact. Commit it now rather than riding on the import's
            # commit, so a failed import doesn't discard the pinning and
            # leave the next clone unverified again.
            await db.commit()

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

            # BUG-37: collect dispatches across all groups, commit once, then
            # dispatch. Per-group _trigger_group_sync stages SyncJob rows but
            # does NOT call .delay(); the caller drains this list after the
            # commit on line below.
            pending_dispatches: list[tuple[int, int, list[str] | None]] = []

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
                        pending_dispatches.extend(
                            await _trigger_group_sync(
                                group.id, db, changed_sync_modules(import_result)
                            )
                        )

                except Exception as e:
                    logger.error("GitOps: error processing group %s: %s", group.name, e)
                    group.gitops_status = GitOpsStatus.error
                    group.gitops_error_message = str(e)
                    await db.flush()

            repo.last_commit_sha = commit_sha
            repo.last_sync_at = datetime.now(UTC)
            await db.commit()

            from app.tasks.host_sync_orchestrator import run_host_sync  # noqa: PLC0415

            for job_id, host_id, module_filter in pending_dispatches:
                run_host_sync.delay(job_id=job_id, host_id=host_id, module_filter=module_filter)
                logger.info("GitOps: dispatched sync job %d for host %d", job_id, host_id)

            logger.info(
                "GitOps: processed webhook for repo %s, SHA %s, %d groups",
                repo.name,
                commit_sha[:8],
                len(groups),
            )

    finally:
        if repo_dir is not None:
            cleanup_repo(repo_dir)


#: GitOps importer module name → orchestrator module (CANONICAL_ORDER).
#: Modules that configure LabDog itself (drift, discovery, schedules) have
#: nothing to apply to a host.
_IMPORT_MODULE_TO_SYNC = {
    "firewall": "firewall",
    "services": "services",
    "packages": "packages",
    "hosts_entries": "hosts-file",
    "cron_jobs": "cron",
    "users": "linux-users",
    "resolver": "resolver",
}


def changed_sync_modules(import_result) -> list[str]:
    """The orchestrator modules an import changed, in canonical order."""
    from app.ansible_runtime.composer import CANONICAL_ORDER

    changed = {
        _IMPORT_MODULE_TO_SYNC[m.module]
        for m in import_result.modules
        if m.changed and m.module in _IMPORT_MODULE_TO_SYNC
    }
    return [m for m in CANONICAL_ORDER if m in changed]


async def _trigger_group_sync(
    group_id: int, db, modules: list[str]
) -> list[tuple[int, int, list[str] | None]]:
    """Stage a multi-module SyncJob for every host in *group_id*.

    Returns ``(job_id, host_id, module_filter)`` tuples for the caller to
    dispatch AFTER ``db.commit()``. BUG-37: dispatching ``.delay()`` before
    the commit races with the Celery worker — the row is invisible to the
    worker's connection until commit lands.

    BUG-135. This used to stage a row with no ``module_type`` (server
    default ``firewall``) and dispatch ``run_sync_playbook``, so a push
    that changed users, packages or cron was imported, reported as
    synced, and never applied. It also skipped any host with a job in
    flight, for good, and raised when a host had two. Now:

    * the row is ``module_type="bulk"`` with the changed modules as its
      filter, dispatched to ``run_host_sync`` like the bulk endpoint;
    * a busy host gets its row anyway: ``run_host_sync`` defers behind
      whatever is running and the host queue runs it afterwards;
    * when a bulk job is already queued for the host (``uq_sync_job_active``
      allows one), its filter is widened to include these modules instead.
      A bulk job already *running* may have read the configuration before
      this import, so it is followed by one per-module job per module.
    """
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    from app.models.host import HostGroupMembership
    from app.models.sync_job import JobStatus, SyncJob
    from app.tasks.host_sync_orchestrator import _DB_TO_CANONICAL

    if not modules:
        return []

    host_result = await db.execute(
        select(HostGroupMembership.c.host_id).where(HostGroupMembership.c.group_id == group_id)
    )
    host_ids = [row[0] for row in host_result.all()]

    if not host_ids:
        logger.info("GitOps: no hosts in group %d, skipping sync", group_id)
        return []

    canonical_to_db = {canonical: db_name for db_name, canonical in _DB_TO_CANONICAL.items()}

    async def _stage(host_id: int, module_type: str, module_filter: list[str] | None):
        """Insert one pending row; ``None`` when an active one of that type exists."""
        job = SyncJob(
            host_id=host_id,
            group_id=group_id,
            status=JobStatus.pending,
            module_type=module_type,
            module_filter=module_filter,
            # None distinguishes gitops-triggered from user-triggered.
            triggered_by_user_id=None,
        )
        try:
            async with db.begin_nested():
                db.add(job)
                await db.flush()
        except IntegrityError:
            return None
        return job.id

    pending: list[tuple[int, int, list[str] | None]] = []
    for host_id in host_ids:
        job_id = await _stage(host_id, "bulk", modules)
        if job_id is not None:
            pending.append((job_id, host_id, modules))
            continue

        existing = (
            await db.execute(
                select(SyncJob).where(
                    SyncJob.host_id == host_id,
                    SyncJob.module_type == "bulk",
                    SyncJob.status.in_([JobStatus.pending, JobStatus.running]),
                )
            )
        ).scalar_one_or_none()
        if existing is not None and existing.status == JobStatus.pending:
            if existing.module_filter:
                wanted = set(existing.module_filter) | set(modules)
                from app.ansible_runtime.composer import CANONICAL_ORDER

                existing.module_filter = [m for m in CANONICAL_ORDER if m in wanted]
            logger.info(
                "GitOps: host %d already has queued sync job %d; widened it to %s",
                host_id,
                existing.id,
                existing.module_filter or "every module",
            )
            continue

        # A bulk job is running and may predate this import.
        for module in modules:
            module_type = canonical_to_db.get(module)
            if module_type is None:
                continue
            job_id = await _stage(host_id, module_type, [module])
            if job_id is not None:
                pending.append((job_id, host_id, [module]))

    await db.flush()
    logger.info("GitOps: staged %d sync jobs for group %d", len(pending), group_id)
    return pending
