from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.host import HostGroupMembership
from app.models.host_group import HostGroup
from app.user_mgmt.models import LinuxUser, LinuxGroup
from app.user_mgmt.schemas import EffectiveLinuxUserResponse, EffectiveLinuxGroupResponse


async def get_effective_users(
    host_id: int, db: AsyncSession
) -> list[EffectiveLinuxUserResponse]:
    """Merge group-level LinuxUser rules + host-level overrides.

    Merge key: username. Higher priority group wins. Host override = full replacement.
    """
    memberships = await db.execute(
        select(
            HostGroupMembership.c.group_id,
            HostGroup.name,
            HostGroup.priority,
        )
        .join(HostGroup, HostGroup.id == HostGroupMembership.c.group_id)
        .where(HostGroupMembership.c.host_id == host_id)
        .order_by(HostGroup.priority.desc())
    )
    groups = memberships.all()

    merged: dict[str, EffectiveLinuxUserResponse] = {}

    for group_id, group_name, _priority in groups:
        result = await db.execute(
            select(LinuxUser).where(LinuxUser.group_id == group_id)
        )
        for rule in result.scalars().all():
            if rule.username not in merged:
                merged[rule.username] = EffectiveLinuxUserResponse(
                    username=rule.username,
                    uid=rule.uid,
                    shell=rule.shell,
                    home_dir=rule.home_dir,
                    state=rule.state.value if hasattr(rule.state, "value") else str(rule.state),
                    comment=rule.comment,
                    sudo_rule=rule.sudo_rule,
                    authorized_keys=rule.authorized_keys or [],
                    supplementary_groups=rule.supplementary_groups or [],
                    source="group",
                    source_id=group_id,
                    source_name=group_name,
                )

    host_result = await db.execute(
        select(LinuxUser).where(LinuxUser.host_id == host_id)
    )
    for rule in host_result.scalars().all():
        merged[rule.username] = EffectiveLinuxUserResponse(
            username=rule.username,
            uid=rule.uid,
            shell=rule.shell,
            home_dir=rule.home_dir,
            state=rule.state.value if hasattr(rule.state, "value") else str(rule.state),
            comment=rule.comment,
            sudo_rule=rule.sudo_rule,
            authorized_keys=rule.authorized_keys or [],
            supplementary_groups=rule.supplementary_groups or [],
            source="host",
            source_id=host_id,
            source_name="host override",
        )

    return sorted(merged.values(), key=lambda u: u.username)


async def get_effective_groups(
    host_id: int, db: AsyncSession
) -> list[EffectiveLinuxGroupResponse]:
    """Merge group-level LinuxGroup rules + host-level overrides.

    Merge key: groupname. Higher priority group wins. Host override = full replacement.
    """
    memberships = await db.execute(
        select(
            HostGroupMembership.c.group_id,
            HostGroup.name,
            HostGroup.priority,
        )
        .join(HostGroup, HostGroup.id == HostGroupMembership.c.group_id)
        .where(HostGroupMembership.c.host_id == host_id)
        .order_by(HostGroup.priority.desc())
    )
    groups = memberships.all()

    merged: dict[str, EffectiveLinuxGroupResponse] = {}

    for group_id, group_name, _priority in groups:
        result = await db.execute(
            select(LinuxGroup).where(LinuxGroup.group_id == group_id)
        )
        for rule in result.scalars().all():
            if rule.groupname not in merged:
                merged[rule.groupname] = EffectiveLinuxGroupResponse(
                    groupname=rule.groupname,
                    gid=rule.gid,
                    state=rule.state.value if hasattr(rule.state, "value") else str(rule.state),
                    source="group",
                    source_id=group_id,
                    source_name=group_name,
                )

    host_result = await db.execute(
        select(LinuxGroup).where(LinuxGroup.host_id == host_id)
    )
    for rule in host_result.scalars().all():
        merged[rule.groupname] = EffectiveLinuxGroupResponse(
            groupname=rule.groupname,
            gid=rule.gid,
            state=rule.state.value if hasattr(rule.state, "value") else str(rule.state),
            source="host",
            source_id=host_id,
            source_name="host override",
        )

    return sorted(merged.values(), key=lambda g: g.groupname)
