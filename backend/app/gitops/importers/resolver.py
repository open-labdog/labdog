"""DNS Resolver module GitOps import handler (singleton shape)."""

import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.logger import log_action
from app.gitops.importers.firewall import ModuleImportResult
from app.gitops.schema import BarricadeGroupYAML
from app.models.host_group import HostGroup
from app.resolver.models import ResolverConfig, ResolverType
from app.resolver.schemas import ResolverConfigCreate

logger = logging.getLogger(__name__)


def _config_snapshot(config: ResolverConfig) -> dict:
    """Return a plain-dict snapshot of a ``ResolverConfig`` for audit trails."""
    return {
        "nameservers": list(config.nameservers),
        "search_domains": list(config.search_domains),
        "options": dict(config.options),
        "resolver_type": str(config.resolver_type),
        "dns_over_tls": config.dns_over_tls,
    }


def _configs_equal(config: ResolverConfig, desired: ResolverConfigCreate) -> bool:
    """Return ``True`` when *config* matches *desired* with no meaningful diff.

    List order for ``nameservers`` and ``search_domains`` is significant (order
    matters in ``/etc/resolv.conf``).  ``options`` dict key order is not
    meaningful, so items are compared as sorted tuples.
    """
    return (
        list(config.nameservers) == desired.nameservers
        and list(config.search_domains) == desired.search_domains
        and sorted(config.options.items()) == sorted(desired.options.items())
        and str(config.resolver_type) == desired.resolver_type
        and config.dns_over_tls == desired.dns_over_tls
    )


async def import_resolver(
    group: HostGroup,
    parsed: BarricadeGroupYAML,
    commit_sha: str,
    db: AsyncSession,
) -> ModuleImportResult:
    """Import DNS resolver config from *parsed* YAML into *group*.

    Implements **leave-alone** singleton semantics: if ``parsed.resolver`` is
    ``None`` (key absent or explicitly ``null``), the current DB state is left
    completely untouched and no audit event is emitted.

    Only when a non-null ``resolver:`` section is present does this handler
    compare the YAML against the existing row and upsert on difference.

    Does **not** touch ``group.gitops_status`` — that is the dispatcher's
    responsibility.

    Args:
        group: The target ``HostGroup`` ORM instance.
        parsed: Validated ``BarricadeGroupYAML`` from the current commit.
        commit_sha: Full commit SHA string (for audit trail).
        db: Active async database session.

    Returns:
        A :class:`ModuleImportResult` describing what changed (or the error).
    """
    group_id = group.id

    # --- Leave-alone semantics for missing / null section ---
    if parsed.resolver is None:
        logger.debug(
            "Group %d: resolver section absent/null — leaving DB state alone", group_id
        )
        return ModuleImportResult(
            module="resolver",
            added=0,
            removed=0,
            unchanged=0,
            changed=False,
        )

    # Run the YAML through ResolverConfigCreate so the dns_over_tls model
    # validator silently normalises dns_over_tls=False for non-systemd_resolved.
    try:
        desired = ResolverConfigCreate.model_validate(parsed.resolver.model_dump())
    except Exception as exc:
        return ModuleImportResult(
            module="resolver",
            error_message=f"Resolver config validation error: {exc}",
        )

    # Fetch existing group-scoped row.
    result = await db.execute(
        select(ResolverConfig).where(ResolverConfig.group_id == group_id)
    )
    existing: ResolverConfig | None = result.scalar_one_or_none()

    if existing is not None and _configs_equal(existing, desired):
        # Identical — nothing to do.
        logger.info(
            "GitOps resolver import for group %d: unchanged (SHA: %s)",
            group_id,
            commit_sha[:8],
        )
        return ModuleImportResult(
            module="resolver",
            added=0,
            removed=0,
            unchanged=1,
            changed=False,
        )

    # Capture before-state for audit.
    before_state: dict | None = None
    added = 0
    removed = 0

    if existing is not None:
        before_state = {"resolver": _config_snapshot(existing)}
        removed = 1
        # Delete-and-recreate to sidestep unique-index conflicts on in-place
        # update.  A simple attribute update also works but delete+insert is
        # explicit and safe given the partial unique index.
        await db.execute(
            delete(ResolverConfig).where(ResolverConfig.group_id == group_id)
        )
        await db.flush()

    new_config = ResolverConfig(
        group_id=group_id,
        nameservers=desired.nameservers,
        search_domains=desired.search_domains,
        options=desired.options,
        resolver_type=ResolverType(desired.resolver_type),
        dns_over_tls=desired.dns_over_tls,
    )
    db.add(new_config)
    await db.flush()
    added = 1

    after_state = {
        "resolver": {
            "nameservers": desired.nameservers,
            "search_domains": desired.search_domains,
            "options": desired.options,
            "resolver_type": desired.resolver_type,
            "dns_over_tls": desired.dns_over_tls,
        },
        "commit_sha": commit_sha,
        "file_path": group.gitops_file_path,
    }

    await log_action(
        db=db,
        action="gitops.import.resolver",
        entity_type="group",
        entity_id=group_id,
        before_state=before_state,
        after_state=after_state,
    )

    logger.info(
        "GitOps resolver import for group %d: +%d -%d (SHA: %s)",
        group_id,
        added,
        removed,
        commit_sha[:8],
    )

    return ModuleImportResult(
        module="resolver",
        added=added,
        removed=removed,
        unchanged=0,
        changed=True,
    )
