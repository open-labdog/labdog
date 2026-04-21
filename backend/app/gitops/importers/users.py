"""Users module GitOps import handler.

Handles both ``LinuxGroup`` (linux_groups) and ``LinuxUser`` (users) in a
single call so they share one audit event and one :class:`ModuleImportResult`.

Validation strategy
-------------------
YAML schema models (``LinuxGroupYAML``, ``LinuxUserYAML``) are intentionally
minimal — they carry the structural shape from the YAML file.  Full validation
(protected names, UID/GID ranges, SSH key prefixes, sudo_rule metacharacters)
is delegated via a ``*Create.model_validate()`` round-trip before any DB write.
This avoids duplicating the validator logic that already lives in
``app.user_mgmt.schemas``.

Import order
------------
Linux groups are processed FIRST so that any ``supplementary_groups`` reference
in a user can resolve against newly-created DB rows.  A cross-reference warning
is emitted (not an error) when a user's supplementary group name is absent from
both the YAML and the current DB.
"""

import logging

from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.logger import log_action
from app.gitops.importers.firewall import ModuleImportResult
from app.gitops.schema import BarricadeGroupYAML, LinuxGroupYAML, LinuxUserYAML
from app.models.host_group import HostGroup
from app.user_mgmt.models import LinuxGroup, LinuxUser, UserState
from app.user_mgmt.schemas import LinuxGroupCreate, LinuxUserCreate

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Diff helpers — linux groups
# ---------------------------------------------------------------------------


def _group_tuple(
    groupname: str,
    gid: int | None,
    state: str,
    priority: int,
) -> tuple:
    """Return a comparable tuple of linux-group fields for diffing."""
    return (groupname, gid, state, priority)


def _group_orm_to_tuple(row: LinuxGroup) -> tuple:
    """Extract a comparable tuple from a ``LinuxGroup`` ORM instance."""
    return _group_tuple(
        groupname=row.groupname,
        gid=row.gid,
        state=str(row.state),
        priority=row.priority,
    )


def _yaml_to_group_tuple(entry: LinuxGroupYAML) -> tuple:
    """Extract a comparable tuple from a ``LinuxGroupYAML`` schema instance."""
    return _group_tuple(
        groupname=entry.groupname,
        gid=entry.gid,
        state=entry.state,
        priority=entry.priority,
    )


# ---------------------------------------------------------------------------
# Diff helpers — linux users
# ---------------------------------------------------------------------------


def _user_tuple(
    username: str,
    uid: int | None,
    shell: str,
    home_dir: str | None,
    state: str,
    comment: str | None,
    sudo_rule: str | None,
    authorized_keys_sorted: tuple,
    supplementary_groups_sorted: tuple,
    priority: int,
) -> tuple:
    """Return a comparable tuple of linux-user fields for diffing."""
    return (
        username,
        uid,
        shell,
        home_dir,
        state,
        comment,
        sudo_rule,
        authorized_keys_sorted,
        supplementary_groups_sorted,
        priority,
    )


def _user_orm_to_tuple(row: LinuxUser) -> tuple:
    """Extract a comparable tuple from a ``LinuxUser`` ORM instance.

    ``authorized_keys`` and ``supplementary_groups`` are sorted for comparison
    so that list-reorder is not treated as drift.  The user-provided order in
    the DB is preserved for writes.
    """
    return _user_tuple(
        username=row.username,
        uid=row.uid,
        shell=row.shell,
        home_dir=row.home_dir,
        state=str(row.state),
        comment=row.comment,
        sudo_rule=row.sudo_rule,
        authorized_keys_sorted=tuple(sorted(row.authorized_keys or [])),
        supplementary_groups_sorted=tuple(sorted(row.supplementary_groups or [])),
        priority=row.priority,
    )


def _yaml_to_user_tuple(entry: LinuxUserYAML) -> tuple:
    """Extract a comparable tuple from a ``LinuxUserYAML`` schema instance.

    Sorts copies of list fields for comparison only; the original order is
    preserved in the YAML entry for DB insert.
    """
    return _user_tuple(
        username=entry.username,
        uid=entry.uid,
        shell=entry.shell,
        home_dir=entry.home_dir,
        state=entry.state,
        comment=entry.comment,
        sudo_rule=entry.sudo_rule,
        authorized_keys_sorted=tuple(sorted(entry.authorized_keys)),
        supplementary_groups_sorted=tuple(sorted(entry.supplementary_groups)),
        priority=entry.priority,
    )


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------


async def import_users(
    group: HostGroup,
    parsed: BarricadeGroupYAML,
    commit_sha: str,
    db: AsyncSession,
) -> ModuleImportResult:
    """Import Linux groups and users from *parsed* YAML into *group*.

    Processes ``parsed.linux_groups`` first (groups before users) then
    ``parsed.users``, emitting a single ``gitops.import.users`` audit event.

    Validation is delegated to ``LinuxGroupCreate``/``LinuxUserCreate`` via
    ``.model_validate()`` round-trip so protected-name checks, UID/GID range
    checks, SSH key prefix checks, and sudo_rule metacharacter checks are all
    applied without duplicating logic here.

    Missing or ``None`` sections and empty lists both trigger wipe semantics —
    all existing group-scoped rows for that table are deleted.

    Does **not** touch ``group.gitops_status`` — that is the dispatcher's
    responsibility.

    Args:
        group: The target ``HostGroup`` ORM instance.
        parsed: Validated ``BarricadeGroupYAML`` from the current commit.
        commit_sha: Full commit SHA string (for audit trail).
        db: Active async database session.

    Returns:
        A :class:`ModuleImportResult` with ``module="users"`` and counts
        summed across both tables.
    """
    group_id = group.id

    # ------------------------------------------------------------------
    # 1. Build desired linux-group entries (validate via LinuxGroupCreate)
    # ------------------------------------------------------------------
    desired_linux_groups: list[LinuxGroupYAML] = []

    if parsed.linux_groups is None:
        logger.warning("Group %d: YAML has no linux_groups section — wiping linux groups", group_id)
    elif not parsed.linux_groups:
        logger.warning("Group %d: YAML has empty linux_groups list — wiping linux groups", group_id)

    for entry in parsed.linux_groups or []:
        try:
            LinuxGroupCreate.model_validate(entry.model_dump())
        except ValidationError as exc:
            return ModuleImportResult(
                module="users",
                error_message=(f"Invalid linux_group '{entry.groupname}': {exc}"),
            )
        desired_linux_groups.append(entry)

    # ------------------------------------------------------------------
    # 2. Build desired linux-user entries (validate via LinuxUserCreate)
    # ------------------------------------------------------------------
    desired_users: list[LinuxUserYAML] = []

    if parsed.users is None:
        logger.warning("Group %d: YAML has no users section — wiping linux users", group_id)
    elif not parsed.users:
        logger.warning("Group %d: YAML has empty users list — wiping linux users", group_id)

    # Build the set of group names defined in the YAML (for cross-ref check).
    yaml_group_names: set[str] = {g.groupname for g in desired_linux_groups}

    # Fetch current DB linux_groups for this host_group (for cross-ref check).
    db_linux_groups_result = await db.execute(
        select(LinuxGroup).where(LinuxGroup.group_id == group_id)
    )
    existing_db_group_names: set[str] = {
        row.groupname for row in db_linux_groups_result.scalars().all()
    }

    for entry in parsed.users or []:
        try:
            LinuxUserCreate.model_validate(entry.model_dump())
        except ValidationError as exc:
            return ModuleImportResult(
                module="users",
                error_message=(f"Invalid user '{entry.username}': {exc}"),
            )

        # Cross-reference check: warn when supplementary_groups references a
        # group not present in YAML or in the current DB.  This is a warning
        # only — the group may pre-exist on the host outside Barricade's control.
        for sg_name in entry.supplementary_groups:
            if sg_name not in yaml_group_names and sg_name not in existing_db_group_names:
                logger.warning(
                    "Group %d: user '%s' references supplementary group '%s' "
                    "which is not in the YAML linux_groups section and not in "
                    "the current DB — the group may pre-exist on the target host",
                    group_id,
                    entry.username,
                    sg_name,
                )

        desired_users.append(entry)

    # ------------------------------------------------------------------
    # 3. Diff linux groups
    # ------------------------------------------------------------------
    current_lg_result = await db.execute(select(LinuxGroup).where(LinuxGroup.group_id == group_id))
    current_linux_groups: list[LinuxGroup] = list(current_lg_result.scalars().all())

    current_lg_tuples = {_group_orm_to_tuple(r) for r in current_linux_groups}
    desired_lg_tuples = {_yaml_to_group_tuple(e) for e in desired_linux_groups}

    lg_added = len(desired_lg_tuples - current_lg_tuples)
    lg_removed = len(current_lg_tuples - desired_lg_tuples)
    lg_unchanged = len(current_lg_tuples & desired_lg_tuples)
    lg_changed = bool(lg_added or lg_removed)

    # ------------------------------------------------------------------
    # 4. Diff linux users
    # ------------------------------------------------------------------
    current_lu_result = await db.execute(select(LinuxUser).where(LinuxUser.group_id == group_id))
    current_linux_users: list[LinuxUser] = list(current_lu_result.scalars().all())

    current_lu_tuples = {_user_orm_to_tuple(r) for r in current_linux_users}
    desired_lu_tuples = {_yaml_to_user_tuple(e) for e in desired_users}

    lu_added = len(desired_lu_tuples - current_lu_tuples)
    lu_removed = len(current_lu_tuples - desired_lu_tuples)
    lu_unchanged = len(current_lu_tuples & desired_lu_tuples)
    lu_changed = bool(lu_added or lu_removed)

    has_changes = lg_changed or lu_changed

    module_result = ModuleImportResult(
        module="users",
        added=lg_added + lu_added,
        removed=lg_removed + lu_removed,
        unchanged=lg_unchanged + lu_unchanged,
        changed=has_changes,
    )

    if has_changes:
        # Capture before-state for audit.
        before_state = {
            "linux_groups": [
                {
                    "groupname": r.groupname,
                    "gid": r.gid,
                    "state": str(r.state),
                    "priority": r.priority,
                }
                for r in current_linux_groups
            ],
            "users": [
                {
                    "username": r.username,
                    "uid": r.uid,
                    "shell": r.shell,
                    "home_dir": r.home_dir,
                    "state": str(r.state),
                    "comment": r.comment,
                    "sudo_rule": r.sudo_rule,
                    "authorized_keys": list(r.authorized_keys or []),
                    "supplementary_groups": list(r.supplementary_groups or []),
                    "priority": r.priority,
                }
                for r in current_linux_users
            ],
        }

        # Delete-and-replace groups first (groups before users).
        if lg_changed:
            await db.execute(delete(LinuxGroup).where(LinuxGroup.group_id == group_id))
            for entry in desired_linux_groups:
                row = LinuxGroup(
                    group_id=group_id,
                    groupname=entry.groupname,
                    gid=entry.gid,
                    state=UserState(entry.state),
                    priority=entry.priority,
                )
                db.add(row)

        # Delete-and-replace users.
        if lu_changed:
            await db.execute(delete(LinuxUser).where(LinuxUser.group_id == group_id))
            for entry in desired_users:
                row = LinuxUser(
                    group_id=group_id,
                    username=entry.username,
                    uid=entry.uid,
                    shell=entry.shell,
                    home_dir=entry.home_dir,
                    state=UserState(entry.state),
                    comment=entry.comment,
                    sudo_rule=entry.sudo_rule,
                    # Preserve user-provided list order in DB; sorted copies
                    # were used only for diff comparison above.
                    authorized_keys=list(entry.authorized_keys),
                    supplementary_groups=list(entry.supplementary_groups),
                    priority=entry.priority,
                )
                db.add(row)

        after_state = {
            "linux_groups": [
                {
                    "groupname": e.groupname,
                    "gid": e.gid,
                    "state": e.state,
                    "priority": e.priority,
                }
                for e in desired_linux_groups
            ],
            "users": [
                {
                    "username": e.username,
                    "uid": e.uid,
                    "shell": e.shell,
                    "home_dir": e.home_dir,
                    "state": e.state,
                    "comment": e.comment,
                    "sudo_rule": e.sudo_rule,
                    "authorized_keys": list(e.authorized_keys),
                    "supplementary_groups": list(e.supplementary_groups),
                    "priority": e.priority,
                }
                for e in desired_users
            ],
            "commit_sha": commit_sha,
            "file_path": group.gitops_file_path,
        }

        await log_action(
            db=db,
            action="gitops.import.users",
            entity_type="group",
            entity_id=group_id,
            before_state=before_state,
            after_state=after_state,
        )

    logger.info(
        "GitOps users import for group %d: +%d -%d =%d (SHA: %s)",
        group_id,
        module_result.added,
        module_result.removed,
        module_result.unchanged,
        commit_sha[:8],
    )

    return module_result
