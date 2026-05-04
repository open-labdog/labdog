"""Drift-interval setting GitOps import handler (singleton, leave-alone).

Imports the ``drift:`` block from ``_global.yaml`` into the
``app_settings`` row keyed ``drift.check_interval_minutes``. Singleton
shape with **leave-alone** semantics — same pattern as
:mod:`app.gitops.importers.resolver` and :mod:`app.gitops.importers.workflow`:

* ``drift:`` absent or ``null`` ⇒ DB row left untouched, no audit emitted.
* ``drift:`` present ⇒ upsert on diff, idempotent on identical re-imports.

This handler does NOT call ``settings_service.update_setting`` — that
function commits eagerly, which conflicts with the global dispatcher's
single-transaction semantics. It writes to ``AppSetting`` directly and
explicitly invalidates the in-process cache so the very next read (even
in the same request) sees the new value.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.logger import log_action
from app.gitops.importers.firewall import ModuleImportResult
from app.gitops.schema import LabDogGlobalYAML
from app.models.app_setting import AppSetting
from app.settings_service import SETTING_DEFINITIONS, get_default, invalidate_cache

logger = logging.getLogger(__name__)

_KEY = "drift.check_interval_minutes"


async def import_drift(
    parsed: LabDogGlobalYAML,
    commit_sha: str,
    db: AsyncSession,
) -> ModuleImportResult:
    """Import the global drift-check interval from ``_global.yaml``.

    Args:
        parsed: Validated ``LabDogGlobalYAML`` payload.
        commit_sha: Full commit SHA string (for audit trail).
        db: Active async session — caller owns the transaction.

    Returns:
        :class:`ModuleImportResult` with module name ``"drift"``.
    """
    if parsed.drift is None:
        logger.debug("drift section absent/null — leaving DB state alone")
        return ModuleImportResult(module="drift", changed=False)

    desired_int = parsed.drift.check_interval_minutes
    desired_str = str(desired_int)

    result = await db.execute(select(AppSetting).where(AppSetting.key == _KEY))
    existing: AppSetting | None = result.scalar_one_or_none()

    current_str = existing.value if existing else get_default(_KEY)

    if current_str == desired_str:
        logger.info(
            "GitOps drift import: unchanged (interval=%s, SHA: %s)",
            desired_int,
            commit_sha[:8],
        )
        return ModuleImportResult(module="drift", unchanged=1, changed=False)

    before_state: dict | None = None
    if existing is not None:
        before_state = {"check_interval_minutes": int(current_str)}
        existing.value = desired_str
        existing.updated_by = None  # System: GitOps import
        added = 0
    else:
        defn = SETTING_DEFINITIONS[_KEY]
        new_setting = AppSetting(
            key=_KEY,
            value=desired_str,
            value_type=defn["type"],
            description=defn["description"],
            updated_by=None,
        )
        db.add(new_setting)
        added = 1

    await db.flush()
    invalidate_cache(_KEY)

    await log_action(
        db=db,
        action="gitops.import.drift",
        entity_type="app_setting",
        entity_id=existing.id if existing else None,
        before_state=before_state,
        after_state={
            "check_interval_minutes": desired_int,
            "commit_sha": commit_sha,
        },
    )

    logger.info(
        "GitOps drift import: %s → %s (SHA: %s)",
        current_str,
        desired_str,
        commit_sha[:8],
    )

    return ModuleImportResult(
        module="drift",
        added=added,
        removed=0,
        unchanged=0,
        changed=True,
    )
